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
        
        # 상태 관리 변수
        self.is_frozen = False      # 사진 모드(정지) 여부
        self.frozen_frame = None    # 캡처된 정지 화면
        self.last_full_canvas = None # 저장용 최종 이미지
        
        # UI 및 색상 설정
        self.view_w, self.ui_w = 1200, 280
        self.total_w = self.view_w + self.ui_w
        self.clr_bg = (248, 249, 250); self.clr_primary = (54, 116, 217)
        self.clr_pressed = (34, 86, 167); self.clr_text = (33, 37, 41)
        
        # 도면 색상 (녹색, 적색, 청색, 황색, 백색)
        self.dxf_color_list = [(0, 255, 0), (0, 0, 255), (255, 0, 0), (0, 255, 255), (255, 255, 255)]
        self.current_color_idx = 0
        
        # [중요] 버튼 목록에 FREEZE_LIVE 추가
        self.modes = ['FREEZE_LIVE', 'SWITCH_CAM', 'COLOR_TOGGLE', 'LOAD_DXF', 'PAN', 'ZOOM', 'ROTATE', 'MEASURE', 'CALIB', 'SAVE_IMG', 'CLEAR', 'QUIT']
        self.current_mode = 'PAN'
        self.pressed_button = None # 클릭 시 꾹 눌리는 효과용
        self.buttons = {}
        self.init_buttons()
        
        self.dxf_contours, self.dxf_real_width = self.load_dxf(dxf_path)
        self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
        self.scale = (self.cam_w * 0.75) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
        self.angle = 0.0; self.measurements = []; self.calib_p1 = None
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
        btn_h = 36; margin = 6; start_y = 60
        for i, mode in enumerate(self.modes):
            y1 = start_y + i * (btn_h + margin)
            self.buttons[mode] = (self.view_w + 15, y1, self.total_w - 15, y1 + btn_h)

    def draw_text_pretty(self, img, text, pos, size=14, color=(33, 37, 41), bold=False):
        img_pil = Image.fromarray(img); draw = ImageDraw.Draw(img_pil)
        try: font = ImageFont.truetype("malgun.ttf" if not bold else "malgunbd.ttf", size)
        except: font = ImageFont.load_default()
        draw.text(pos, text, font=font, fill=(color[2], color[1], color[0])); return np.array(img_pil)

    def draw_ui(self, display_img):
        # 사이드바 배경 및 타이틀
        cv2.rectangle(display_img, (self.view_w, 0), (self.total_w, self.view_h), self.clr_bg, -1)
        cv2.line(display_img, (self.view_w, 0), (self.view_w, self.view_h), (222, 226, 230), 1)
        status_text = "PHOTO MODE" if self.is_frozen else "LIVE MODE"
        display_img = self.draw_text_pretty(display_img, status_text, (self.view_w + 20, 20), size=18, bold=True, color=self.clr_primary)
        
        for mode, (x1, y1, x2, y2) in self.buttons.items():
            active = (mode == self.current_mode); pressed = (mode == self.pressed_button)
            # 꾹 눌리는 효과 색상 적용
            b_clr = self.clr_pressed if pressed else (self.clr_primary if active else (255, 255, 255))
            t_clr = (255, 255, 255) if (active or pressed) else self.clr_text
            
            if not pressed: cv2.rectangle(display_img, (x1+1, y1+1), (x2+1, y2+1), (200, 200, 200), -1)
            cv2.rectangle(display_img, (x1, y1), (x2, y2), b_clr, -1)
            cv2.rectangle(display_img, (x1, y1), (x2, y2), (180, 180, 180), 1)
            
            # 눌렸을 때 텍스트를 약간 아래로 이동시켜 입체감 부여
            ty = y1 + 10 if pressed else y1 + 8
            display_img = self.draw_text_pretty(display_img, mode, (x1 + 10, ty), color=t_clr, bold=active)
        return display_img

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and x > self.view_w:
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2: self.pressed_button = m; return
                
        if event == cv2.EVENT_LBUTTONUP and self.pressed_button:
            m = self.pressed_button; self.pressed_button = None; bx1, by1, bx2, by2 = self.buttons[m]
            if bx1 <= x <= bx2 and by1 <= y <= by2:
                if m == 'FREEZE_LIVE': # [신규] 화면 정지 및 해제
                    if not self.is_frozen:
                        ret, frame = self.cap.read()
                        if ret: self.frozen_frame = frame.copy(); self.is_frozen = True
                    else: self.is_frozen = False
                elif m == 'SWITCH_CAM': self.switch_camera()
                elif m == 'COLOR_TOGGLE': self.current_color_idx = (self.current_color_idx + 1) % len(self.dxf_color_list)
                elif m == 'SAVE_IMG': # [신규] 도면 포함 저장
                    fn = f'Inspection_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg'
                    cv2.imwrite(fn, self.last_full_canvas)
                elif m == 'LOAD_DXF': self.open_file_dialog()
                elif m == 'CLEAR': self.measurements = []; self.calib_p1 = None; self.scale = 1.0; self.angle = 0.0
                elif m == 'QUIT': self.current_mode = 'QUIT'
                else: self.current_mode = m
            return
            
        # 메인 화면 조작 (PAN, ZOOM, ROTATE)
        w_ratio = self.cam_w / self.view_w; rx, ry = x * w_ratio, y * w_ratio
        if event == cv2.EVENT_LBUTTONDOWN:
            self.is_dragging = True; self.lmx, self.lmy = x, y
            if self.current_mode == 'CALIB': self.calib_p1 = (rx, ry)
        elif event == cv2.EVENT_MOUSEMOVE and self.is_dragging:
            dx, dy = (x - self.lmx) * w_ratio, (y - self.lmy) * w_ratio
            if self.current_mode == 'PAN': self.offset_x += dx; self.offset_y += dy
            elif self.current_mode == 'ZOOM': self.scale *= (1 - dy * 0.005)
            elif self.current_mode == 'ROTATE': self.angle += dx * 0.1
            self.lmx, self.lmy = x, y
        elif event == cv2.EVENT_LBUTTONUP: self.is_dragging = False

    def open_file_dialog(self):
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        path = filedialog.askopenfilename(filetypes=[("DXF Files", "*.dxf")]); root.destroy()
        if path:
            self.dxf_contours, self.dxf_real_width = self.load_dxf(path)
            self.scale = (self.cam_w * 0.75) / self.dxf_real_width if self.dxf_real_width > 0 else 1.0
            self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2

    def run(self):
        cv2.namedWindow('Vision Inspector', cv2.WINDOW_NORMAL)
        cv2.setMouseCallback('Vision Inspector', self.mouse_callback)
        while self.current_mode != 'QUIT':
            # 정지 모드일 때는 캡처된 화면 사용, 라이브일 때는 실시간 수신
            if self.is_frozen: frame = self.frozen_frame.copy()
            else: 
                ret, frame = self.cap.read()
                if not ret: continue
            
            canvas = frame.copy(); rad = np.radians(self.angle); rot_m = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
            draw_color = self.dxf_color_list[self.current_color_idx]
            
            # 도면 그리기 (정지 화면 위에도 그려짐)
            for pts in self.dxf_contours:
                pts_draw = ((pts @ rot_m.T) * self.scale + [self.offset_x, self.offset_y]).astype(np.int32)
                cv2.polylines(canvas, [pts_draw], True, draw_color, 1)
            
            self.last_full_canvas = canvas.copy() # 저장 시 도면이 포함되도록 원본 저장
            res_view = cv2.resize(canvas, (self.view_w, self.view_h))
            display_img = np.zeros((self.view_h, self.total_w, 3), dtype=np.uint8)
            display_img[:, :self.view_w] = res_view; display_img = self.draw_ui(display_img)
            
            cv2.imshow('Vision Inspector', display_img)
            if cv2.waitKey(1) == ord('q'): break
        self.cap.release(); cv2.destroyAllWindows()

if __name__ == "__main__":
    inspector = VisionInspector(); inspector.run()
