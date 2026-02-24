import cv2
import numpy as np
import ezdxf
import os
import csv
from datetime import datetime
import tkinter as tk
from tkinter import filedialog

class VisionInspector:
    def __init__(self, dxf_path=""):
        self.dxf_path = dxf_path
        
        # [핵심] DirectShow 모드로 USB 카메라 자동 탐색
        self.cap = self.auto_find_camera()
        if self.cap is None:
            print("\n❌ [Error] 연결된 USB 카메라를 찾을 수 없거나 접근이 차단되었습니다.")
            print("1. 카메라가 다른 프로그램(기본 카메라 앱 등)에서 켜져 있다면 꺼주세요.")
            print("2. USB 선을 뺐다가 다시 꽂아보세요.\n")
            exit()
            
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        
        # 카메라 렌즈가 열릴 때까지 잠시 대기하며 프레임 확보
        cv2.waitKey(500)
        ret, frame = self.cap.read()
        if not ret or frame is None:
            print("\n❌ [Error] 카메라는 연결되었으나, 화면 데이터를 받을 수 없습니다.\n")
            exit()
            
        self.cam_h, self.cam_w = frame.shape[:2]
        self.view_w = 1200
        self.view_h = int(self.cam_h * (self.view_w / self.cam_w))
        self.ui_w = 200
        self.total_w = self.view_w + self.ui_w
        
        self.modes = ['LOAD_DXF', 'PAN', 'ZOOM', 'ROTATE', 'MEASURE', 'CALIB', 'SAVE_IMG', 'CLEAR', 'QUIT']
        self.current_mode = 'PAN'
        self.buttons = {}
        self.init_buttons()

        self.dxf_contours, self.dxf_real_width = self.load_dxf(dxf_path)
        self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
        self.scale = (self.cam_w * 0.75) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
        self.angle = 0.0
        self.base_px_per_mm = None
        
        self.measurements = []
        self.calib_p1, self.calib_p2 = None, None
        self.need_calib_input = False 
        
        self.is_dragging = False
        self.last_mx, self.last_my = 0, 0
        self.curr_mx, self.curr_my = 0, 0

    def auto_find_camera(self):
        print("\n[System] USB 카메라를 탐색합니다 (DirectShow 모드)...")
        # 주로 외부 USB 카메라인 1, 2번을 먼저 찾고, 없으면 0번(노트북 내장)을 찾습니다.
        for i in [1, 2, 0, 3]: 
            # cv2.CAP_DSHOW 옵션이 윈도우에서의 USB 카메라 충돌 에러를 100% 방지합니다.
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, frame = cap.read()
                # 프레임이 빈 껍데기가 아니고 정상적으로 읽히는지 철저히 확인
                if ret and frame is not None and frame.size > 0:
                    print(f"✅ [System] {i}번 포트에서 카메라 연결 성공!\n")
                    return cap
            cap.release()
        return None

    def load_dxf(self, path):
        if not path or not os.path.exists(path): return [], 0
        doc = ezdxf.readfile(path)
        msp = doc.modelspace()
        contours, all_pts = [], []
        for e in msp.query('LWPOLYLINE'):
            pts = np.array(e.get_points('xy'), dtype=np.float32)
            contours.append(pts)
            all_pts.extend(pts)
        if not all_pts: return [], 0
        all_pts = np.array(all_pts)
        center = np.mean(all_pts, axis=0)
        dxf_w = np.max(all_pts[:, 0]) - np.min(all_pts[:, 0])
        return [c - center for c in contours], dxf_w

    def init_buttons(self):
        btn_h = 45; margin = 10; start_y = 10
        for i, mode in enumerate(self.modes):
            y1 = start_y + i * (btn_h + margin)
            self.buttons[mode] = (self.view_w + margin, y1, self.total_w - margin, y1 + btn_h)

    def open_file_dialog(self):
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        file_path = filedialog.askopenfilename(
            title="DXF 도면 파일을 선택하세요", 
            filetypes=[("DXF Files", "*.dxf"), ("All Files", "*.*")]
        )
        root.destroy()
        if file_path:
            self.dxf_contours, self.dxf_real_width = self.load_dxf(file_path)
            self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
            self.scale = (self.cam_w * 0.75) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
            self.angle = 0.0
            self.measurements = []

    def save_capture(self, frame):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        cv2.imwrite(f'Capture_{timestamp}.jpg', frame)
        print(f"📸 캡처 저장 완료: Capture_{timestamp}.jpg")

    def get_closest_dxf_point(self, real_x, real_y):
        if not self.dxf_contours: return float('inf'), None
        rad = np.radians(self.angle)
        rot_m = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
        min_dist, closest_pt = float('inf'), None
        for pts in self.dxf_contours:
            pts_real = (pts @ rot_m.T) * self.scale + [self.offset_x, self.offset_y]
            dists = np.linalg.norm(pts_real - [real_x, real_y], axis=1)
            idx = np.argmin(dists)
            if dists[idx] < min_dist:
                min_dist, closest_pt = dists[idx], pts_real[idx]
        return min_dist, closest_pt

    def draw_dim_line(self, img, p1, p2, color=(0, 0, 255), thickness=2):
        p1_i, p2_i = (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1]))
        cv2.line(img, p1_i, p2_i, color, thickness)
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = np.hypot(dx, dy)
        if length > 0:
            nx, ny = -dy / length, dx / length
            tick_len = 10
            t1_p1, t1_p2 = (int(p1[0] + nx * tick_len), int(p1[1] + ny * tick_len)), (int(p1[0] - nx * tick_len), int(p1[1] - ny * tick_len))
            t2_p1, t2_p2 = (int(p2[0] + nx * tick_len), int(p2[1] + ny * tick_len)), (int(p2[0] - nx * tick_len), int(p2[1] - ny * tick_len))
            cv2.line(img, t1_p1, t1_p2, color, thickness)
            cv2.line(img, t2_p1, t2_p2, color, thickness)

    def mouse_callback(self, event, x, y, flags, param):
        self.curr_mx, self.curr_my = x, y
        if event == cv2.EVENT_LBUTTONDOWN and x > self.view_w:
            for mode, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    if mode == 'LOAD_DXF': self.open_file_dialog()
                    elif mode == 'SAVE_IMG': self.save_capture(self.last_canvas)
                    elif mode == 'CLEAR': 
                        self.measurements = []
                        self.calib_p1, self.calib_p2 = None, None
                        self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
                        self.scale = (self.cam_w * 0.75) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
                        self.angle = 0.0
                    elif mode == 'QUIT': self.current_mode = 'QUIT'
                    else: self.current_mode = mode
            return

        w_ratio = self.cam_w / self.view_w
        real_x, real_y = x * w_ratio, y * w_ratio

        if event == cv2.EVENT_LBUTTONDOWN:
            self.is_dragging = True
            self.last_mx, self.last_my = x, y
            if self.current_mode == 'CALIB':
                self.calib_p1, self.calib_p2 = (real_x, real_y), (real_x, real_y)
            elif self.current_mode == 'MEASURE' and self.scale > 0.1:
                min_dist, closest_pt = self.get_closest_dxf_point(real_x, real_y)
                if closest_pt is not None:
                    self.measurements.append(((real_x, real_y), closest_pt, min_dist / self.scale))

        elif event == cv2.EVENT_MOUSEMOVE and self.is_dragging:
            dx, dy = (x - self.last_mx) * w_ratio, (y - self.last_my) * w_ratio
            if self.current_mode == 'PAN': self.offset_x += dx; self.offset_y += dy
            elif self.current_mode == 'ZOOM': self.scale *= (1 - dy * 0.005)
            elif self.current_mode == 'ROTATE': self.angle += dx * 0.1
            elif self.current_mode == 'CALIB': self.calib_p2 = (real_x, real_y)
            self.last_mx, self.last_my = x, y

        elif event == cv2.EVENT_LBUTTONUP:
            self.is_dragging = False
            if self.current_mode == 'CALIB' and self.calib_p1 is not None:
                self.calib_p2 = (real_x, real_y)
                dist_px = np.linalg.norm(np.array(self.calib_p1) - np.array(self.calib_p2))
                if dist_px > 5: self.need_calib_input = True
                else: self.calib_p1, self.calib_p2 = None, None

    def draw_ui(self, display_img):
        cv2.rectangle(display_img, (self.view_w, 0), (self.total_w, self.view_h), (40, 40, 40), -1)
        for mode, (x1, y1, x2, y2) in self.buttons.items():
            color = (0, 200, 0) if mode == self.current_mode else (100, 100, 100)
            if mode == 'LOAD_DXF': color = (200, 100, 0)
            elif mode in ['SAVE_IMG', 'CLEAR', 'QUIT']: color = (0, 100, 200)
            cv2.rectangle(display_img, (x1, y1), (x2, y2), color, -1)
            cv2.putText(display_img, mode, (x1 + 10, y1 + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        mag_size = 180
        mag_y1, mag_y2 = self.view_h - mag_size - 10, self.view_h - 10
        mag_x1, mag_x2 = self.view_w + 10, self.total_w - 10
        cv2.rectangle(display_img, (mag_x1-2, mag_y1-2), (mag_x2+2, mag_y2+2), (255, 255, 255), 2)
        
        if self.curr_mx < self.view_w:
            rx, ry = int(self.curr_mx * (self.cam_w / self.view_w)), int(self.curr_my * (self.cam_w / self.view_w))
            roi_size = 40
            y1, y2 = max(0, ry - roi_size), min(self.cam_h, ry + roi_size)
            x1, x2 = max(0, rx - roi_size), min(self.cam_w, rx + roi_size)
            if y2 > y1 and x2 > x1:
                roi = self.last_canvas[y1:y2, x1:x2]
                if roi.size > 0:
                    roi_resized = cv2.resize(roi, (mag_size, mag_size), interpolation=cv2.INTER_NEAREST)
                    display_img[mag_y1:mag_y1+mag_size, mag_x1:mag_x1+mag_size] = roi_resized
                    cv2.line(display_img, (mag_x1 + mag_size//2, mag_y1), (mag_x1 + mag_size//2, mag_y2), (0, 255, 0), 1)
                    cv2.line(display_img, (mag_x1, mag_y1 + mag_size//2), (mag_x2, mag_y1 + mag_size//2), (0, 255, 0), 1)

    def run(self):
        cv2.namedWindow('Vision Inspector', cv2.WINDOW_NORMAL)
        cv2.setMouseCallback('Vision Inspector', self.mouse_callback)

        while self.current_mode != 'QUIT':
            ret, frame = self.cap.read()
            if not ret: break
            
            canvas = frame.copy()
            rad = np.radians(self.angle)
            rot_m = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
            
            for pts in self.dxf_contours:
                pts_draw = ((pts @ rot_m.T) * self.scale + [self.offset_x, self.offset_y]).astype(np.int32)
                cv2.polylines(canvas, [pts_draw], True, (0, 255, 0), 2)
            
            for m in self.measurements:
                self.draw_dim_line(canvas, m[0], m[1], color=(255, 0, 255), thickness=2)
                cv2.putText(canvas, f"L:{m[2]:.3f}mm", (int(m[0][0])+10, int(m[0][1])), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                
            if self.calib_p1 and self.calib_p2:
                self.draw_dim_line(canvas, self.calib_p1, self.calib_p2, color=(0, 0, 255), thickness=2)

            self.last_canvas = canvas.copy()
            res_view = cv2.resize(canvas, (self.view_w, self.view_h))
            display_img = np.zeros((self.view_h, self.total_w, 3), dtype=np.uint8)
            display_img[:, :self.view_w] = res_view
            self.draw_ui(display_img)
            
            cv2.putText(display_img, f"Mode: {self.current_mode} | Scale: {self.scale:.2f}", 
                        (20, self.view_h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            cv2.imshow('Vision Inspector', display_img)
            
            if self.need_calib_input:
                cv2.waitKey(10)
                dist_px = np.linalg.norm(np.array(self.calib_p1) - np.array(self.calib_p2))
                try:
                    real_mm = float(input("\n[Calib] 드래그한 선의 실제 길이(mm) 입력: "))
                    self.base_px_per_mm = dist_px / real_mm
                    self.scale = self.base_px_per_mm
                    print(f"✅ 완료: 1mm = {self.base_px_per_mm:.2f}px")
                except ValueError: pass
                self.calib_p1, self.calib_p2 = None, None
                self.need_calib_input = False
            
            if cv2.waitKey(1) == ord('q'): break

        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    inspector = VisionInspector()
    inspector.run()