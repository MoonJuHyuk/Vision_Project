import cv2
import numpy as np
import ezdxf
import os
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, simpledialog
from PIL import ImageFont, ImageDraw, Image

class VisionInspector:
    def __init__(self, dxf_path=""):
        self.dxf_path = dxf_path
        self.cam_index_list = [1, 2, 3, 0]
        self.current_cam_idx_ptr = 0
        self.cap = self.auto_find_camera()
        if self.cap is None: exit()
        self.setup_camera()
        
        self.is_frozen = False
        self.frozen_frame = None
        self.last_full_canvas = None
        
        self.view_w, self.ui_w = 1200, 280
        self.total_w = self.view_w + self.ui_w
        self.clr_bg = (248, 249, 250); self.clr_primary = (54, 116, 217)
        self.clr_pressed = (34, 86, 167); self.clr_text = (33, 37, 41)
        self.dxf_color_list = [(0, 255, 0), (0, 0, 255), (255, 0, 0), (0, 255, 255), (255, 255, 255)]
        self.current_color_idx = 0
        
        self.modes = ['FREEZE_LIVE', 'SWITCH_CAM', 'COLOR_TOGGLE', 'LOAD_DXF', 'PAN', 'ZOOM', 'ROTATE', 'MEASURE', 'CALIB', 'SAVE_IMG', 'CLEAR', 'QUIT']
        self.current_mode = 'PAN'; self.pressed_button = None; self.buttons = {}; self.init_buttons()
        
        self.dxf_contours, self.dxf_real_width = self.load_dxf(dxf_path)
        self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
        self.scale = (self.cam_w * 0.75) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
        self.angle = 0.0
        
        self.measurements = []
        self.measure_p1 = None
        
        # [수정] 캘리브레이션 기준선 저장 변수
        self.calib_p1 = None; self.calib_p2 = None
        self.fixed_calib_line = None # (p1, p2, mm_val) 저장용
        
        self.is_dragging = False; self.curr_mx, self.curr_my = 0, 0

    def setup_camera(self):
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920); self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        cv2.waitKey(500); ret, frame = self.cap.read()
        if ret: self.cam_h, self.cam_w = frame.shape[:2]; self.view_h = int(self.cam_h * (1200 / self.cam_w))

    def auto_find_camera(self):
        for i in self.cam_index_list:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None: return cap
            cap.release()
        return None

    def switch_camera(self):
        self.current_cam_idx_ptr = (self.current_cam_idx_ptr + 1) % len(self.cam_index_list)
        new_idx = self.cam_index_list[self.current_cam_idx_ptr]
        if self.cap: self.cap.release()
        self.cap = cv2.VideoCapture(new_idx, cv2.CAP_DSHOW)
        if self.cap.isOpened(): self.setup_camera(); self.is_frozen = False

    def load_dxf(self, path):
        if not path or not os.path.exists(path): return [], 0
        doc = ezdxf.readfile(path); msp = doc.modelspace(); contours, all_pts = [], []
        for e in msp.query('LWPOLYLINE'):
            pts = np.array(e.get_points('xy'), dtype=np.float32); contours.append(pts); all_pts.extend(pts)
        if not all_pts: return [], 0
        all_pts = np.array(all_pts); center = np.mean(all_pts, axis=0)
        dxf_w = np.max(all_pts[:, 0]) - np.min(all_pts[:, 0])
        return [c - center for c in contours], dxf_w

    def init_buttons(self):
        btn_h = 32; margin = 5; start_y = 55
        for i, mode in enumerate(self.modes):
            y1 = start_y + i * (btn_h + margin)
            self.buttons[mode] = (self.view_w + 15, y1, self.total_w - 15, y1 + btn_h)

    def draw_text_pretty(self, img, text, pos, size=14, color=(33, 37, 41), bold=False):
        img_pil = Image.fromarray(img); draw = ImageDraw.Draw(img_pil)
        try: font = ImageFont.truetype("malgun.ttf" if not bold else "malgunbd.ttf", size)
        except: font = ImageFont.load_default()
        draw.text(pos, text, font=font, fill=(color[2], color[1], color[0])); return np.array(img_pil)

    def draw_ui(self, display_img):
        cv2.rectangle(display_img, (self.view_w, 0), (self.total_w, self.view_h), self.clr_bg, -1)
        cv2.line(display_img, (self.view_w, 0), (self.view_w, self.view_h), (222, 226, 230), 1)
        display_img = self.draw_text_pretty(display_img, "VISION CONTROL", (self.view_w + 20, 15), size=18, bold=True, color=self.clr_primary)
        
        for mode, (x1, y1, x2, y2) in self.buttons.items():
            active = (mode == self.current_mode); pressed = (mode == self.pressed_button)
            b_clr = self.clr_pressed if pressed else (self.clr_primary if active else (255, 255, 255))
            t_clr = (255, 255, 255) if (active or pressed) else self.clr_text
            cv2.rectangle(display_img, (x1, y1), (x2, y2), b_clr, -1)
            cv2.rectangle(display_img, (x1, y1), (x2, y2), (206, 212, 218), 1)
            display_img = self.draw_text_pretty(display_img, mode, (x1 + 10, y1 + 7), size=13, color=t_clr, bold=active)
        
        mag_size = 200; mag_margin = 40
        mag_y1, mag_y2 = self.view_h - mag_size - mag_margin, self.view_h - mag_margin
        mag_x1, mag_x2 = self.view_w + (self.ui_w - mag_size)//2, self.view_w + (self.ui_w + mag_size)//2
        cv2.rectangle(display_img, (mag_x1-2, mag_y1-2), (mag_x2+2, mag_y2+2), (200, 200, 200), 2)
        
        if self.curr_mx < self.view_w:
            w_ratio = self.cam_w / self.view_w
            rx, ry = int(self.curr_mx * w_ratio), int(self.curr_my * w_ratio)
            roi_s = 30
            y1, y2 = max(0, ry-roi_s), min(self.cam_h, ry+roi_s)
            x1, x2 = max(0, rx-roi_s), min(self.cam_w, rx+roi_s)
            if y2 > y1 and x2 > x1:
                roi = self.last_full_canvas[y1:y2, x1:x2]
                roi_res = cv2.resize(roi, (mag_size, mag_size), interpolation=cv2.INTER_NEAREST)
                display_img[mag_y1:mag_y2, mag_x1:mag_x2] = roi_res
                cv2.line(display_img, (mag_x1 + mag_size//2, mag_y1), (mag_x1 + mag_size//2, mag_y2), (0, 255, 0), 1)
                cv2.line(display_img, (mag_x1, mag_y1 + mag_size//2), (mag_x2, mag_y1 + mag_size//2), (0, 255, 0), 1)
        return display_img

    def mouse_callback(self, event, x, y, flags, param):
        self.curr_mx, self.curr_my = x, y
        if event == cv2.EVENT_LBUTTONDOWN and x > self.view_w:
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2: self.pressed_button = m; return
        
        if event == cv2.EVENT_LBUTTONUP and self.pressed_button:
            m = self.pressed_button; self.pressed_button = None; bx1, by1, bx2, by2 = self.buttons[m]
            if bx1 <= x <= bx2 and by1 <= y <= by2:
                if m == 'FREEZE_LIVE':
                    if not self.is_frozen:
                        ret, frame = self.cap.read()
                        if ret: self.frozen_frame = frame.copy(); self.is_frozen = True
                    else: self.is_frozen = False
                elif m == 'SWITCH_CAM': self.switch_camera()
                elif m == 'COLOR_TOGGLE': self.current_color_idx = (self.current_color_idx + 1) % len(self.dxf_color_list)
                elif m == 'SAVE_IMG':
                    fn = f'Inspection_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg'
                    cv2.imwrite(fn, self.last_full_canvas)
                elif m == 'LOAD_DXF': self.open_file_dialog()
                elif m == 'CLEAR': # [수정] 캘리브레이션 기준선도 함께 삭제
                    self.measurements = []; self.measure_p1 = None; self.calib_p1 = None; 
                    self.fixed_calib_line = None; self.scale = 1.0; self.angle = 0.0
                elif m == 'QUIT': self.current_mode = 'QUIT'
                else: self.current_mode = m; self.measure_p1 = None
            return

        w_ratio = self.cam_w / self.view_w; rx, ry = x * w_ratio, y * w_ratio
        if event == cv2.EVENT_LBUTTONDOWN and x <= self.view_w:
            self.is_dragging = True; self.lmx, self.lmy = x, y
            if self.current_mode == 'CALIB': self.calib_p1 = (rx, ry); self.calib_p2 = (rx, ry)
            elif self.current_mode == 'MEASURE':
                if self.measure_p1 is None: self.measure_p1 = (rx, ry)
                else:
                    dist_px = np.linalg.norm(np.array(self.measure_p1) - np.array([rx, ry]))
                    dist_mm = dist_px / self.scale
                    self.measurements.append((self.measure_p1, (rx, ry), dist_mm))
                    self.measure_p1 = None
        
        elif event == cv2.EVENT_MOUSEMOVE and self.is_dragging:
            if self.current_mode == 'CALIB': self.calib_p2 = (rx, ry)
            dx, dy = (x - self.lmx) * w_ratio, (y - self.lmy) * w_ratio
            if self.current_mode == 'PAN': self.offset_x += dx; self.offset_y += dy
            elif self.current_mode == 'ZOOM': self.scale *= (1 - dy * 0.005)
            elif self.current_mode == 'ROTATE': self.angle += dx * 0.1
            self.lmx, self.lmy = x, y
        elif event == cv2.EVENT_LBUTTONUP:
            if self.is_dragging and self.current_mode == 'CALIB' and self.calib_p1:
                dist_px = np.linalg.norm(np.array(self.calib_p1) - np.array([rx, ry]))
                if dist_px > 10:
                    root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
                    val = simpledialog.askfloat("Calibration", "실제 길이(mm) 입력:", parent=root)
                    if val: 
                        self.scale = dist_px / val
                        # [추가] 기준선 고정 데이터 저장
                        self.fixed_calib_line = (self.calib_p1, (rx, ry), val)
                    root.destroy()
                self.calib_p1 = None; self.calib_p2 = None
            self.is_dragging = False

    def run(self):
        cv2.namedWindow('Vision Inspector', cv2.WINDOW_NORMAL)
        cv2.setMouseCallback('Vision Inspector', self.mouse_callback)
        while self.current_mode != 'QUIT':
            if self.is_frozen: frame = self.frozen_frame.copy()
            else: 
                ret, frame = self.cap.read()
                if not ret: continue
            
            canvas = frame.copy(); rad = np.radians(self.angle); rot_m = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
            draw_color = self.dxf_color_list[self.current_color_idx]
            for pts in self.dxf_contours:
                pts_draw = ((pts @ rot_m.T) * self.scale + [self.offset_x, self.offset_y]).astype(np.int32)
                cv2.polylines(canvas, [pts_draw], True, draw_color, 1)
            
            # [추가] 고정된 캘리브레이션 기준선 그리기
            if self.fixed_calib_line:
                p1, p2, val = self.fixed_calib_line
                p1_i, p2_i = (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1]))
                cv2.line(canvas, p1_i, p2_i, (0, 0, 255), 2)
                cv2.circle(canvas, p1_i, 5, (0, 0, 255), -1)
                cv2.circle(canvas, p2_i, 5, (0, 0, 255), -1)
                cv2.putText(canvas, f"CALIB REF: {val:.1f}mm", (p1_i[0], p1_i[1]-15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            for m in self.measurements:
                p1, p2 = (int(m[0][0]), int(m[0][1])), (int(m[1][0]), int(m[1][1]))
                cv2.line(canvas, p1, p2, (255, 0, 255), 2)
                cv2.putText(canvas, f"{m[2]:.3f}mm", (p2[0]+10, p2[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            
            if self.current_mode == 'MEASURE' and self.measure_p1:
                cv2.circle(canvas, (int(self.measure_p1[0]), int(self.measure_p1[1])), 5, (0, 255, 255), 2)

            if self.current_mode == 'CALIB' and self.calib_p1 and self.calib_p2:
                cv2.line(canvas, (int(self.calib_p1[0]), int(self.calib_p1[1])), (int(self.calib_p2[0]), int(self.calib_p2[1])), (0, 0, 255), 2)
            
            self.last_full_canvas = canvas.copy()
            res_view = cv2.resize(canvas, (self.view_w, self.view_h))
            display_img = np.zeros((self.view_h, self.total_w, 3), dtype=np.uint8)
            display_img[:, :self.view_w] = res_view; display_img = self.draw_ui(display_img)
            cv2.imshow('Vision Inspector', display_img)
            if cv2.waitKey(1) == ord('q'): break
        self.cap.release(); cv2.destroyAllWindows()

if __name__ == "__main__":
    inspector = VisionInspector(); inspector.run()
