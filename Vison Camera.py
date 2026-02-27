import cv2
import numpy as np
import ezdxf
import os
import sys
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from PIL import ImageFont, ImageDraw, Image

class VisionInspector:
    def __init__(self, dxf_path=""):
        self.dxf_path = dxf_path
        self.current_cam_idx = 0
        self.cap = self.auto_scan_and_connect(0)
        if self.cap is None: sys.exit()
        
        self.is_running = True; self.is_frozen = False; self.frozen_frame = None; self.last_full_canvas = None
        self.view_w, self.ui_w = 1200, 280; self.total_w = self.view_w + self.ui_w
        
        # 색상 테마 설정
        self.clr_bg = (248, 249, 250); self.clr_primary = (54, 116, 217)
        self.clr_pressed = (20, 60, 140); self.clr_text = (33, 37, 41)
        self.color_palette = [(0, 255, 0), (0, 0, 255), (255, 0, 0), (0, 255, 255), (255, 255, 255)]
        self.idx_dxf_color = 0; self.idx_meas_color = 3; self.idx_calib_color = 1
        
        # 버튼 내부 모드와 한글 라벨 매칭
        self.btn_labels = {
            'SWITCH_CAM': '카메라 전환', 'FREEZE_LIVE': '정지 / 라이브',
            'LOAD_DXF': '도면 불러오기', 'DXF_COLOR': '도면 색상',
            'PAN': '이동 (PAN)', 'ZOOM': '확대 / 축소',
            'ROTATE': '회전 (Angle)', 'CLEAR': '전체 삭제',
            'MEAS_P2P': '직선 측정', 'MEAS_HV': '수평수직 측정',
            'MEAS_COLOR': '측정 색상', 'MEAS_UNDO': '측정 취소',
            'CALIB': '캘리브레이션', 'CALIB_COLOR': '캘리브 색상',
            'SAVE_IMG': '이미지 저장', 'QUIT': '종료'
        }
        
        self.modes_grid = [
            ['SWITCH_CAM', 'FREEZE_LIVE'], ['LOAD_DXF', 'DXF_COLOR'],
            ['PAN', 'ZOOM'], ['ROTATE', 'CLEAR'],
            ['MEAS_P2P', 'MEAS_HV'], ['MEAS_COLOR', 'MEAS_UNDO'],
            ['CALIB', 'CALIB_COLOR'], ['SAVE_IMG', 'QUIT']
        ]
        
        self.current_mode = 'PAN'; self.pressed_button = None; self.buttons = {}; self.init_buttons()
        self.dxf_contours = []; self.dxf_real_width = 0
        self.setup_camera(); self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
        self.scale = 1.0; self.angle = 0.0
        
        self.measurements = []; self.measure_p1 = None; self.measure_p2 = None; self.measure_temp_val = 0
        self.calib_p1 = None; self.calib_p2 = None; self.calib_temp_data = None; self.fixed_calib_line = None
        self.is_dragging = False; self.curr_mx, self.curr_my = 0, 0
        if dxf_path: self.load_dxf_action(dxf_path)

    def setup_camera(self):
        ret, frame = self.cap.read()
        if ret: self.cam_h, self.cam_w = frame.shape[:2]; self.view_h = int(self.cam_h * (1200 / self.cam_w))

    def auto_scan_and_connect(self, start_idx):
        for i in range(start_idx, start_idx + 6):
            idx = i % 6
            tmp_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if tmp_cap.isOpened():
                tmp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920); tmp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                time.sleep(0.8); ret, frame = tmp_cap.read()
                if ret and frame is not None: self.current_cam_idx = idx; return tmp_cap
            tmp_cap.release()
        return None

    def switch_camera(self):
        old_cap = self.cap; new_cap = self.auto_scan_and_connect(self.current_cam_idx + 1)
        if new_cap: old_cap.release(); self.cap = new_cap; self.setup_camera(); self.is_frozen = False

    def load_dxf_action(self, path):
        if not path or not os.path.exists(path): return
        try:
            doc = ezdxf.readfile(path); msp = doc.modelspace(); contours, all_pts = [], []
            for e in msp.query('LWPOLYLINE'):
                pts = np.array(e.get_points('xy'), dtype=np.float32); contours.append(pts); all_pts.extend(pts)
            if not all_pts: return
            all_pts = np.array(all_pts); center = np.mean(all_pts, axis=0); self.dxf_contours = [c - center for c in contours]
            self.dxf_real_width = np.max(all_pts[:, 0]) - np.min(all_pts[:, 0])
            if self.scale <= 1.1: self.scale = (self.cam_w * 0.5) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
        except: pass

    def init_buttons(self):
        btn_h, margin_x, margin_y, start_y = 28, 10, 5, 45
        col_w = (self.ui_w - 30 - margin_x) // 2
        for r, row in enumerate(self.modes_grid):
            for c, mode in enumerate(row):
                if not mode: continue
                x1 = self.view_w + 15 + (c * (col_w + margin_x)); y1 = start_y + r * (btn_h + margin_y); self.buttons[mode] = (x1, y1, x1 + col_w, y1 + btn_h)

    def draw_ui(self, display_img):
        cv2.rectangle(display_img, (self.view_w, 0), (self.total_w, self.view_h), self.clr_bg, -1)
        cv2.line(display_img, (self.view_w, 0), (self.view_w, self.view_h), (222, 226, 230), 1)
        
        img_pil = Image.fromarray(display_img); draw = ImageDraw.Draw(img_pil)
        try: 
            font_b = ImageFont.truetype("malgunbd.ttf", 15)
            font_btn = ImageFont.truetype("malgunbd.ttf", 10)
        except: font_b = font_btn = ImageFont.load_default()
        
        draw.text((self.view_w + 20, 10), "비전 측정 시스템", font=font_b, fill=(217, 116, 54))
        
        for mode, (x1, y1, x2, y2) in self.buttons.items():
            active = (mode == self.current_mode); pressed = (mode == self.pressed_button)
            
            # [수정] 클릭 상태 대비 강화 로직
            b_clr = self.clr_pressed if pressed else (self.clr_primary if active else (255, 255, 255))
            t_clr = (255, 255, 255) if (active or pressed) else self.clr_text
            
            cv2.rectangle(display_img, (x1, y1), (x2, y2), b_clr, -1)
            cv2.rectangle(display_img, (x1, y1), (x2, y2), (206, 212, 218), 1)
            
            # PIL로 한글 텍스트 그리기
            label = self.btn_labels.get(mode, mode)
            draw.text((x1 + 10, y1 + 7), label, font=font_btn, fill=(t_clr[2], t_clr[1], t_clr[0]))
            
        display_img = np.array(img_pil)
        
        mag_size, mag_margin = 200, 25; mag_y1, mag_y2 = self.view_h - mag_size - mag_margin, self.view_h - mag_margin
        mag_x1, mag_x2 = self.view_w + (self.ui_w - mag_size)//2, self.view_w + (self.ui_w + mag_size)//2
        cv2.rectangle(display_img, (mag_x1-2, mag_y1-2), (mag_x2+2, mag_y2+2), (200, 200, 200), 2)
        if self.curr_mx < self.view_w and self.last_full_canvas is not None:
            w_ratio = self.cam_w / self.view_w; rx, ry = int(self.curr_mx * w_ratio), int(self.curr_my * w_ratio)
            roi_s = 30; y1, y2 = max(0, ry-roi_s), min(self.cam_h, ry+roi_s); x1, x2 = max(0, rx-roi_s), min(self.cam_w, rx+roi_s)
            if y2 > y1 and x2 > x1:
                roi = self.last_full_canvas[y1:y2, x1:x2]; roi_res = cv2.resize(roi, (mag_size, mag_size), interpolation=cv2.INTER_NEAREST)
                display_img[mag_y1:mag_y2, mag_x1:mag_x2] = roi_res
                cv2.line(display_img, (mag_x1 + mag_size//2, mag_y1), (mag_x1 + mag_size//2, mag_y2), (0, 255, 0), 1)
                cv2.line(display_img, (mag_x1, mag_y1 + mag_size//2), (mag_x2, mag_y1 + mag_size//2), (0, 255, 0), 1)
        return display_img

    def mouse_callback(self, event, x, y, flags, param):
        self.curr_mx, self.curr_my = x, y
        if event == cv2.EVENT_LBUTTONDOWN and x > self.view_w:
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    self.pressed_button = m
                    if m == 'FREEZE_LIVE':
                        if not self.is_frozen:
                            ret, frame = self.cap.read()
                            if ret: self.frozen_frame = frame.copy(); self.is_frozen = True
                        else: self.is_frozen = False
                    elif m == 'SWITCH_CAM': self.switch_camera()
                    elif m == 'DXF_COLOR': self.idx_dxf_color = (self.idx_dxf_color + 1) % len(self.color_palette)
                    elif m == 'MEAS_COLOR': self.idx_meas_color = (self.idx_meas_color + 1) % len(self.color_palette)
                    elif m == 'CALIB_COLOR': self.idx_calib_color = (self.idx_calib_color + 1) % len(self.color_palette)
                    elif m == 'MEAS_UNDO':
                        if self.measure_p2: self.measure_p2 = None; self.measure_p1 = None
                        elif self.measure_p1: self.measure_p1 = None
                        elif self.measurements: self.measurements.pop()
                    elif m == 'SAVE_IMG':
                        if self.last_full_canvas is not None:
                            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
                            path = filedialog.asksaveasfilename(defaultextension=".jpg", initialfile=f'검사결과_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg', parent=root)
                            if path:
                                try:
                                    res, buffer = cv2.imencode('.jpg', self.last_full_canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                                    if res:
                                        with open(path, "wb") as f: f.write(buffer.tobytes())
                                        messagebox.showinfo("저장 완료", "이미지가 성공적으로 저장되었습니다.", parent=root)
                                except: messagebox.showerror("저장 실패", "이미지를 저장하는 중 오류가 발생했습니다.", parent=root)
                            root.destroy()
                    elif m == 'LOAD_DXF':
                        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
                        path = filedialog.askopenfilename(filetypes=[("DXF 도면", "*.dxf")], parent=root); root.destroy()
                        if path: self.load_dxf_action(path)
                    elif m == 'CLEAR': self.measurements = []; self.measure_p1 = None; self.measure_p2 = None; self.fixed_calib_line = None; self.calib_temp_data = None
                    elif m == 'QUIT': self.is_running = False
                    else: self.current_mode = m; self.measure_p1 = None; self.measure_p2 = None
                    return
        if event == cv2.EVENT_LBUTTONUP: self.pressed_button = None

        w_ratio = self.cam_w / self.view_w; rx, ry = x * w_ratio, y * w_ratio
        if flags & cv2.EVENT_FLAG_SHIFTKEY:
            if self.measure_p1:
                if abs(rx - self.measure_p1[0]) > abs(ry - self.measure_p1[1]): ry = self.measure_p1[1]
                else: rx = self.measure_p1[0]
            elif self.calib_p1:
                if abs(rx - self.calib_p1[0]) > abs(ry - self.calib_p1[1]): ry = self.calib_p1[1]
                else: rx = self.calib_p1[0]

        if event == cv2.EVENT_LBUTTONDOWN and x <= self.view_w:
            if self.current_mode in ['PAN', 'ZOOM', 'ROTATE']: self.is_dragging = True; self.lmx, self.lmy = x, y; return
            if 'MEAS' in self.current_mode:
                if self.measure_p1 is None: self.measure_p1 = (rx, ry)
                elif self.measure_p2 is None: 
                    self.measure_p2 = (rx, ry); p1, p2 = np.array(self.measure_p1), np.array(self.measure_p2)
                    self.measure_temp_val = np.linalg.norm(p1-p2) if self.current_mode == 'MEAS_P2P' else max(abs(p1[0]-p2[0]), abs(p1[1]-p2[1]))
                else:
                    self.measurements.append((self.measure_p1, self.measure_p2, self.measure_temp_val/self.scale, self.current_mode, (rx, ry))); self.measure_p1 = None; self.measure_p2 = None
            elif self.current_mode == 'CALIB':
                if self.calib_temp_data: p1, p2, val = self.calib_temp_data; self.fixed_calib_line = (p1, p2, val, (rx, ry)); self.calib_temp_data = None
                else: self.is_dragging = True; self.lmx, self.lmy = x, y; self.calib_p1 = (rx, ry); self.calib_p2 = (rx, ry)
        
        elif event == cv2.EVENT_MOUSEMOVE and self.is_dragging:
            if self.current_mode == 'CALIB': self.calib_p2 = (rx, ry)
            else:
                dx, dy = (x - self.lmx) * w_ratio, (y - self.lmy) * w_ratio
                if self.current_mode == 'PAN': self.offset_x += dx; self.offset_y += dy
                elif self.current_mode == 'ZOOM': self.scale *= (1 - (y - self.lmy) * 0.005)
                elif self.current_mode == 'ROTATE': self.angle += (x - self.lmx) * 0.2
                self.lmx, self.lmy = x, y
        
        elif event == cv2.EVENT_LBUTTONUP:
            if self.is_dragging and self.current_mode == 'CALIB' and self.calib_p1:
                dist_px = np.linalg.norm(np.array(self.calib_p1) - np.array([rx, ry]))
                if dist_px > 10:
                    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
                    val = simpledialog.askfloat("캘리브레이션", "실제 길이(mm)를 입력하세요:", parent=root); root.destroy()
                    if val: self.scale = dist_px / val; self.calib_temp_data = (self.calib_p1, (rx, ry), val)
            self.is_dragging = False; self.calib_p1 = self.calib_p2 = None

    def run(self):
        cv2.namedWindow('Vision Inspector', cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback('Vision Inspector', self.mouse_callback)
        while self.is_running:
            if cv2.getWindowProperty('Vision Inspector', cv2.WND_PROP_VISIBLE) < 1: break
            if self.is_frozen: frame = self.frozen_frame.copy()
            else: 
                ret, frame = self.cap.read()
                if not ret: continue
            canvas = frame.copy(); rad = np.radians(self.angle); rot_m = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
            dxf_clr, meas_clr, calib_clr = self.color_palette[self.idx_dxf_color], self.color_palette[self.idx_meas_color], self.color_palette[self.idx_calib_color]
            for pts in self.dxf_contours:
                pts_draw = ((pts @ rot_m.T) * self.scale + [self.offset_x, self.offset_y]).astype(np.int32); cv2.polylines(canvas, [pts_draw], True, dxf_clr, 1)
            if self.fixed_calib_line:
                p1, p2, val, pt = self.fixed_calib_line; cv2.line(canvas, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])), calib_clr, 1); cv2.putText(canvas, f"REF: {val:.1f}mm", (int(pt[0]), int(pt[1])), cv2.FONT_HERSHEY_SIMPLEX, 0.5, calib_clr, 1)
            for m1, m2, val, m_type, pt in self.measurements:
                p1, p2 = (int(m1[0]), int(m1[1])), (int(m2[0]), int(m2[1]))
                if m_type == 'MEAS_HV':
                    if abs(p1[0]-p2[0]) > abs(p1[1]-p2[1]): cv2.line(canvas, p1, (p2[0], p1[1]), meas_clr, 1); p2 = (p2[0], p1[1])
                    else: cv2.line(canvas, p1, (p1[0], p2[1]), meas_clr, 1); p2 = (p1[0], p2[1])
                else: cv2.line(canvas, p1, p2, meas_clr, 1)
                cv2.putText(canvas, f"{val:.3f}mm", (int(pt[0]), int(pt[1])), cv2.FONT_HERSHEY_SIMPLEX, 0.5, meas_clr, 1)
            if self.is_dragging and self.current_mode in ['PAN', 'ZOOM', 'ROTATE']:
                mx, my = int(self.curr_mx*(self.cam_w/self.view_w)), int(self.curr_my*(self.cam_w/self.view_w)); cv2.drawMarker(canvas, (mx, my), (0, 255, 255), markerType=cv2.MARKER_CROSS, markerSize=25, thickness=1)
            if self.measure_p2: cv2.putText(canvas, f"{self.measure_temp_val/self.scale:.3f}mm", (int(self.curr_mx*(self.cam_w/self.view_w)), int(self.curr_my*(self.cam_w/self.view_w))), cv2.FONT_HERSHEY_SIMPLEX, 0.5, meas_clr, 1)
            elif self.measure_p1: cv2.circle(canvas, (int(self.measure_p1[0]), int(self.measure_p1[1])), 5, meas_clr, 1)
            if self.calib_temp_data: cv2.putText(canvas, f"REF: {self.calib_temp_data[2]:.1f}mm", (int(self.curr_mx*(self.cam_w/self.view_w)), int(self.curr_my*(self.cam_w/self.view_w))), cv2.FONT_HERSHEY_SIMPLEX, 0.5, calib_clr, 1)
            elif self.calib_p1 and self.calib_p2: cv2.line(canvas, (int(self.calib_p1[0]), int(self.calib_p1[1])), (int(self.calib_p2[0]), int(self.calib_p2[1])), calib_clr, 1)
            self.last_full_canvas = canvas.copy(); res_view = cv2.resize(canvas, (self.view_w, self.view_h))
            display_img = np.zeros((self.view_h, self.total_w, 3), dtype=np.uint8); display_img[:, :self.view_w] = res_view; display_img = self.draw_ui(display_img)
            cv2.imshow('Vision Inspector', display_img)
            if cv2.waitKey(1) == ord('q'): break
        self.cap.release(); cv2.destroyAllWindows(); sys.exit()

if __name__ == "__main__":
    inspector = VisionInspector(); inspector.run()
