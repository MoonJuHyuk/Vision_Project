import cv2
import numpy as np
import ezdxf
import os
import sys
import time
import threading
import random
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from PIL import ImageFont, ImageDraw, Image

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


class VisionInspector:
    def __init__(self, dxf_path=""):
        self.dxf_path = dxf_path
        self.current_cam_idx = 0
        self.cap = self.auto_scan_and_connect(0)
        if self.cap is None:
            sys.exit()

        self.is_running = True
        self.is_frozen = False
        self.frozen_frame = None
        self.last_full_canvas = None
        self.view_w, self.ui_w = 1200, 340
        self.total_w = self.view_w + self.ui_w

        # 산업용 다크 테마 색상 설정
        self.clr_bg = (45, 45, 48)
        self.clr_panel = (37, 37, 38)
        self.clr_primary = (0, 122, 204)
        self.clr_hover = (28, 151, 234)
        self.clr_pressed = (0, 84, 153)
        self.clr_active = (0, 153, 76)
        self.clr_text = (241, 241, 241)
        self.clr_text_dim = (160, 160, 160)
        self.clr_border = (63, 63, 70)
        self.clr_section = (28, 28, 30)

        self.color_palette = [
            (0, 255, 0),
            (0, 0, 255),
            (255, 0, 0),
            (0, 255, 255),
            (255, 255, 255)
        ]
        self.idx_dxf_color = 0
        self.idx_meas_color = 3
        self.idx_calib_color = 1

        self.hovered_button = None

        self.btn_labels = {
            'SWITCH_CAM': '카메라 전환',
            'FREEZE_LIVE': '정지 / 라이브',
            'LOAD_DXF': '도면 불러오기',
            'DXF_COLOR': '도면 색상',
            'PAN': '이동 (PAN)',
            'ZOOM': '확대 / 축소',
            'ROTATE': '회전 (Angle)',
            'CROSS': '십자선',
            'CLEAR': '전체 삭제',
            'MEAS_P2P': '직선 측정',
            'MEAS_HV': '수평수직 측정',
            'MEAS_COLOR': '측정 색상',
            'MEAS_UNDO': '측정 취소',
            'CALIB': '캘리브레이션',
            'CALIB_COLOR': '캘리브 색상',
            'SCALE_CONNECT': '저울 연결',
            'SCALE_SAVE': '무게 저장',
            'SAVE_IMG': '이미지 저장',
            'QUIT': '종료'
        }

        self.button_sections = [
            {
                'title': '카메라 제어',
                'buttons': [['SWITCH_CAM', 'FREEZE_LIVE']]
            },
            {
                'title': '도면 관리',
                'buttons': [['LOAD_DXF', 'DXF_COLOR']]
            },
            {
                'title': '뷰 조작',
                'buttons': [
                    ['PAN', 'ZOOM'],
                    ['ROTATE', 'CROSS'],
                    ['CLEAR']
                ]
            },
            {
                'title': '측정 도구',
                'buttons': [
                    ['MEAS_P2P', 'MEAS_HV'],
                    ['MEAS_COLOR', 'MEAS_UNDO']
                ]
            },
            {
                'title': '캘리브레이션',
                'buttons': [['CALIB', 'CALIB_COLOR']]
            },
            {
                'title': '정밀저울',
                'buttons': [['SCALE_CONNECT', 'SCALE_SAVE']]
            },
            {
                'title': '시스템',
                'buttons': [['SAVE_IMG', 'QUIT']]
            }
        ]

        self.current_mode = 'PAN'
        self.pressed_button = None
        self.buttons = {}
        self.section_headers = {}

        self.bottom_area_height = 250

        # 카메라 설정 먼저 (view_h 정의)
        self.dxf_contours = []   # 각 원소: (ctype, pts_array)
        self.dxf_real_width = 0
        self.setup_camera()

        # 버튼 초기화
        self.init_buttons()

        self.offset_x, self.offset_y = self.cam_w // 2, self.cam_h // 2
        self.scale = 1.0
        self.angle = 0.0

        self.measurements = []
        self.measure_p1 = None
        self.measure_p2 = None
        self.measure_temp_val = 0
        self.calib_p1 = None
        self.calib_p2 = None
        self.calib_temp_data = None
        self.fixed_calib_line = None
        self.is_dragging = False
        self.curr_mx, self.curr_my = 0, 0
        self.cross_pos = None  # (x, y) 카메라 좌표계

        # 저울 관련 초기화
        self.scale_weight = None          # 현재 무게값 (g)
        self.scale_connected = False      # 실제 시리얼 연결 여부
        self.scale_simulating = True      # 시뮬레이션 모드
        self.scale_com_port = None
        self.scale_serial = None
        self.scale_thread = None
        self.scale_lock = threading.Lock()
        self.weight_log = []              # 저장된 무게 기록

        self._start_scale_simulation()

        if dxf_path:
            self.load_dxf_action(dxf_path)

    # ──────────────────────────────────────────────
    # 저울 (Scale)
    # ──────────────────────────────────────────────
    def _start_scale_simulation(self):
        """시뮬레이션 모드: 가상 무게값을 주기적으로 생성"""
        def _sim_loop():
            base = 12.34
            while self.scale_simulating:
                noise = random.uniform(-0.05, 0.05)
                with self.scale_lock:
                    self.scale_weight = round(base + noise, 2)
                time.sleep(0.5)

        t = threading.Thread(target=_sim_loop, daemon=True)
        t.start()
        self.scale_thread = t

    def connect_scale(self, port, baud=9600):
        """실제 RS-232 저울 연결 (pyserial 필요)"""
        if not SERIAL_AVAILABLE:
            messagebox.showerror("오류", "pyserial이 설치되지 않았습니다.\n\npip install pyserial")
            return False
        try:
            ser = serial.Serial(port, baudrate=baud, bytesize=8,
                                parity='N', stopbits=1, timeout=1)
            self.scale_serial = ser
            self.scale_com_port = port
            self.scale_connected = True
            self.scale_simulating = False

            def _read_loop():
                buf = ""
                while self.scale_connected:
                    try:
                        ch = ser.read(1).decode('ascii', errors='ignore')
                        if ch in ('\r', '\n'):
                            val = self._parse_weight(buf.strip())
                            if val is not None:
                                with self.scale_lock:
                                    self.scale_weight = val
                            buf = ""
                        else:
                            buf += ch
                    except Exception:
                        break

            t = threading.Thread(target=_read_loop, daemon=True)
            t.start()
            self.scale_thread = t
            return True
        except Exception as ex:
            messagebox.showerror("저울 연결 실패", str(ex))
            return False

    def disconnect_scale(self):
        self.scale_connected = False
        if self.scale_serial:
            try:
                self.scale_serial.close()
            except Exception:
                pass
            self.scale_serial = None
        self.scale_simulating = True
        self._start_scale_simulation()

    @staticmethod
    def _parse_weight(raw):
        """저울 출력 문자열에서 숫자만 추출 (예: '  12.34 g' → 12.34)"""
        import re
        m = re.search(r'[\d]+\.[\d]+', raw)
        if m:
            return float(m.group())
        m = re.search(r'[\d]+', raw)
        if m:
            return float(m.group())
        return None

    def save_weight(self):
        """현재 무게를 로그에 저장하고 CSV 파일에 기록"""
        with self.scale_lock:
            w = self.scale_weight
        if w is None:
            messagebox.showwarning("저울", "수신된 무게값이 없습니다.")
            return

        meas_count = len(self.measurements)
        entry = {
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'weight_g': w,
            'measurements': meas_count,
            'source': '시뮬레이션' if self.scale_simulating else self.scale_com_port
        }
        self.weight_log.append(entry)

        # CSV 저장 (프로젝트 폴더에 자동 기록)
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'weight_log.csv')
        write_header = not os.path.exists(csv_path)
        try:
            with open(csv_path, 'a', encoding='utf-8-sig') as f:
                if write_header:
                    f.write("시간,무게(g),측정개수,출처\n")
                f.write(f"{entry['time']},{entry['weight_g']},{entry['measurements']},{entry['source']}\n")
            messagebox.showinfo("무게 저장", f"저장 완료\n무게: {w:.2f} g\n파일: weight_log.csv")
        except Exception as ex:
            messagebox.showerror("저장 실패", str(ex))

    # ──────────────────────────────────────────────
    # 카메라
    # ──────────────────────────────────────────────
    def setup_camera(self):
        ret, frame = self.cap.read()
        if ret:
            self.cam_h, self.cam_w = frame.shape[:2]
            self.cam_display_h = int(self.cam_h * (self.view_w / self.cam_w))
            self.view_h = max(900, self.cam_display_h)
            self.cam_y_offset = (self.view_h - self.cam_display_h) // 2

    def auto_scan_and_connect(self, start_idx):
        for i in range(start_idx, start_idx + 6):
            idx = i % 6
            tmp_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if tmp_cap.isOpened():
                tmp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                tmp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                time.sleep(0.8)
                ret, frame = tmp_cap.read()
                if ret and frame is not None:
                    self.current_cam_idx = idx
                    return tmp_cap
            tmp_cap.release()
        return None

    def switch_camera(self):
        old_cap = self.cap
        new_cap = self.auto_scan_and_connect(self.current_cam_idx + 1)
        if new_cap:
            old_cap.release()
            self.cap = new_cap
            self.setup_camera()
            self.is_frozen = False

    # ──────────────────────────────────────────────
    # DXF 로딩 (핵심 수정 부분)
    # ──────────────────────────────────────────────
    def load_dxf_action(self, path):
        if not path or not os.path.exists(path):
            messagebox.showerror("오류", f"파일을 찾을 수 없습니다:\n{path}")
            return
        try:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            contours = []   # (ctype, np.array)
            all_pts = []

            # ── 1. LWPOLYLINE ──────────────────────
            for e in msp.query('LWPOLYLINE'):
                try:
                    pts = np.array(e.get_points('xy'), dtype=np.float32)
                    if len(pts) >= 2:
                        closed = e.closed
                        contours.append(('poly' if closed else 'line', pts))
                        all_pts.extend(pts.tolist())
                except Exception:
                    pass

            # ── 2. POLYLINE (구형 폴리라인) ────────
            for e in msp.query('POLYLINE'):
                try:
                    pts = np.array([[v.dxf.location.x, v.dxf.location.y]
                                    for v in e.vertices], dtype=np.float32)
                    if len(pts) >= 2:
                        contours.append(('poly' if e.is_closed else 'line', pts))
                        all_pts.extend(pts.tolist())
                except Exception:
                    pass

            # ── 3. LINE ───────────────────────────
            for e in msp.query('LINE'):
                try:
                    pts = np.array([
                        [e.dxf.start.x, e.dxf.start.y],
                        [e.dxf.end.x,   e.dxf.end.y]
                    ], dtype=np.float32)
                    contours.append(('line', pts))
                    all_pts.extend(pts.tolist())
                except Exception:
                    pass

            # ── 4. CIRCLE ─────────────────────────
            for e in msp.query('CIRCLE'):
                try:
                    cx, cy = e.dxf.center.x, e.dxf.center.y
                    r = e.dxf.radius
                    n = max(72, int(r * 4))          # 반경에 비례해 점 수 조절
                    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
                    pts = np.column_stack([
                        cx + r * np.cos(angles),
                        cy + r * np.sin(angles)
                    ]).astype(np.float32)
                    contours.append(('poly', pts))
                    all_pts.extend([[cx + r, cy], [cx - r, cy],
                                    [cx, cy + r], [cx, cy - r]])
                except Exception:
                    pass

            # ── 5. ARC ────────────────────────────
            for e in msp.query('ARC'):
                try:
                    cx, cy = e.dxf.center.x, e.dxf.center.y
                    r = e.dxf.radius
                    a1 = np.radians(e.dxf.start_angle)
                    a2 = np.radians(e.dxf.end_angle)
                    if a2 <= a1:
                        a2 += 2 * np.pi
                    n = max(12, int(np.degrees(a2 - a1) / 3))
                    angles = np.linspace(a1, a2, n)
                    pts = np.column_stack([
                        cx + r * np.cos(angles),
                        cy + r * np.sin(angles)
                    ]).astype(np.float32)
                    contours.append(('line', pts))
                    all_pts.extend(pts.tolist())
                except Exception:
                    pass

            # ── 6. ELLIPSE ────────────────────────
            for e in msp.query('ELLIPSE'):
                try:
                    cx, cy = e.dxf.center.x, e.dxf.center.y
                    major = np.array([e.dxf.major_axis.x, e.dxf.major_axis.y])
                    ratio = e.dxf.ratio
                    a1 = e.dxf.start_param
                    a2 = e.dxf.end_param
                    if a2 <= a1:
                        a2 += 2 * np.pi
                    n = max(72, int(np.degrees(a2 - a1) / 3))
                    t = np.linspace(a1, a2, n)
                    major_len = np.linalg.norm(major)
                    major_angle = np.arctan2(major[1], major[0])
                    px = cx + major_len * np.cos(t) * np.cos(major_angle) \
                             - major_len * ratio * np.sin(t) * np.sin(major_angle)
                    py = cy + major_len * np.cos(t) * np.sin(major_angle) \
                             + major_len * ratio * np.sin(t) * np.cos(major_angle)
                    pts = np.column_stack([px, py]).astype(np.float32)
                    contours.append(('line', pts))
                    all_pts.extend(pts.tolist())
                except Exception:
                    pass

            # ── 7. SPLINE ─────────────────────────
            for e in msp.query('SPLINE'):
                try:
                    # ezdxf 0.18+ : flattening 으로 근사 폴리라인 추출
                    pts = np.array([[p[0], p[1]] for p in e.flattening(0.1)],
                                   dtype=np.float32)
                    if len(pts) >= 2:
                        contours.append(('line', pts))
                        all_pts.extend(pts.tolist())
                except Exception:
                    pass

            # ── 결과 확인 ─────────────────────────
            if not all_pts:
                entity_types = set(e.dxftype() for e in msp)
                messagebox.showwarning(
                    "DXF 경고",
                    f"읽을 수 있는 도형이 없습니다.\n\n"
                    f"파일에 포함된 엔티티 타입:\n{', '.join(sorted(entity_types))}\n\n"
                    f"지원: LINE, CIRCLE, ARC, ELLIPSE,\n"
                    f"LWPOLYLINE, POLYLINE, SPLINE"
                )
                return

            all_pts = np.array(all_pts, dtype=np.float32)
            center = np.mean(all_pts, axis=0)

            self.dxf_contours = [(ctype, pts - center) for ctype, pts in contours]
            self.dxf_real_width = (np.max(all_pts[:, 0]) - np.min(all_pts[:, 0]))

            if self.scale <= 1.1 and self.dxf_real_width > 0:
                self.scale = (self.cam_w * 0.5) / self.dxf_real_width

            # 로드 성공 메시지 (엔티티 수 표시)
            counts = {}
            for e in msp:
                t = e.dxftype()
                counts[t] = counts.get(t, 0) + 1
            summary = '\n'.join(f"  {k}: {v}개" for k, v in sorted(counts.items()))
            messagebox.showinfo(
                "DXF 로드 완료",
                f"도형 {len(contours)}개 로드됨\n\n엔티티 구성:\n{summary}"
            )

        except ezdxf.DXFStructureError as ex:
            messagebox.showerror("DXF 오류", f"DXF 파일 구조 오류:\n{ex}")
        except Exception as ex:
            messagebox.showerror("DXF 오류", f"도면 로드 실패:\n{ex}")

    # ──────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────
    def init_buttons(self):
        btn_h = 30
        margin_x = 8
        margin_y = 5
        section_h = 18
        section_gap = 8
        start_y = 65

        col_w = (self.ui_w - 30 - margin_x) // 2
        max_button_y = self.view_h - self.bottom_area_height - 10

        y = start_y

        for section in self.button_sections:
            if y + section_h < max_button_y:
                self.section_headers[section['title']] = y
                y += section_h
            else:
                break

            for row in section['buttons']:
                if y + btn_h > max_button_y:
                    break
                for col_idx, btn in enumerate(row):
                    x1 = self.view_w + 15 + col_idx * (col_w + margin_x)
                    x2 = x1 + col_w
                    self.buttons[btn] = (x1, y, x2, y + btn_h)
                y += btn_h + margin_y

            y += section_gap

    def draw_ui(self, display_img):
        # 배경
        cv2.rectangle(display_img, (self.view_w, 0), (self.total_w, self.view_h), self.clr_bg, -1)
        cv2.line(display_img, (self.view_w, 0), (self.view_w, self.view_h), self.clr_border, 2)

        # 상단 타이틀
        cv2.rectangle(display_img, (self.view_w, 0), (self.total_w, 50), self.clr_section, -1)

        # 버튼
        for mode, (x1, y1, x2, y2) in self.buttons.items():
            is_active  = (mode == self.current_mode)
            is_hovered = (mode == self.hovered_button)
            is_pressed = (mode == self.pressed_button)

            if is_pressed:
                btn_clr, border_clr = self.clr_pressed, self.clr_primary
            elif is_active:
                btn_clr, border_clr = self.clr_active, self.clr_active
            elif is_hovered:
                btn_clr, border_clr = self.clr_hover, self.clr_hover
            else:
                btn_clr, border_clr = self.clr_panel, self.clr_border

            cv2.rectangle(display_img, (x1, y1), (x2, y2), btn_clr, -1)
            cv2.rectangle(display_img, (x1, y1), (x2, y2), border_clr, 1)
            if is_active:
                cv2.rectangle(display_img, (x1, y1), (x1 + 4, y2), (76, 255, 153), -1)

        # 하단 영역
        bottom_start_y = self.view_h - self.bottom_area_height
        cv2.rectangle(display_img, (self.view_w, bottom_start_y), (self.total_w, self.view_h), self.clr_section, -1)
        cv2.line(display_img, (self.view_w, bottom_start_y), (self.total_w, bottom_start_y), self.clr_border, 2)

        # 확대경
        mag_size = 150
        mag_y1 = bottom_start_y + 20
        mag_y2 = mag_y1 + mag_size
        mag_x1 = self.view_w + (self.ui_w - mag_size) // 2
        mag_x2 = mag_x1 + mag_size

        cv2.rectangle(display_img, (mag_x1 - 2, mag_y1 - 2), (mag_x2 + 2, mag_y2 + 2), self.clr_border, 2)
        cv2.rectangle(display_img, (mag_x1 - 1, mag_y1 - 1), (mag_x2 + 1, mag_y2 + 1), self.clr_bg, 1)

        if (self.curr_mx < self.view_w and
                self.cam_y_offset <= self.curr_my < self.cam_y_offset + self.cam_display_h and
                self.last_full_canvas is not None):
            w_ratio = self.cam_w / self.view_w
            rx = int(self.curr_mx * w_ratio)
            ry = int((self.curr_my - self.cam_y_offset) * w_ratio)
            roi_s = 30
            y1c, y2c = max(0, ry - roi_s), min(self.cam_h, ry + roi_s)
            x1c, x2c = max(0, rx - roi_s), min(self.cam_w, rx + roi_s)
            if y2c > y1c and x2c > x1c:
                roi = self.last_full_canvas[y1c:y2c, x1c:x2c]
                roi_res = cv2.resize(roi, (mag_size, mag_size), interpolation=cv2.INTER_NEAREST)
                display_img[mag_y1:mag_y2, mag_x1:mag_x2] = roi_res
                cx_m = mag_x1 + mag_size // 2
                cy_m = mag_y1 + mag_size // 2
                cv2.line(display_img, (cx_m, mag_y1), (cx_m, mag_y2), (0, 255, 0), 1)
                cv2.line(display_img, (mag_x1, cy_m), (mag_x2, cy_m), (0, 255, 0), 1)

        # PIL 텍스트
        img_pil = Image.fromarray(display_img)
        draw = ImageDraw.Draw(img_pil)

        try:
            font_title  = ImageFont.truetype("malgunbd.ttf", 16)
            font_section = ImageFont.truetype("malgun.ttf",  10)
            font_btn    = ImageFont.truetype("malgun.ttf",   10)
            font_status = ImageFont.truetype("malgun.ttf",    9)
        except Exception:
            font_title = font_section = font_btn = font_status = ImageFont.load_default()

        draw.text((self.view_w + 20, 12), "VISION MEASUREMENT", font=font_title,  fill=(204, 122, 0))
        draw.text((self.view_w + 20, 32), "SYSTEM v2.1",        font=font_status, fill=self.clr_text_dim)

        for title, y_pos in self.section_headers.items():
            draw.text((self.view_w + 20, y_pos + 2), title, font=font_section, fill=self.clr_text_dim)

        for mode, (x1, y1, x2, y2) in self.buttons.items():
            is_active  = (mode == self.current_mode)
            is_hovered = (mode == self.hovered_button)
            is_pressed = (mode == self.pressed_button)
            if is_pressed or is_active or is_hovered:
                txt_fill = (241, 241, 241)
            else:
                txt_fill = (200, 200, 200)
            label = self.btn_labels.get(mode, mode)
            draw.text((x1 + 8, y1 + 8), label, font=font_btn, fill=txt_fill)

        # ── 상태 텍스트 ───────────────────────────
        status_texts = [
            f"모드: {self.btn_labels.get(self.current_mode, self.current_mode)}",
            f"배율: {self.scale:.2f}x",
            f"회전: {self.angle:.1f}°",
            f"측정: {len(self.measurements)}개",
            f"카메라: {self.current_cam_idx}",
            f"상태: {'정지' if self.is_frozen else '라이브'}",
            f"도형: {len(self.dxf_contours)}개",
            f"저장: {len(self.weight_log)}건",
        ]
        y_pos = mag_y2 + 15
        for i, text in enumerate(status_texts):
            col = self.view_w + 20 if i % 2 == 0 else self.view_w + 180
            draw.text((col, y_pos), text, font=font_status, fill=self.clr_text_dim)
            if i % 2 == 1:
                y_pos += 16

        return np.array(img_pil)

    def draw_crosshair(self, canvas):
        """십자선 오버레이 - CROSS 모드일 때 캔버스에 그림"""
        if self.current_mode != 'CROSS':
            return canvas
        if self.cross_pos is None:
            self.cross_pos = (canvas.shape[1] // 2, canvas.shape[0] // 2)
        cx, cy = int(self.cross_pos[0]), int(self.cross_pos[1])
        h, w = canvas.shape[:2]

        # 전체 폭/높이 십자선
        cv2.line(canvas, (0, cy), (w, cy), (0, 255, 255), 1)
        cv2.line(canvas, (cx, 0), (cx, h), (0, 255, 255), 1)

        # 중심 원
        cv2.circle(canvas, (cx, cy), 12, (0, 255, 255), 1)
        cv2.circle(canvas, (cx, cy), 2,  (0, 255, 255), -1)

        # 좌표 표시
        img_pil = Image.fromarray(canvas)
        draw = ImageDraw.Draw(img_pil)
        try:
            font = ImageFont.truetype("malgun.ttf", 10)
        except Exception:
            font = ImageFont.load_default()
        draw.text((cx + 16, cy - 18), f"({cx}, {cy})", font=font, fill=(0, 255, 255))
        canvas[:] = np.array(img_pil)
        return canvas

    def draw_weight_overlay(self, canvas):
        """카메라 영상 우하단에 무게값 오버레이 (저장 이미지에도 포함됨)"""
        with self.scale_lock:
            w_val = self.scale_weight

        weight_str = f"{w_val:.2f} g" if w_val is not None else "-- g"

        if self.scale_connected:
            border_rgb = (0, 200, 100)
            tag = self.scale_com_port or "COM"
        elif self.scale_simulating:
            border_rgb = (255, 180, 0)
            tag = "SIM"
        else:
            border_rgb = (120, 120, 120)
            tag = "---"

        # 박스 크기·위치 (카메라 원본 해상도 기준)
        box_w, box_h = 220, 70
        margin = 20
        bx1 = canvas.shape[1] - box_w - margin
        by1 = canvas.shape[0] - box_h - margin
        bx2 = bx1 + box_w
        by2 = by1 + box_h

        # 반투명 배경 (알파 블렌딩)
        overlay = canvas.copy()
        cv2.rectangle(overlay, (bx1, by1), (bx2, by2), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.65, canvas, 0.35, 0, canvas)

        # 테두리
        b = border_rgb
        cv2.rectangle(canvas, (bx1, by1), (bx2, by2), (b[2], b[1], b[0]), 2)

        # PIL로 한글 텍스트
        img_pil = Image.fromarray(canvas)
        draw = ImageDraw.Draw(img_pil)
        try:
            font_large = ImageFont.truetype("malgunbd.ttf", 26)
            font_small = ImageFont.truetype("malgun.ttf",   11)
        except Exception:
            font_large = font_small = ImageFont.load_default()

        draw.text((bx1 + 10, by1 + 6),  "정밀저울",  font=font_small, fill=(180, 180, 180))
        draw.text((bx2 - 40, by1 + 6),  tag,         font=font_small, fill=border_rgb)
        draw.text((bx1 + 10, by1 + 24), weight_str,  font=font_large, fill=(255, 220, 60))
        draw.text((bx1 + 10, by1 + 54), f"저장 {len(self.weight_log)}건",
                  font=font_small, fill=(140, 140, 140))

        canvas[:] = np.array(img_pil)
        return canvas

    # ──────────────────────────────────────────────
    # 마우스
    # ──────────────────────────────────────────────
    def mouse_callback(self, event, x, y, flags, param):
        self.curr_mx, self.curr_my = x, y

        self.hovered_button = None
        if x > self.view_w:
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    self.hovered_button = m
                    break

        if event == cv2.EVENT_LBUTTONDOWN and x > self.view_w:
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    self.pressed_button = m
                    self._handle_button(m)
                    return

        if event == cv2.EVENT_LBUTTONUP:
            self.pressed_button = None

        w_ratio = self.cam_w / self.view_w
        rx = x * w_ratio
        ry = (y - self.cam_y_offset) * w_ratio

        if flags & cv2.EVENT_FLAG_SHIFTKEY:
            if self.measure_p1:
                if abs(rx - self.measure_p1[0]) > abs(ry - self.measure_p1[1]):
                    ry = self.measure_p1[1]
                else:
                    rx = self.measure_p1[0]
            elif self.calib_p1:
                if abs(rx - self.calib_p1[0]) > abs(ry - self.calib_p1[1]):
                    ry = self.calib_p1[1]
                else:
                    rx = self.calib_p1[0]

        if event == cv2.EVENT_LBUTTONDOWN and x <= self.view_w:
            if self.current_mode in ['PAN', 'ZOOM', 'ROTATE']:
                self.is_dragging = True
                self.lmx, self.lmy = x, y
                return
            if self.current_mode == 'CROSS':
                self.cross_pos = (rx, ry)
                self.is_dragging = True
                self.lmx, self.lmy = x, y
                return

            if 'MEAS' in self.current_mode:
                if self.measure_p1 is None:
                    self.measure_p1 = (rx, ry)
                elif self.measure_p2 is None:
                    self.measure_p2 = (rx, ry)
                    p1, p2 = np.array(self.measure_p1), np.array(self.measure_p2)
                    if self.current_mode == 'MEAS_P2P':
                        self.measure_temp_val = np.linalg.norm(p1 - p2)
                    else:
                        self.measure_temp_val = max(abs(p1[0] - p2[0]), abs(p1[1] - p2[1]))
                else:
                    self.measurements.append((
                        self.measure_p1, self.measure_p2,
                        self.measure_temp_val / self.scale,
                        self.current_mode, (rx, ry)
                    ))
                    self.measure_p1 = None
                    self.measure_p2 = None

            elif self.current_mode == 'CALIB':
                if self.calib_temp_data:
                    p1, p2, val = self.calib_temp_data
                    self.fixed_calib_line = (p1, p2, val, (rx, ry))
                    self.calib_temp_data = None
                else:
                    self.is_dragging = True
                    self.lmx, self.lmy = x, y
                    self.calib_p1 = (rx, ry)
                    self.calib_p2 = (rx, ry)

        elif event == cv2.EVENT_MOUSEMOVE and self.is_dragging:
            if self.current_mode == 'CROSS':
                self.cross_pos = (rx, ry)
                self.lmx, self.lmy = x, y
            elif self.current_mode == 'CALIB':
                self.calib_p2 = (rx, ry)
            else:
                dx = (x - self.lmx) * w_ratio
                dy = (y - self.lmy) * w_ratio
                if self.current_mode == 'PAN':
                    self.offset_x += dx
                    self.offset_y += dy
                elif self.current_mode == 'ZOOM':
                    self.scale *= (1 - (y - self.lmy) * 0.005)
                    self.scale = max(0.01, self.scale)
                elif self.current_mode == 'ROTATE':
                    self.angle += (x - self.lmx) * 0.2
                self.lmx, self.lmy = x, y

        elif event == cv2.EVENT_LBUTTONUP:
            if self.is_dragging and self.current_mode == 'CALIB' and self.calib_p1:
                dist_px = np.linalg.norm(np.array(self.calib_p1) - np.array([rx, ry]))
                if dist_px > 10:
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    val = simpledialog.askfloat("캘리브레이션", "실제 길이(mm)를 입력하세요:", parent=root)
                    root.destroy()
                    if val:
                        self.scale = dist_px / val
                        self.calib_temp_data = (self.calib_p1, (rx, ry), val)
            self.is_dragging = False
            self.calib_p1 = self.calib_p2 = None

    def _handle_button(self, m):
        """버튼 클릭 처리 분리"""
        if m == 'FREEZE_LIVE':
            if not self.is_frozen:
                ret, frame = self.cap.read()
                if ret:
                    self.frozen_frame = frame.copy()
                    self.is_frozen = True
            else:
                self.is_frozen = False

        elif m == 'SWITCH_CAM':
            self.switch_camera()

        elif m == 'DXF_COLOR':
            self.idx_dxf_color = (self.idx_dxf_color + 1) % len(self.color_palette)

        elif m == 'MEAS_COLOR':
            self.idx_meas_color = (self.idx_meas_color + 1) % len(self.color_palette)

        elif m == 'CALIB_COLOR':
            self.idx_calib_color = (self.idx_calib_color + 1) % len(self.color_palette)

        elif m == 'MEAS_UNDO':
            if self.measure_p2:
                self.measure_p2 = None
                self.measure_p1 = None
            elif self.measure_p1:
                self.measure_p1 = None
            elif self.measurements:
                self.measurements.pop()

        elif m == 'SAVE_IMG':
            if self.last_full_canvas is not None:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                path = filedialog.asksaveasfilename(
                    defaultextension=".jpg",
                    initialfile=f'검사결과_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg',
                    parent=root
                )
                if path:
                    try:
                        res, buffer = cv2.imencode('.jpg', self.last_full_canvas,
                                                   [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                        if res:
                            with open(path, "wb") as f:
                                f.write(buffer.tobytes())
                            messagebox.showinfo("저장 완료", "이미지가 성공적으로 저장되었습니다.", parent=root)
                    except Exception as ex:
                        messagebox.showerror("저장 실패", f"저장 오류:\n{ex}", parent=root)
                root.destroy()

        elif m == 'LOAD_DXF':
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(filetypes=[("DXF 도면", "*.dxf")], parent=root)
            root.destroy()
            if path:
                self.load_dxf_action(path)

        elif m == 'CLEAR':
            self.measurements = []
            self.measure_p1 = None
            self.measure_p2 = None
            self.fixed_calib_line = None
            self.calib_temp_data = None

        elif m == 'SCALE_CONNECT':
            if self.scale_connected:
                self.disconnect_scale()
                messagebox.showinfo("저울", "저울 연결을 해제했습니다.")
            else:
                if not SERIAL_AVAILABLE:
                    messagebox.showinfo(
                        "저울 연결",
                        "현재 시뮬레이션 모드입니다.\n\n"
                        "실제 저울 연결 방법:\n"
                        "1. pip install pyserial\n"
                        "2. 저울 RS-232 → USB 변환 케이블 연결\n"
                        "3. 장치관리자에서 COM 포트 확인\n"
                        "4. 이 버튼을 다시 클릭"
                    )
                else:
                    ports = [p.device for p in serial.tools.list_ports.comports()]
                    if not ports:
                        messagebox.showwarning("저울", "연결된 COM 포트가 없습니다.")
                        return
                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    port = simpledialog.askstring(
                        "저울 연결",
                        f"COM 포트를 입력하세요:\n사용 가능: {', '.join(ports)}",
                        parent=root
                    )
                    root.destroy()
                    if port:
                        self.connect_scale(port.strip().upper())

        elif m == 'SCALE_SAVE':
            self.save_weight()

        elif m == 'QUIT':
            self.is_running = False

        else:
            self.current_mode = m
            self.measure_p1 = None
            self.measure_p2 = None

    # ──────────────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────────────
    def run(self):
        cv2.namedWindow('Vision Inspector', cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback('Vision Inspector', self.mouse_callback)

        while self.is_running:
            if cv2.getWindowProperty('Vision Inspector', cv2.WND_PROP_VISIBLE) < 1:
                break

            frame = self.frozen_frame.copy() if self.is_frozen else None
            if not self.is_frozen:
                ret, frame = self.cap.read()
                if not ret:
                    continue

            canvas = frame.copy()
            rad = np.radians(self.angle)
            rot_m = np.array([
                [ np.cos(rad), -np.sin(rad)],
                [ np.sin(rad),  np.cos(rad)]
            ])

            dxf_clr   = self.color_palette[self.idx_dxf_color]
            meas_clr  = self.color_palette[self.idx_meas_color]
            calib_clr = self.color_palette[self.idx_calib_color]

            # ── DXF 렌더링 (ctype 구분) ──────────
            for ctype, pts in self.dxf_contours:
                pts_draw = (
                    (pts @ rot_m.T) * self.scale + [self.offset_x, self.offset_y]
                ).astype(np.int32)
                closed = (ctype == 'poly')
                cv2.polylines(canvas, [pts_draw], closed, dxf_clr, 1)

            # ── 캘리브 고정선 ─────────────────────
            if self.fixed_calib_line:
                p1, p2, val, pt = self.fixed_calib_line
                cv2.line(canvas, (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])), calib_clr, 1)
                cv2.putText(canvas, f"REF: {val:.1f}mm", (int(pt[0]), int(pt[1])),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, calib_clr, 1)

            # ── 측정선 ────────────────────────────
            for m1, m2, val, m_type, pt in self.measurements:
                p1 = (int(m1[0]), int(m1[1]))
                p2 = (int(m2[0]), int(m2[1]))
                if m_type == 'MEAS_HV':
                    if abs(p1[0] - p2[0]) > abs(p1[1] - p2[1]):
                        cv2.line(canvas, p1, (p2[0], p1[1]), meas_clr, 1)
                        p2 = (p2[0], p1[1])
                    else:
                        cv2.line(canvas, p1, (p1[0], p2[1]), meas_clr, 1)
                        p2 = (p1[0], p2[1])
                else:
                    cv2.line(canvas, p1, p2, meas_clr, 1)
                cv2.putText(canvas, f"{val:.3f}mm", (int(pt[0]), int(pt[1])),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, meas_clr, 1)

            w_ratio = self.cam_w / self.view_w

            # ── 드래그 마커 ───────────────────────
            if self.is_dragging and self.current_mode in ['PAN', 'ZOOM', 'ROTATE']:
                mx = int(self.curr_mx * w_ratio)
                my = int((self.curr_my - self.cam_y_offset) * w_ratio)
                cv2.drawMarker(canvas, (mx, my), (0, 255, 255),
                               markerType=cv2.MARKER_CROSS, markerSize=25, thickness=1)

            # ── 측정 임시 표시 ────────────────────
            if self.measure_p2:
                cx = int(self.curr_mx * w_ratio)
                cy = int((self.curr_my - self.cam_y_offset) * w_ratio)
                cv2.putText(canvas, f"{self.measure_temp_val / self.scale:.3f}mm",
                            (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, meas_clr, 1)
            elif self.measure_p1:
                cv2.circle(canvas, (int(self.measure_p1[0]), int(self.measure_p1[1])), 5, meas_clr, 1)

            # ── 캘리브 임시 표시 ──────────────────
            if self.calib_temp_data:
                cx = int(self.curr_mx * w_ratio)
                cy = int((self.curr_my - self.cam_y_offset) * w_ratio)
                cv2.putText(canvas, f"REF: {self.calib_temp_data[2]:.1f}mm",
                            (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, calib_clr, 1)
            elif self.calib_p1 and self.calib_p2:
                cv2.line(canvas,
                         (int(self.calib_p1[0]), int(self.calib_p1[1])),
                         (int(self.calib_p2[0]), int(self.calib_p2[1])),
                         calib_clr, 1)

            # ── 십자선 오버레이 ────────────────────
            canvas = self.draw_crosshair(canvas)

            # ── 무게 오버레이 (저장 이미지에도 포함) ──
            canvas = self.draw_weight_overlay(canvas)

            # ── 화면 출력 ─────────────────────────
            self.last_full_canvas = canvas.copy()
            res_view = cv2.resize(canvas, (self.view_w, self.cam_display_h))
            display_img = np.zeros((self.view_h, self.total_w, 3), dtype=np.uint8)
            display_img[self.cam_y_offset:self.cam_y_offset + self.cam_display_h, :self.view_w] = res_view
            display_img = self.draw_ui(display_img)

            cv2.imshow('Vision Inspector', display_img)
            if cv2.waitKey(1) == ord('q'):
                break

        self.cap.release()
        cv2.destroyAllWindows()
        sys.exit()


if __name__ == "__main__":
    inspector = VisionInspector()
    inspector.run()
