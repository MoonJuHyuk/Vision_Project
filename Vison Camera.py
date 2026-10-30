import cv2
import numpy as np
import ezdxf
import os
import sys
import threading
import subprocess
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import ImageFont, ImageDraw, Image

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


# ──────────────────────────────────────────────
# 제품 게이지 목록 (모든 PC 공통) - 제품명, 높이(mm)
# 제품을 추가·수정하려면 여기에 한 줄씩 적고 GitHub에 올리면 모든 PC에 적용됨
# ──────────────────────────────────────────────
PRODUCTS = [
    ("고스트1.88", 8.0),
    ("고스트2.5", 8.2),
]
GAUGE_BAR_MM = 3.0   # 제품 게이지 평행선 길이 (mm)


class VisionInspector:
    # ──────────────────────────────────────────────
    # 여러 도면: 도면마다 위치·회전·배율·색을 따로 가짐.
    # self.scale / angle / offset_x / offset_y / idx_dxf_color / dxf_contours 는
    # '지금 선택된 도면'의 값을 가리킴 (도면이 없으면 기본값 저장소 _nodwg)
    # ──────────────────────────────────────────────
    DXF_COLOR_ORDER = [0, 4, 2, 1, 3]   # 새 도면 색 순서: 초록, 흰색, 파랑, 빨강, 노랑

    def _dwg(self):
        if self.dxf_active is not None and 0 <= self.dxf_active < len(self.drawings):
            return self.drawings[self.dxf_active]
        return self._nodwg

    def _dwg_prop(key):
        return property(lambda self: self._dwg()[key],
                        lambda self, v: self._dwg().__setitem__(key, v))

    scale = _dwg_prop('scale')              # 도면 표시 배율 (px/mm) - 도면 확대/축소 시 바뀜
    angle = _dwg_prop('angle')
    offset_x = _dwg_prop('offset_x')        # 화면 중심 기준 이동량 (원본 px)
    offset_y = _dwg_prop('offset_y')
    idx_dxf_color = _dwg_prop('color_idx')
    dxf_contours = property(lambda self: self._dwg()['contours'])
    del _dwg_prop

    def __init__(self, dxf_path=""):
        self.dxf_path = dxf_path
        self.drawings = []        # [{'name', 'contours', 'scale', 'angle', 'offset_x', 'offset_y', 'color_idx'}]
        self.dxf_active = None    # 선택된 도면 번호
        self._nodwg = {'name': '', 'contours': [], 'scale': 1.0, 'angle': 0.0,
                       'offset_x': 0.0, 'offset_y': 0.0, 'color_idx': 0}
        self.current_cam_idx = 0
        self.cap = self.auto_scan_and_connect(0)
        if self.cap is None:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "카메라 오류",
                "사용 가능한 카메라를 찾을 수 없습니다.\n\n"
                "카메라 연결 상태를 확인하고, 다른 프로그램이 카메라를 사용 중이면 종료한 뒤 다시 실행하세요."
            )
            root.destroy()
            sys.exit()
        self.camera_switching = False
        self.pending_camera = None
        self.pending_cam_idx = None
        self.camera_lock = threading.Lock()
        self.camera_list_visible = False
        self.camera_names = []

        self.is_running = True
        self.is_frozen = False
        self.frozen_frame = None
        self.loaded_frame = None
        self.last_frame = None            # 마지막으로 화면에 표시한 원본 프레임 (경계 맞춤·저장용)
        self.edge_snap = True             # 측정/캘리브 점을 가까운 경계에 자동으로 맞춤
        self.view_dragging = False        # 오른쪽 버튼으로 영상 이동 중
        self._font_cache = {}
        self._modal = None                # 화면 안 입력판 (열려 있으면 다른 조작은 막음)
        self._modal_hits = []
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
        self.idx_cross_color = 3   # 기본 cyan

        self.hovered_button = None

        self.btn_labels = {
            'SWITCH_CAM': '카메라 전환',
            'FREEZE_LIVE': '정지 / 라이브',
            'LOAD_IMAGE': '사진 불러오기',
            'LOAD_DXF': '도면 추가',
            'DXF_COLOR': '도면 색상',
            'DXF_NEXT': '도면 선택 전환',
            'DXF_DEL': '도면 빼기',
            'PAN': '이동 (PAN)',
            'ZOOM': '확대 / 축소',
            'ZOOM_IN': '확대 +',
            'ZOOM_OUT': '축소 -',
            'ROTATE': '회전 (Angle)',
            'CROSS': '십자선',
            'CROSS_COLOR': '십자선 색상',
            'CROSS_UNDO': '십자선 취소',
            'DXF_REAL': '도면 실측크기',
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
            'CAM_SETTINGS': '카메라 설정',
            'VIEW_FIT': '화면 맞춤',
            'EDGE_SNAP': '경계 맞춤',
            'TPL_ADD': '도형 추가',
            'TPL_EDIT': '도형 치수/삭제',
            'TPL_GAUGE': '제품 게이지',
            'QUIT': '종료'
        }

        self.button_sections = [
            {
                'title': '카메라 제어',
                'buttons': [['SWITCH_CAM', 'FREEZE_LIVE'], ['LOAD_IMAGE', 'SAVE_IMG'],
                            ['CAM_SETTINGS', 'VIEW_FIT']]
            },
            {
                'title': '도면 관리',
                'buttons': [['LOAD_DXF', 'DXF_COLOR'], ['DXF_NEXT', 'DXF_DEL']]
            },
            {
                'title': '뷰 조작',
                'buttons': [
                    ['PAN', 'ZOOM'],
                    ['ZOOM_IN', 'ZOOM_OUT'],
                    ['ROTATE', 'CROSS'],
                    ['CROSS_COLOR', 'CROSS_UNDO'],
                    ['DXF_REAL', 'CLEAR']
                ]
            },
            {
                'title': '측정 도구',
                'buttons': [
                    ['MEAS_P2P', 'MEAS_HV'],
                    ['MEAS_COLOR', 'MEAS_UNDO'],
                    ['EDGE_SNAP']
                ]
            },
            {
                'title': '기준 도형',
                'buttons': [['TPL_ADD', 'TPL_EDIT'], ['TPL_GAUGE']]
            },
            {
                'title': '캘리브레이션',
                'buttons': [['CALIB', 'CALIB_COLOR']]
            },
            {
                'title': '정밀저울',
                'buttons': [['SCALE_CONNECT', 'SCALE_SAVE']]
            }
            # '종료' 버튼은 화면에 들어갈 자리가 없어 제외 (창 X 버튼 또는 q 키로 종료)
        ]

        self.current_mode = 'PAN'
        self.pressed_button = None
        self.buttons = {}
        self.section_headers = {}

        self.bottom_area_height = 224   # 확대경 + 상태 표시 영역

        # 카메라 설정 먼저 (view_h 정의)
        self.setup_camera()

        # 버튼 초기화
        self.init_buttons()

        self.offset_x, self.offset_y = 0, 0  # 캔버스 중심 기준 pan 델타
        self.scale = 1.0               # 도면 표시 배율 (px/mm) - 도면 확대/축소 시 바뀜
        self.calib_px_per_mm = None    # 측정 기준 (px/mm) - 캘리브레이션으로만 바뀜, None이면 미보정
        self.calib_notice = ""         # 캘리브레이션이 초기화된 이유 (화면 안내용)
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
        self.cross_preview_pos = None  # 마우스 미리보기 위치 (x, y)
        self.cross_angle = 0.0         # 십자선 회전 각도 (도)
        self.cross_size = 1.0          # 십자선 크기 배율
        self.crosshairs = []           # 배치된 십자선 리스트: [(x, y, angle, size), ...]
        self.cross_edit_idx = None     # 현재 조정 중인 십자선 인덱스
        self.cross_selected_idx = None # 선택된 십자선 인덱스
        # 기준 도형: [{'type': 'circle'|'rect'|'line', 'w': mm, 'h': mm, 'angle': 도, 'cx', 'cy': 원본 px}, ...]
        self.templates = []
        self.tpl_selected = None
        self.tpl_color = (255, 0, 255)  # 자홍색 (BGR)

        # 저울 관련 초기화
        self.scale_weight = None          # 현재 무게값 (g)
        self.scale_connected = False      # 실제 시리얼 연결 여부
        self.scale_com_port = None
        self.scale_serial = None
        self.scale_thread = None
        self.scale_error = None
        self.scale_lock = threading.Lock()
        self.weight_log = []              # 저장된 무게 기록

        if dxf_path:
            self.load_dxf_action(dxf_path)

    # ──────────────────────────────────────────────
    # 저울 (Scale)
    # ──────────────────────────────────────────────
    def connect_scale(self, port, baud=9600):
        """실제 RS-232 저울 연결 (pyserial 필요)"""
        if not SERIAL_AVAILABLE:
            self._message("오류", "pyserial이 설치되지 않았습니다.\n\npip install pyserial")
            return False
        try:
            ser = serial.Serial(port, baudrate=baud, bytesize=8,
                                parity='N', stopbits=1, timeout=1)
            self.scale_serial = ser
            self.scale_com_port = port
            self.scale_error = None
            self.scale_connected = True

            def _read_loop():
                import re
                buf = ""
                while self.scale_connected:
                    try:
                        chunk = ser.read(ser.in_waiting or 1).decode(
                            'ascii', errors='ignore'
                        )
                        if not chunk:
                            # 1초(timeout) 동안 새 데이터가 없으면 줄바꿈 없이 끝난 값으로 보고 처리.
                            # 전송 도중의 조각('+0012.3' 등)을 읽어 잘못된 값이 들어가는 것을 막음
                            if buf.strip():
                                val = self._parse_weight(buf.strip())
                                if val is not None:
                                    with self.scale_lock:
                                        self.scale_weight = val
                                buf = ""
                            continue
                        buf += chunk
                        frames = re.split(r'[\r\n]+', buf)
                        buf = frames.pop()
                        for frame in frames:
                            val = self._parse_weight(frame.strip())
                            if val is not None:
                                with self.scale_lock:
                                    self.scale_weight = val
                    except Exception as ex:
                        self.scale_error = str(ex)
                        self.scale_connected = False
                        # 연결이 끊긴 뒤 마지막 값이 실제 값처럼 저장되지 않도록 비움
                        with self.scale_lock:
                            self.scale_weight = None
                        break

            t = threading.Thread(target=_read_loop, daemon=True)
            t.start()
            self.scale_thread = t
            return True
        except Exception as ex:
            self.scale_error = str(ex)
            self._message("저울 연결 실패", str(ex))
            return False

    def disconnect_scale(self):
        self.scale_connected = False
        if self.scale_serial:
            try:
                self.scale_serial.close()
            except Exception:
                pass
            self.scale_serial = None
        with self.scale_lock:
            self.scale_weight = None

    @staticmethod
    def _parse_weight(raw):
        """저울 출력 문자열에서 숫자만 추출 (예: '  12.34 g' → 12.34)"""
        import re
        m = re.search(r'[-+]?\d+(?:[.,]\d+)?', raw)
        if m:
            return float(m.group().replace(',', '.'))
        return None

    def save_weight(self):
        """현재 무게를 로그에 저장하고 CSV 파일에 기록"""
        if not self.scale_connected:
            self._message("저울", "저울이 연결되어 있지 않습니다.\n'저울 연결'을 먼저 하세요.")
            return
        with self.scale_lock:
            w = self.scale_weight
        if w is None:
            self._message("저울", "수신된 무게값이 없습니다.")
            return

        meas_count = len(self.measurements)
        entry = {
            'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'weight_g': w,
            'measurements': meas_count,
            'source': self.scale_com_port
        }
        self.weight_log.append(entry)

        # CSV 저장 (실행한 파일과 같은 폴더에 자동 기록)
        # 런처/exe로 실행하면 __file__이 없으므로 실행 파일 경로(sys.argv[0]) 기준으로 저장
        csv_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'weight_log.csv')
        write_header = not os.path.exists(csv_path)
        try:
            with open(csv_path, 'a', encoding='utf-8-sig') as f:
                if write_header:
                    f.write("시간,무게(g),측정개수,출처\n")
                f.write(f"{entry['time']},{entry['weight_g']},{entry['measurements']},{entry['source']}\n")
            self._message("무게 저장", f"저장 완료\n무게: {w:.2f} g\n파일: weight_log.csv")
        except Exception as ex:
            self._message("저장 실패", str(ex))

    # ──────────────────────────────────────────────
    # 카메라
    # ──────────────────────────────────────────────
    def setup_camera(self):
        # 안정된 해상도를 얻기 위해 여러 프레임 읽기
        w, h = 0, 0
        for _ in range(3):
            ret, frame = self.cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
        if w == 0 or h == 0:
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.cam_w, self.cam_h = w, h
        self.cam_display_h = int(self.cam_h * (self.view_w / self.cam_w))
        self.view_h = max(900, self.cam_display_h)
        self.cam_y_offset = (self.view_h - self.cam_display_h) // 2
        self.frame_w, self.frame_h = w, h
        self._reset_view()

    def auto_scan_and_connect(self, start_idx):
        for i in range(start_idx, start_idx + 6):
            idx = i % 6
            tmp_cap = self._open_camera(idx)
            if tmp_cap is not None:
                self.current_cam_idx = idx
                return tmp_cap
        return None

    MIN_LIVE_FPS = 8   # 최대 해상도에서 이보다 느리면 1920x1080으로 되돌림

    @classmethod
    def _open_camera(cls, idx):
        """카메라를 열고 가장 높은 해상도로 설정한 뒤 첫 프레임이 정상인지 확인"""
        tmp_cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if not tmp_cap.isOpened():
            tmp_cap.release()
            return None
        cls._apply_max_resolution(tmp_cap)
        ret, frame = tmp_cap.read()
        if ret and frame is not None:
            return tmp_cap
        tmp_cap.release()
        return None

    @classmethod
    def _apply_max_resolution(cls, cap):
        """지원하지 않는 큰 값(9999x9999)을 요청하면 OpenCV(DirectShow)가 카메라가 지원하는
        가장 큰 해상도를 골라줌. 해상도 변경은 한 번에 수 초가 걸려 여러 번 시도하지 않음"""
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 9999)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 9999)
        ret, frame = cap.read()
        if ret and frame is not None and frame.shape[1] <= 1920:
            return
        # 최대 해상도에서 영상이 너무 느리거나 안 나오면 1920x1080으로
        if ret and frame is not None:
            import time
            t0 = time.time()
            n = 0
            while n < 5 and cap.read()[0]:
                n += 1
            fps = n / max(time.time() - t0, 1e-3)
            if n == 5 and fps >= cls.MIN_LIVE_FPS:
                return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    @staticmethod
    def _get_camera_names():
        """Windows 장치 관리자에서 카메라 이름을 가져옴"""
        try:
            command = (
                "Get-PnpDevice -Class Camera -Status OK "
                "| Select-Object -ExpandProperty FriendlyName"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            return []

    def switch_camera(self):
        self.camera_names = self._get_camera_names()
        self.camera_list_visible = not self.camera_list_visible

    def _camera_list_bounds(self, idx):
        row_y = 82 + idx * 34
        return (self.view_w + 12, row_y, self.total_w - 12, row_y + 28)

    def _draw_camera_list(self, display_img, draw, font_section, font_status):
        draw.text((self.view_w + 20, 68), "카메라 선택", font=font_section,
                  fill=self.clr_text)
        draw.text((self.view_w + 20, 84), "항목을 클릭하면 전환합니다", font=font_status,
                  fill=self.clr_text_dim)

        for idx in range(6):
            x1, y1, x2, y2 = self._camera_list_bounds(idx)
            is_current = idx == self.current_cam_idx
            is_hovered = (self.view_w + 12 <= self.curr_mx <= self.total_w - 12 and
                          y1 <= self.curr_my <= y2)
            if is_current:
                fill = self.clr_active
            elif is_hovered:
                fill = self.clr_hover
            else:
                fill = self.clr_panel
            draw.rectangle((x1, y1, x2, y2), fill=fill, outline=self.clr_border, width=1)

            label = self.camera_names[idx] if idx < len(self.camera_names) else "장치명 확인 불가"
            label = label[:30]
            suffix = "  [현재]" if is_current else ""
            draw.text((x1 + 8, y1 + 7), f"카메라 {idx}: {label}{suffix}",
                      font=font_status, fill=self.clr_text)

    def _start_camera_switch(self, target_idx):
        with self.camera_lock:
            if self.camera_switching:
                return
            self.camera_switching = True
            old_cap = self.cap
            old_idx = self.current_cam_idx
            self.cap = None

        if old_cap is not None:
            old_cap.release()

        def _find_camera():
            new_cap = self._open_camera(target_idx)
            selected_idx = target_idx
            if new_cap is None:
                new_cap = self._open_camera(old_idx)
                selected_idx = old_idx if new_cap is not None else None
            with self.camera_lock:
                self.pending_camera = new_cap
                self.pending_cam_idx = selected_idx
                self.camera_switching = False

        threading.Thread(target=_find_camera, daemon=True).start()

    def _apply_pending_camera(self):
        with self.camera_lock:
            new_cap = self.pending_camera
            cam_idx = self.pending_cam_idx
            self.pending_camera = None
            self.pending_cam_idx = None

        if new_cap is None:
            return

        old_cap = self.cap
        self.cap = new_cap
        self.current_cam_idx = cam_idx
        if old_cap is not None:
            old_cap.release()
        self.setup_camera()
        self.is_frozen = False
        self.loaded_frame = None
        self._reset_calibration("카메라가 바뀌어")

    def load_image_action(self):
        root = self._dialog_root()
        path = filedialog.askopenfilename(
            filetypes=[("이미지 파일", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff"),
                       ("모든 파일", "*.*")],
            parent=root
        )
        root.destroy()
        if not path:
            return

        try:
            image_data = np.fromfile(path, dtype=np.uint8)
            image = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
        except (OSError, ValueError, cv2.error):
            image = None
        if image is None:
            self._message("사진 불러오기 실패", "이미지 파일을 읽을 수 없습니다.")
            return

        # 화면(카메라) 비율과 다른 사진은 가로·세로가 다르게 늘어나 보이므로 검은 여백을 붙여 비율을 맞춤
        h, w = image.shape[:2]
        target_ratio = self.cam_w / max(1, self.cam_h)
        if w / h > target_ratio:
            pad = int(round(w / target_ratio)) - h
            image = cv2.copyMakeBorder(image, pad // 2, pad - pad // 2, 0, 0,
                                       cv2.BORDER_CONSTANT, value=(0, 0, 0))
        elif w / h < target_ratio:
            pad = int(round(h * target_ratio)) - w
            image = cv2.copyMakeBorder(image, 0, 0, pad // 2, pad - pad // 2,
                                       cv2.BORDER_CONSTANT, value=(0, 0, 0))

        self.loaded_frame = image
        self.is_frozen = False
        self.frozen_frame = None
        self._reset_calibration("사진을 불러와")

    # ──────────────────────────────────────────────
    # DXF 로딩 (핵심 수정 부분)
    # ──────────────────────────────────────────────
    def load_dxf_action(self, path):
        if not path or not os.path.exists(path):
            self._message("오류", f"파일을 찾을 수 없습니다:\n{path}")
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
                self._message(
                    "DXF 경고",
                    f"읽을 수 있는 도형이 없습니다.\n\n"
                    f"파일에 포함된 엔티티 타입:\n{', '.join(sorted(entity_types))}\n\n"
                    f"지원: LINE, CIRCLE, ARC, ELLIPSE,\n"
                    f"LWPOLYLINE, POLYLINE, SPLINE"
                )
                return

            all_pts = np.array(all_pts, dtype=np.float32)
            center = np.mean(all_pts, axis=0)

            real_width = float(np.max(all_pts[:, 0]) - np.min(all_pts[:, 0]))
            if self.calib_px_per_mm:
                # 캘리브레이션이 되어 있으면 도면을 실제 치수 크기로 표시
                scale = self.calib_px_per_mm
            elif self.drawings:
                # 미보정: 이미 불러온 도면과 같은 배율로 (도면끼리 크기 비교가 맞도록)
                scale = self.scale
            else:
                # 미보정 첫 도면: 모양 확인용으로 영상 폭의 40% 크기로 표시
                scale = (self.frame_w * 0.4) / real_width if real_width > 0 else 1.0

            # 새 도면은 지금 보고 있는 화면 가운데에 추가 (기존 도면은 그대로 둠)
            self.drawings.append({
                'name': os.path.basename(path),
                # AutoCAD Y축(위=+) → 화면 Y축(아래=+) 변환을 위해 Y 반전
                'contours': [(ctype, (pts - center) * [1, -1]) for ctype, pts in contours],
                'scale': scale,
                'angle': 0.0,
                'offset_x': self.view_cx - self.frame_w / 2.0,
                'offset_y': self.view_cy - self.frame_h / 2.0,
                'color_idx': self.DXF_COLOR_ORDER[len(self.drawings) % len(self.DXF_COLOR_ORDER)],
            })
            self.dxf_active = len(self.drawings) - 1

            # 로드 성공 메시지 (엔티티 수 표시)
            counts = {}
            for e in msp:
                t = e.dxftype()
                counts[t] = counts.get(t, 0) + 1
            summary = '\n'.join(f"  {k}: {v}개" for k, v in sorted(counts.items()))
            self._message(
                "DXF 로드 완료",
                f"도면 {len(self.drawings)}: {os.path.basename(path)}\n"
                f"도형 {len(contours)}개 로드됨\n\n엔티티 구성:\n{summary}"
            )

        except ezdxf.DXFStructureError as ex:
            self._message("DXF 오류", f"DXF 파일 구조 오류:\n{ex}")
        except Exception as ex:
            self._message("DXF 오류", f"도면 로드 실패:\n{ex}")

    # ──────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────
    def init_buttons(self):
        # 버튼이 모두 들어가도록 간격을 촘촘하게 (높이 900 기준 하단 확대경 영역 위까지)
        btn_h = 24
        margin_x = 8
        margin_y = 3
        section_h = 16
        section_gap = 4
        start_y = 68

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
            is_active  = (mode == self.current_mode) or (mode == 'EDGE_SNAP' and self.edge_snap)
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

        # 확대경 (마우스 주변 원본 픽셀을 그대로 크게 보여줌)
        mag_size = 120
        mag_y1 = bottom_start_y + 14
        mag_y2 = mag_y1 + mag_size
        mag_x1 = self.view_w + (self.ui_w - mag_size) // 2
        mag_x2 = mag_x1 + mag_size

        cv2.rectangle(display_img, (mag_x1 - 2, mag_y1 - 2), (mag_x2 + 2, mag_y2 + 2), self.clr_border, 2)
        cv2.rectangle(display_img, (mag_x1 - 1, mag_y1 - 1), (mag_x2 + 1, mag_y2 + 1), self.clr_bg, 1)

        if (self.curr_mx < self.view_w and
                self.cam_y_offset <= self.curr_my < self.cam_y_offset + self.cam_display_h and
                self.last_frame is not None):
            fx, fy = self._s2f(self.curr_mx, self.curr_my)
            # 확대경 배율: 큰 화면보다 항상 3배 더 크게 (최소 원본 1픽셀 = 4.7픽셀, 최대 40픽셀)
            k = float(np.clip(3.0 * self._view_scale(), mag_size / 30.0, 40.0))
            m = np.array([[k, 0, mag_size / 2.0 - fx * k],
                          [0, k, mag_size / 2.0 - fy * k]])
            mag_img = cv2.warpAffine(
                self.last_frame, m, (mag_size, mag_size), flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
            # 십자선·측정선·도면·경계 맞춤 미리보기도 큰 화면과 똑같이 표시
            to_mag = lambda p: (np.asarray(p, dtype=np.float64) - [fx, fy]) * k + [mag_size / 2.0, mag_size / 2.0]
            self._draw_overlays(mag_img, to_mag, unit=1.0, live=True)
            display_img[mag_y1:mag_y2, mag_x1:mag_x2] = mag_img
            # 마우스 위치 표시: 십자선과 헷갈리지 않고 가운데를 가리지 않도록 가장자리 짧은 표시만
            cx_m = mag_x1 + mag_size // 2
            cy_m = mag_y1 + mag_size // 2
            tick, gap = 18, 6
            for (ax, ay, bx, by) in ((cx_m, mag_y1, cx_m, mag_y1 + tick), (cx_m, mag_y2 - tick, cx_m, mag_y2),
                                     (mag_x1, cy_m, mag_x1 + tick, cy_m), (mag_x2 - tick, cy_m, mag_x2, cy_m),
                                     (cx_m - gap, cy_m, cx_m - 2, cy_m), (cx_m + 2, cy_m, cx_m + gap, cy_m)):
                cv2.line(display_img, (ax, ay), (bx, by), (0, 255, 0), 1)

        # PIL 텍스트
        img_pil = Image.fromarray(display_img)
        draw = ImageDraw.Draw(img_pil)

        font_title = self._font("malgunbd.ttf", 16)
        font_section = self._font("malgun.ttf", 10)
        font_btn = self._font("malgun.ttf", 10)
        font_status = self._font("malgun.ttf", 9)
        font_section_warn = self._font("malgunbd.ttf", 14)

        draw.text((self.view_w + 20, 8), "VISION MEASUREMENT", font=font_title,  fill=(204, 122, 0))
        draw.text((self.view_w + 20, 28), "SYSTEM v2.2",        font=font_status, fill=self.clr_text_dim)

        # 측정 기준(캘리브레이션), 분해능, 도면 배율 상태를 상단에 표시
        if self.calib_px_per_mm:
            calib_label = f"보정: {self.calib_px_per_mm:.3f} px/mm"
            calib_fill = (255, 214, 0)
        else:
            calib_label = "미보정"
            calib_fill = (80, 80, 255)
        draw.text((self.view_w + 18, 44), calib_label, font=font_section_warn, fill=calib_fill)
        if self.calib_px_per_mm:
            mm_per_px = 1.0 / self.calib_px_per_mm
            # 0.1mm를 믿고 판정하려면 1픽셀이 0.02mm 이하여야 함 (공차의 1/5)
            draw.text((self.view_w + 200, 47), f"1px = {mm_per_px:.4f}mm", font=font_status,
                      fill=(120, 220, 120) if mm_per_px <= 0.02 else (80, 80, 255))
        dxf_ratio = self._dxf_real_ratio()
        if dxf_ratio is not None:
            draw.text((self.view_w + 200, 30), f"도면 {dxf_ratio * 100:.1f}%", font=font_status,
                      fill=(120, 220, 255) if self._dxf_is_real_size() else (80, 80, 255))

        # 측정 화면 좌상단 경고 (측정값을 믿으면 안 되는 상태를 눈에 띄게 표시)
        warnings = []
        if not self.calib_px_per_mm:
            warnings.append("미보정: 측정값은 px 단위입니다. 캘리브레이션을 하세요.")
            if self.calib_notice:
                warnings.append(self.calib_notice)
        if self.templates and not self.calib_px_per_mm:
            warnings.append("기준 도형은 캘리브레이션 후에 표시됩니다.")
        not_real = self._dxf_not_real()
        if not_real:
            which = ", ".join(f"도면 {i + 1}: {r * 100:.1f}%" for i, r in not_real)
            warnings.append(f"도면 배율 ≠ 실측 ({which}) - 모양 비교용입니다. '도면 실측크기'로 복귀")
        # 불러온 도면 목록 (영상 오른쪽 위, 도면 색으로 / 선택된 도면은 ▶)
        for i, d in enumerate(self.drawings):
            text = f"{'▶ ' if i == self.dxf_active else ''}도면 {i + 1}: {d['name'][:28]}"
            ty = self.cam_y_offset + 10 + i * 20
            bx1, by1, bx2, by2 = draw.textbbox((0, ty), text, font=font_btn)
            tx = self.view_w - 12 - (bx2 - bx1)
            draw.rectangle((tx - 4, by1 - 2, self.view_w - 8, by2 + 2), fill=(70, 70, 70))
            draw.text((tx, ty), text, font=font_btn, fill=self.color_palette[d['color_idx']])

        for i, text in enumerate(warnings):
            wy = self.cam_y_offset + 10 + i * 24
            tx1, ty1, tx2, ty2 = draw.textbbox((12, wy), text, font=font_section_warn)
            draw.rectangle((tx1 - 4, ty1 - 3, tx2 + 4, ty2 + 3), fill=(0, 0, 0))
            draw.text((12, wy), text, font=font_section_warn, fill=(80, 80, 255))

        for title, y_pos in self.section_headers.items():
            draw.text((self.view_w + 20, y_pos + 2), title, font=font_section, fill=self.clr_text_dim)

        for mode, (x1, y1, x2, y2) in self.buttons.items():
            is_active  = (mode == self.current_mode) or (mode == 'EDGE_SNAP' and self.edge_snap)
            is_hovered = (mode == self.hovered_button)
            is_pressed = (mode == self.pressed_button)
            if is_pressed or is_active or is_hovered:
                txt_fill = (241, 241, 241)
            else:
                txt_fill = (200, 200, 200)
            label = self.btn_labels.get(mode, mode)
            if mode == 'EDGE_SNAP':
                label = f"경계 맞춤: {'켬' if self.edge_snap else '끔'}  (E)"
            draw.text((x1 + 8, y1 + 5), label, font=font_btn, fill=txt_fill)

        # ── 상태 텍스트 ───────────────────────────
        if self.loaded_frame is not None:
            source = "사진"
        else:
            source = '정지' if self.is_frozen else '라이브'
        status_texts = [
            f"모드: {self.btn_labels.get(self.current_mode, self.current_mode)}",
            f"상태: {source} (카메라 {self.current_cam_idx})",
            f"해상도: {self.frame_w}x{self.frame_h}",
            f"영상 확대: {self.view_zoom:.1f}배",
            (f"도면{self.dxf_active + 1} 배율: {self.scale:.3f}px/mm" if self.drawings
             else "도면: 없음"),
            f"도면 회전: {self.angle:.1f}°" if self.drawings else f"도면: {len(self.drawings)}개",
            f"측정: {len(self.measurements)}개",
            f"십자선: {len(self.crosshairs)}개",
            f"저울: {self.scale_error[:24] if self.scale_error else ('연결' if self.scale_connected else '미연결')}",
            f"기준도형: {len(self.templates)}개",
        ]
        y_pos = mag_y2 + 10
        for i, text in enumerate(status_texts):
            col = self.view_w + 20 if i % 2 == 0 else self.view_w + 180
            draw.text((col, y_pos), text, font=font_status, fill=self.clr_text_dim)
            if i % 2 == 1:
                y_pos += 15

        # ── 영상 조작 안내 (영상 아래 여백) ──
        hint = ("휠: 영상 확대/축소   오른쪽 버튼 드래그: 영상 이동   오른쪽 더블클릭 / F: 화면 맞춤   "
                "E: 경계 맞춤 켬/끔   기준 도형 더블클릭: 치수 변경")
        hy = min(self.view_h - 20, self.cam_y_offset + self.cam_display_h + 6)
        draw.text((12, hy), hint, font=font_status, fill=self.clr_text_dim)

        if self.camera_list_visible:
            self._draw_camera_list(display_img, draw, font_section, font_status)

        if self._modal is None:
            return np.array(img_pil)
        # 입력판이 열려 있으면 뒤 화면을 어둡게 하고 그 위에 입력판을 그림
        dimmed = Image.fromarray((np.asarray(img_pil) * 0.45).astype(np.uint8))
        self._draw_modal(ImageDraw.Draw(dimmed))
        return np.array(dimmed)

    # ──────────────────────────────────────────────
    # 측정 기준 (캘리브레이션)
    # ──────────────────────────────────────────────
    def _measure_text(self, px_len):
        """픽셀 길이를 측정 표시 문자열로 변환. 미보정이면 mm로 속이지 않고 px로 표시"""
        if self.calib_px_per_mm:
            return f"{px_len / self.calib_px_per_mm:.3f}mm"
        return f"{px_len:.1f}px (NO CAL)"   # cv2.putText는 구버전에서 한글이 깨지므로 영문 표기

    def _reset_calibration(self, reason):
        """영상 조건이 바뀌어 기존 캘리브레이션을 믿을 수 없을 때 초기화"""
        had_calib = self.calib_px_per_mm is not None
        self.calib_px_per_mm = None
        self.fixed_calib_line = None
        self.calib_temp_data = None
        if had_calib:
            self.calib_notice = f"{reason} 캘리브레이션이 초기화되었습니다."

    def _dxf_real_ratio(self):
        """도면 표시 배율 / 측정 기준 (1.0 = 실제 치수). 미보정이거나 도면이 없으면 None"""
        if not self.calib_px_per_mm or not self.dxf_contours:
            return None
        return self.scale / self.calib_px_per_mm

    def _dxf_is_real_size(self):
        ratio = self._dxf_real_ratio()
        return ratio is None or abs(ratio - 1.0) < 1e-6

    # ──────────────────────────────────────────────
    # 영상 확대/이동 (화면 좌표 ↔ 원본 프레임 좌표)
    #   frame 좌표: 카메라 원본 픽셀 (측정·캘리브는 모두 이 좌표로 계산)
    #   화면 좌표 = (frame 좌표 - 화면중심의 frame 좌표) × 배율 + 화면중심
    # ──────────────────────────────────────────────
    MAX_SCREEN_PX_PER_FRAME_PX = 20.0   # 최대 확대: 원본 1픽셀이 화면 20픽셀

    def _reset_view(self):
        """영상 확대를 처음(전체 보기) 상태로"""
        self.view_zoom = 1.0
        self.view_cx = self.frame_w / 2.0
        self.view_cy = self.frame_h / 2.0

    def _view_scale(self):
        """원본 1픽셀이 화면 몇 픽셀인지"""
        return self.view_w / self.frame_w * self.view_zoom

    def _clamp_view(self):
        """확대한 영상이 화면 밖으로 벗어나지 않도록 중심 위치를 제한"""
        s = self._view_scale()
        half_w = self.view_w / (2 * s)
        half_h = self.cam_display_h / (2 * s)
        if half_w * 2 >= self.frame_w:
            self.view_cx = self.frame_w / 2.0
        else:
            self.view_cx = min(max(self.view_cx, half_w), self.frame_w - half_w)
        if half_h * 2 >= self.frame_h:
            self.view_cy = self.frame_h / 2.0
        else:
            self.view_cy = min(max(self.view_cy, half_h), self.frame_h - half_h)

    def _s2f(self, x, y):
        """창 좌표(마우스) → 원본 프레임 좌표"""
        s = self._view_scale()
        return ((x - self.view_w / 2.0) / s + self.view_cx,
                (y - self.cam_y_offset - self.cam_display_h / 2.0) / s + self.view_cy)

    def _f2s_arr(self, pts):
        """원본 프레임 좌표 배열 (N,2) → 영상 표시 영역 좌표 배열"""
        s = self._view_scale()
        pts = np.asarray(pts, dtype=np.float64)
        return (pts - [self.view_cx, self.view_cy]) * s + [self.view_w / 2.0, self.cam_display_h / 2.0]

    def _zoom_view_at(self, x, y, factor):
        """마우스 위치를 기준으로 영상 확대/축소 (마우스 아래 지점이 그대로 유지됨)"""
        fx, fy = self._s2f(x, y)
        max_zoom = self.MAX_SCREEN_PX_PER_FRAME_PX * self.frame_w / self.view_w
        self.view_zoom = min(max(self.view_zoom * factor, 1.0), max(1.0, max_zoom))
        s = self._view_scale()
        self.view_cx = fx - (x - self.view_w / 2.0) / s
        self.view_cy = fy - (y - self.cam_y_offset - self.cam_display_h / 2.0) / s
        self._clamp_view()

    def _render_view(self, frame):
        """원본 프레임에서 현재 확대 영역을 잘라 화면 크기 영상으로 만듦 (좌표 계산과 정확히 일치)"""
        s = self._view_scale()
        src, k = frame, 1.0
        # 많이 축소할 때는 피라미드 축소로 계단 현상을 줄임 (pyrDown은 정확히 1/2 배율)
        while (s * k * 2 <= 1.0 and src.shape[0] % 2 == 0 and src.shape[1] % 2 == 0
               and min(src.shape[:2]) > 64):
            src = cv2.pyrDown(src)
            k *= 2
        m = np.array([[k * s, 0, self.view_w / 2.0 - self.view_cx * s],
                      [0, k * s, self.cam_display_h / 2.0 - self.view_cy * s]], dtype=np.float64)
        # 크게 확대하면 원본 픽셀이 네모로 보이게 해 실제 경계 위치를 판단하기 쉽게 함
        interp = cv2.INTER_NEAREST if s >= 2.0 else cv2.INTER_LINEAR
        return cv2.warpAffine(src, m, (self.view_w, self.cam_display_h), flags=interp,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))

    # ──────────────────────────────────────────────
    # 경계 자동 맞춤 (서브픽셀)
    # ──────────────────────────────────────────────
    EDGE_MIN_STRENGTH = 40.0   # 이보다 약한 밝기 변화는 경계로 보지 않음 (흐린 노이즈 무시)

    def _snap_to_edge(self, fx, fy):
        """클릭 근처에서 가장 가까운 뚜렷한 경계를 찾아 1픽셀보다 정밀한 위치를 돌려줌.
        경계를 못 찾으면 None"""
        frame = self.last_frame
        if frame is None:
            return None
        s = self._view_scale()
        # 화면 기준 약 8픽셀 범위에서 찾음 (크게 확대해도 흐린 경계 폭을 덮도록 원본 3픽셀 이상)
        radius = float(np.clip(8.0 / s, 3.0, 40.0))
        h, w = frame.shape[:2]
        margin = int(np.ceil(radius)) + 4
        x0, y0 = max(0, int(fx) - margin), max(0, int(fy) - margin)
        x1, y1 = min(w, int(fx) + margin + 1), min(h, int(fy) + margin + 1)
        if x1 - x0 < 7 or y1 - y0 < 7:
            return None

        roi = frame[y0:y1, x0:x1]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
        gray = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.0)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)

        yy, xx = np.mgrid[y0:y1, x0:x1]
        dist = np.hypot(xx - fx, yy - fy)
        inside = dist <= radius
        inside[:2, :] = inside[-2:, :] = False
        inside[:, :2] = inside[:, -2:] = False
        if not inside.any():
            return None
        peak = float(mag[inside].max())
        if peak < self.EDGE_MIN_STRENGTH:
            return None

        def sample(px, py):
            """mag 이미지에서 실수 좌표 값을 보간해서 읽음"""
            px = np.clip(px, 0, mag.shape[1] - 1.001)
            py = np.clip(py, 0, mag.shape[0] - 1.001)
            ix, iy = np.floor(px).astype(int), np.floor(py).astype(int)
            ax, ay = px - ix, py - iy
            return (mag[iy, ix] * (1 - ax) * (1 - ay) + mag[iy, ix + 1] * ax * (1 - ay) +
                    mag[iy + 1, ix] * (1 - ax) * ay + mag[iy + 1, ix + 1] * ax * ay)

        # 경계 후보: 충분히 강하고, 밝기 변화 방향으로 봤을 때 가장 강한 지점(경계의 한가운데)
        cy_idx, cx_idx = np.nonzero(inside & (mag >= 0.5 * peak))
        m0 = mag[cy_idx, cx_idx]
        dx = gx[cy_idx, cx_idx] / np.maximum(m0, 1e-6)
        dy = gy[cy_idx, cx_idx] / np.maximum(m0, 1e-6)
        m_minus = sample(cx_idx - dx, cy_idx - dy)
        m_plus = sample(cx_idx + dx, cy_idx + dy)
        is_ridge = (m0 >= m_minus) & (m0 >= m_plus)
        if not is_ridge.any():
            return None

        # 그중 클릭한 곳에서 가장 가까운 경계 선택 (경계가 여러 개면 사용자가 찍은 쪽을 따름)
        cand = np.nonzero(is_ridge)[0]
        best = cand[np.argmin(dist[cy_idx[cand], cx_idx[cand]])]
        bx, by = cx_idx[best], cy_idx[best]
        a, b, c = m_minus[best], m0[best], m_plus[best]
        denom = a - 2 * b + c
        t = 0.5 * (a - c) / denom if abs(denom) > 1e-6 else 0.0
        t = float(np.clip(t, -0.5, 0.5))   # 포물선 꼭짓점 = 서브픽셀 경계 위치
        return (x0 + bx + t * dx[best], y0 + by + t * dy[best])

    def _maybe_snap(self, fx, fy):
        """경계 맞춤이 켜져 있으면 경계 위치로, 못 찾으면 클릭 위치 그대로"""
        if not self.edge_snap:
            return (fx, fy)
        snapped = self._snap_to_edge(fx, fy)
        return snapped if snapped is not None else (fx, fy)

    # ──────────────────────────────────────────────
    # 여러 도면 도우미
    # ──────────────────────────────────────────────
    def _dwg_frame_contours(self, d):
        """도면 d의 윤곽선을 원본 프레임 좌표로 변환 [(ctype, pts), ...]"""
        rad = np.radians(d['angle'])
        rot = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
        c = np.array([self.frame_w / 2.0 + d['offset_x'], self.frame_h / 2.0 + d['offset_y']])
        return [(ctype, (pts @ rot.T) * d['scale'] + c) for ctype, pts in d['contours']]

    def _drawing_hit(self, fx, fy):
        """클릭 위치에서 화면 기준 10픽셀 이내에 선이 지나가는 도면 번호 (가장 가까운 것). 없으면 None"""
        tol = 10.0 / self._view_scale()
        p = np.array([fx, fy])
        best, best_d = None, tol
        for i, d in enumerate(self.drawings):
            for ctype, pts in self._dwg_frame_contours(d):
                a = pts if ctype == 'poly' else pts[:-1]
                b = np.roll(pts, -1, axis=0) if ctype == 'poly' else pts[1:]
                ab = b - a
                u = np.clip(np.einsum('ij,ij->i', p - a, ab) / np.maximum(np.einsum('ij,ij->i', ab, ab), 1e-9), 0, 1)
                dist = np.hypot(*(p - (a + u[:, None] * ab)).T).min()
                if dist <= best_d:
                    best, best_d = i, dist
        return best

    def _dxf_not_real(self):
        """실제 치수와 다른 배율로 표시 중인 도면 [(번호, 비율), ...]"""
        if not self.calib_px_per_mm:
            return []
        return [(i, d['scale'] / self.calib_px_per_mm) for i, d in enumerate(self.drawings)
                if abs(d['scale'] / self.calib_px_per_mm - 1.0) >= 1e-6]

    def _set_all_drawings_real_size(self):
        """캘리브레이션 값으로 모든 도면을 실제 치수 크기로"""
        for d in self.drawings + [self._nodwg]:
            d['scale'] = self.calib_px_per_mm

    # ──────────────────────────────────────────────
    # 기준 도형 (치수를 입력한 원·사각형·직선을 실제 크기로 표시)
    # ──────────────────────────────────────────────
    TPL_NAMES = {'circle': '원', 'rect': '사각형', 'line': '직선'}

    def _tpl_geometry(self, t):
        """도형을 이루는 선들 [(점들(원본 px), 닫힌 도형 여부), ...]. 캘리브레이션이 없으면 None"""
        ppm = self.calib_px_per_mm
        if not ppm:
            return None
        rad = np.radians(t['angle'])
        rot = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
        c = np.array([t['cx'], t['cy']])
        place = lambda local: np.asarray(local, dtype=np.float64) @ rot.T + c
        if t['type'] == 'circle':
            a = np.linspace(0, 2 * np.pi, 180, endpoint=False)
            r = t['w'] / 2 * ppm
            return [(np.column_stack([np.cos(a), np.sin(a)]) * r + c, True)]
        if t['type'] == 'rect':
            hw, hh = t['w'] / 2 * ppm, t['h'] / 2 * ppm
            return [(place([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]), True)]
        if t['type'] == 'gauge':
            # 제품 게이지: 길이 3mm 평행선 2개 (간격 = 제품 높이) + 두 평행선 가운데를 잇는 선
            hb, hh = GAUGE_BAR_MM / 2 * ppm, t['w'] / 2 * ppm
            return [(place([[-hb, -hh], [hb, -hh]]), False),
                    (place([[-hb, hh], [hb, hh]]), False),
                    (place([[0, -hh], [0, hh]]), False)]
        hl = t['w'] / 2 * ppm
        return [(place([[-hl, 0.0], [hl, 0.0]]), False)]

    def _tpl_label(self, t):
        # cv2.putText는 Ø 같은 기호가 깨지므로 영문 표기 (제품 게이지는 한글 이름이라 별도로 그림)
        if t['type'] == 'circle':
            return f"D {t['w']:.3f}mm"
        if t['type'] == 'rect':
            return f"{t['w']:.3f} x {t['h']:.3f}mm"
        if t['type'] == 'gauge':
            return f"{t['name']} {t['w']:.3f}mm"
        return f"L {t['w']:.3f}mm"

    def _pil_text(self, img, pos, text, color, size):
        """한글이 들어간 글자를 영상의 글자 부분만 PIL로 그림 (cv2.putText는 한글이 깨짐)"""
        font = self._font("malgunbd.ttf", max(8, int(size)))
        x, y = int(pos[0]), int(pos[1])
        bx1, by1, bx2, by2 = font.getbbox(text)
        x1, y1 = max(0, x + bx1 - 2), max(0, y + by1 - 2)
        x2, y2 = min(img.shape[1], x + bx2 + 2), min(img.shape[0], y + by2 + 2)
        if x2 <= x1 or y2 <= y1:
            return
        roi = img[y1:y2, x1:x2]
        pil = Image.fromarray(roi)
        ImageDraw.Draw(pil).text((x - x1, y - y1), text, font=font, fill=color)
        roi[:] = np.array(pil)

    def _template_hit(self, fx, fy):
        """클릭 위치에서 가까운 기준 도형(테두리 또는 가운데) 번호. 없으면 None"""
        tol = 10.0 / self._view_scale()   # 화면 기준 10픽셀 이내
        best, best_d = None, tol
        for i, t in enumerate(self.templates):
            geo = self._tpl_geometry(t)
            if geo is None:
                continue
            p = np.array([fx, fy])
            d = np.hypot(fx - t['cx'], fy - t['cy'])      # 가운데
            segs = []
            for pts, closed in geo:
                segs += list(zip(pts, np.roll(pts, -1, axis=0))) if closed else list(zip(pts[:-1], pts[1:]))
            for a, b in segs:                              # 테두리까지 거리
                ab = b - a
                u = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-9), 0, 1)
                d = min(d, np.hypot(*(p - (a + u * ab))))
            if d <= best_d:
                best, best_d = i, d
        return best

    def _template_form(self, initial, on_done, on_delete=None):
        """도형 종류·치수(mm)·각도 입력판 (화면 안). 확인하면 on_done(dict)"""
        kind = initial['type'] if initial else 'circle'
        fields = [
            {'key': 'w', 'label': {'circle': '지름 (mm)', 'rect': '가로 (mm)', 'line': '길이 (mm)'},
             'value': f"{initial['w']:g}" if initial else ""},
            {'key': 'h', 'label': '세로 (mm)', 'enabled': {'rect'},
             'value': f"{initial['h']:g}" if initial and initial['type'] == 'rect' else ""},
            {'key': 'angle', 'label': '각도 (도)', 'value': f"{initial['angle']:g}" if initial else "0"},
        ]

        def ok(vals):
            try:
                w = float(vals['w'])
                h = float(vals['h']) if vals['_radio'] == 'rect' else 0.0
                ang = float(vals['angle'] or 0)
            except ValueError:
                return "치수는 숫자로 입력하세요."
            if w <= 0 or (vals['_radio'] == 'rect' and h <= 0):
                return "치수는 0보다 커야 합니다."
            on_done({'type': vals['_radio'], 'w': w, 'h': h, 'angle': ang % 360})

        self._form("기준 도형 수정" if initial else "기준 도형 추가", fields, ok,
                   radio=([(k, self.TPL_NAMES[k]) for k in ('circle', 'rect', 'line')], kind),
                   on_delete=on_delete)

    def _product_pick(self, on_done, current=None, on_delete=None):
        """제품 목록 입력판 (화면 안). 고르면 on_done(제품명, 높이)"""
        if not PRODUCTS:
            self._message("제품 게이지", "제품 목록이 비어 있습니다.")
            return
        names = [n for n, _ in PRODUCTS]
        self._pick("제품 게이지 변경" if current else "제품 게이지",
                   [f"{name}   ({height:g} mm)" for name, height in PRODUCTS],
                   lambda i: on_done(*PRODUCTS[i]),
                   sel=names.index(current) if current in names else 0,
                   text=f"제품을 더블클릭하세요  (평행선 {GAUGE_BAR_MM:g}mm, 간격 = 제품 높이)",
                   on_delete=on_delete)

    def _add_template(self, gauge=False):
        if not self.calib_px_per_mm:
            self._message("기준 도형", "먼저 캘리브레이션을 하세요.\n기준 도형은 실제 치수(mm) 크기로 표시됩니다.")
            return

        def place(res):
            res.update(cx=self.view_cx, cy=self.view_cy)   # 지금 보고 있는 화면 가운데에 배치
            self.templates.append(res)
            self.tpl_selected = len(self.templates) - 1
            self.cross_selected_idx = self.cross_edit_idx = None
            self.current_mode = 'PAN'                        # 바로 끌어서 옮길 수 있게

        if gauge:
            self._product_pick(lambda name, height: place(
                {'type': 'gauge', 'name': name, 'w': height, 'h': GAUGE_BAR_MM, 'angle': 0.0}))
        else:
            self._template_form(None, place)

    def _edit_template(self, idx=None):
        if idx is None:
            idx = self.tpl_selected
            if idx is None or not (0 <= idx < len(self.templates)):
                idx = len(self.templates) - 1 if self.templates else None
        if idx is None:
            self._message("기준 도형", "수정할 기준 도형이 없습니다.\n'도형 추가'로 먼저 만드세요.")
            return
        t = self.templates[idx]
        self.tpl_selected = idx

        def delete():
            if t in self.templates:
                self.templates.remove(t)
            self.tpl_selected = None

        if t['type'] == 'gauge':   # 제품 게이지는 제품을 다시 골라서 변경
            self._product_pick(lambda name, height: t.update(name=name, w=height),
                               current=t['name'], on_delete=delete)
        else:
            self._template_form(t, t.update, on_delete=delete)

    def _draw_templates(self, img, to_img, unit, live):
        for i, t in enumerate(self.templates):
            geo = self._tpl_geometry(t)
            if geo is None:
                continue
            selected = live and i == self.tpl_selected
            color = (255, 255, 255) if selected else self.tpl_color
            for pts, closed in geo:
                q = self._fix(to_img(pts)).reshape(-1, 1, 2)
                cv2.polylines(img, [q], closed, color, max(1, int(round(unit))), cv2.LINE_AA, self.SHIFT)
            pts = np.vstack([p for p, _ in geo])
            # 가운데 표시 (작은 +)
            c = to_img([(t['cx'], t['cy'])])[0]
            arm = 5 * unit
            cv2.line(img, (int(c[0] - arm), int(c[1])), (int(c[0] + arm), int(c[1])), color, 1, cv2.LINE_AA)
            cv2.line(img, (int(c[0]), int(c[1] - arm)), (int(c[0]), int(c[1] + arm)), color, 1, cv2.LINE_AA)
            if selected:
                cv2.circle(img, (int(c[0]), int(c[1])), 9, color, 1, cv2.LINE_AA)
            # 치수 글자: 도형 오른쪽 위
            anchor = pts[np.argmax(pts[:, 0] - pts[:, 1])]
            if t['type'] == 'gauge':
                q = to_img([anchor])[0]
                self._pil_text(img, (q[0] + 6 * unit, q[1] - 18 * unit), self._tpl_label(t), color, 13 * unit)
            else:
                self._text(img, to_img, anchor, self._tpl_label(t), color, unit, 0.45)

    # ──────────────────────────────────────────────
    # 화면 안 입력판 (팝업창 대신 프로그램 화면 가운데에 표시)
    #   form: 숫자 입력 / list: 목록에서 고르기 / confirm: 예·아니요 / message: 안내
    #   키보드: 숫자 입력, Tab·↑↓ 칸 이동, Enter 확인, Esc 취소, Backspace 지우기
    # ──────────────────────────────────────────────
    KEY_UP, KEY_DOWN = 2490368, 2621440   # cv2.waitKeyEx 화살표 키 (Windows)
    MODAL_W = 440
    LIST_VISIBLE = 8

    def _open_modal(self, m):
        m.setdefault('error', '')
        m.setdefault('text', '')
        self._modal = m
        self._modal_hits = []
        self.is_dragging = False
        self.view_dragging = False

    def _message(self, title, text):
        self._open_modal({'kind': 'message', 'title': title, 'text': text,
                          'buttons': [('확인', 'cancel', 'primary')]})

    def _confirm(self, title, text, on_yes):
        self._open_modal({'kind': 'confirm', 'title': title, 'text': text, 'on_ok': lambda _: on_yes(),
                          'buttons': [('예', 'ok', 'primary'), ('아니요', 'cancel', None)]})

    def _form(self, title, fields, on_ok, radio=None, on_delete=None, text='', on_cancel=None):
        """fields: [{'key', 'label'(문자열 또는 라디오값별 dict), 'value', 'enabled'(라디오값 집합), 'numeric'}]"""
        for f in fields:
            f.setdefault('enabled', None)
            f.setdefault('numeric', True)
            f['value'] = str(f.get('value', ''))
        m = {'kind': 'form', 'title': title, 'text': text, 'fields': fields, 'on_ok': on_ok,
             'on_delete': on_delete, 'on_cancel': on_cancel, 'radio_opts': radio[0] if radio else None,
             'radio': radio[1] if radio else None, 'fresh': True,
             'buttons': [('확인', 'ok', 'primary')] + ([('삭제', 'delete', 'danger')] if on_delete else [])
                        + [('취소', 'cancel', None)]}
        m['active'] = next((i for i, f in enumerate(fields) if self._field_enabled(m, f)), None)
        self._open_modal(m)

    def _pick(self, title, items, on_ok, sel=0, text='', on_delete=None):
        m = {'kind': 'list', 'title': title, 'text': text, 'items': items, 'sel': sel,
             'scroll': max(0, sel - self.LIST_VISIBLE + 1), 'on_ok': on_ok, 'on_delete': on_delete,
             'buttons': [('확인', 'ok', 'primary')] + ([('삭제', 'delete', 'danger')] if on_delete else [])
                        + [('취소', 'cancel', None)]}
        self._open_modal(m)

    @staticmethod
    def _field_enabled(m, f):
        return f['enabled'] is None or m['radio'] in f['enabled']

    def _modal_action(self, action):
        m = self._modal
        if m is None:
            return
        if action == 'cancel':
            self._modal = None
            if m.get('on_cancel'):
                m['on_cancel']()
            return
        if action == 'delete':
            self._modal = None
            m['on_delete']()
            return
        if m['kind'] == 'form':
            arg = {f['key']: f['value'].strip() for f in m['fields']}
            arg['_radio'] = m['radio']
        elif m['kind'] == 'list':
            if not m['items']:
                return
            arg = m['sel']
        else:
            arg = None
        self._modal = None   # 먼저 닫음 (확인 동작이 안내판을 새로 띄울 수 있음)
        err = m['on_ok'](arg) if m.get('on_ok') else None
        if isinstance(err, str):   # 입력값 오류 → 같은 입력판을 다시 띄우고 오류 표시
            m['error'] = err
            self._modal = m

    def _modal_move_field(self, step):
        m = self._modal
        enabled = [i for i, f in enumerate(m['fields']) if self._field_enabled(m, f)]
        if not enabled:
            return
        pos = enabled.index(m['active']) if m['active'] in enabled else -1
        m['active'] = enabled[(pos + step) % len(enabled)]
        m['fresh'] = True

    def _modal_key(self, key):
        m = self._modal
        if key in (13, 10):
            self._modal_action('ok')
            return
        if key == 27:
            self._modal_action('cancel')
            return
        if m['kind'] == 'list':
            if key in (self.KEY_UP, self.KEY_DOWN) and m['items']:
                m['sel'] = min(max(m['sel'] + (1 if key == self.KEY_DOWN else -1), 0), len(m['items']) - 1)
                m['scroll'] = min(max(m['scroll'], m['sel'] - self.LIST_VISIBLE + 1), m['sel'])
            return
        if m['kind'] != 'form' or m['active'] is None:
            return
        if key == 9 or key == self.KEY_DOWN:
            self._modal_move_field(1)
            return
        if key == self.KEY_UP:
            self._modal_move_field(-1)
            return
        f = m['fields'][m['active']]
        if key == 8:   # Backspace
            f['value'] = '' if m['fresh'] else f['value'][:-1]
            m['fresh'] = False
            m['error'] = ''
            return
        ch = chr(key) if 32 <= key < 127 else ''
        allowed = '0123456789.-' if f['numeric'] else ''.join(chr(c) for c in range(32, 127))
        if ch and ch in allowed and len(f['value']) < 20:
            # 칸을 처음 고른 뒤 입력하면 기존 값을 새로 씀 (수정할 때 지우는 수고를 덜기 위함)
            f['value'] = ch if m['fresh'] else f['value'] + ch
            m['fresh'] = False
            m['error'] = ''

    def _modal_mouse(self, event, x, y, flags):
        m = self._modal
        if event in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONDBLCLK):
            for x1, y1, x2, y2, (what, val) in self._modal_hits:
                if not (x1 <= x <= x2 and y1 <= y <= y2):
                    continue
                if what == 'radio':
                    m['radio'] = val
                    if m['active'] is None or not self._field_enabled(m, m['fields'][m['active']]):
                        m['active'] = next((i for i, f in enumerate(m['fields'])
                                            if self._field_enabled(m, f)), None)
                    m['fresh'] = True
                elif what == 'field':
                    m['active'] = val
                    m['fresh'] = True
                elif what == 'item':
                    m['sel'] = val
                    if event == cv2.EVENT_LBUTTONDBLCLK:
                        self._modal_action('ok')
                elif what == 'button' and event == cv2.EVENT_LBUTTONDOWN:
                    self._modal_action(val)
                return
        elif event == cv2.EVENT_MOUSEWHEEL and m['kind'] == 'list':
            m['scroll'] = min(max(m['scroll'] + (-1 if flags > 0 else 1), 0),
                              max(0, len(m['items']) - self.LIST_VISIBLE))

    def _draw_modal(self, draw):
        """입력판을 그리고 클릭 영역을 기록"""
        m = self._modal
        f_title = self._font("malgunbd.ttf", 15)
        f_body = self._font("malgun.ttf", 13)
        f_small = self._font("malgun.ttf", 10)
        accent, panel, txt = (204, 122, 0), (48, 45, 45), (241, 241, 241)   # BGR 순서
        W, pad = self.MODAL_W, 18
        text_lines = m['text'].split('\n') if m['text'] else []
        h = pad + 28 + len(text_lines) * 21 + (8 if text_lines else 0)
        if m['kind'] == 'form':
            h += (40 if m['radio_opts'] else 0) + len(m['fields']) * 38
        elif m['kind'] == 'list':
            h += min(self.LIST_VISIBLE, max(1, len(m['items']))) * 28 + 6
        h += (24 if m['error'] else 0) + 46 + (18 if m['kind'] in ('form', 'list') else 0) + pad
        x0 = (self.view_w - W) // 2
        y0 = self.cam_y_offset + max(10, (self.cam_display_h - h) // 2)
        hits = []

        def center_text(box, text, font, fill):
            bx1, by1, bx2, by2 = draw.textbbox((0, 0), text, font=font)
            draw.text(((box[0] + box[2] - (bx2 - bx1)) / 2 - bx1, (box[1] + box[3] - (by2 - by1)) / 2 - by1),
                      text, font=font, fill=fill)

        draw.rectangle((x0, y0, x0 + W, y0 + h), fill=panel, outline=accent, width=2)
        y = y0 + pad
        draw.text((x0 + pad, y), m['title'], font=f_title, fill=txt)
        y += 28
        for line in text_lines:
            draw.text((x0 + pad, y), line, font=f_body, fill=(205, 205, 205))
            y += 21
        if text_lines:
            y += 8

        if m['kind'] == 'form':
            if m['radio_opts']:
                n = len(m['radio_opts'])
                bw = (W - 2 * pad - (n - 1) * 6) / n
                for i, (val, label) in enumerate(m['radio_opts']):
                    box = (x0 + pad + i * (bw + 6), y, x0 + pad + i * (bw + 6) + bw, y + 30)
                    draw.rectangle(box, fill=accent if m['radio'] == val else (75, 75, 75))
                    center_text(box, label, f_body, txt)
                    hits.append((*box, ('radio', val)))
                y += 40
            for i, f in enumerate(m['fields']):
                enabled = self._field_enabled(m, f)
                label = f['label'][m['radio']] if isinstance(f['label'], dict) else f['label']
                draw.text((x0 + pad, y + 6), label, font=f_body, fill=txt if enabled else (110, 110, 110))
                box = (x0 + pad + 130, y, x0 + W - pad, y + 30)
                active = enabled and i == m['active']
                draw.rectangle(box, fill=(28, 28, 28) if enabled else (58, 58, 58),
                               outline=accent if active else (95, 95, 95), width=2 if active else 1)
                if enabled:
                    value = f['value']
                    if active and m['fresh'] and value:
                        # 새로 입력하면 바뀔 값임을 표시 (선택된 것처럼)
                        bx1, by1, bx2, by2 = draw.textbbox((box[0] + 8, y + 5), value, font=f_body)
                        draw.rectangle((bx1 - 2, by1 - 2, bx2 + 2, by2 + 2), fill=(140, 90, 30))
                    draw.text((box[0] + 8, y + 5), value + ('|' if active and not m['fresh'] else ''),
                              font=f_body, fill=txt)
                    hits.append((*box, ('field', i)))
                y += 38
        elif m['kind'] == 'list':
            vis = m['items'][m['scroll']:m['scroll'] + self.LIST_VISIBLE]
            for j, item in enumerate(vis):
                i = m['scroll'] + j
                box = (x0 + pad, y, x0 + W - pad, y + 26)
                draw.rectangle(box, fill=accent if i == m['sel'] else (62, 62, 62))
                draw.text((box[0] + 10, y + 4), item, font=f_body, fill=txt)
                hits.append((*box, ('item', i)))
                y += 28
            if not m['items']:
                draw.text((x0 + pad, y + 4), "(목록이 비어 있음)", font=f_body, fill=(150, 150, 150))
                y += 28
            if len(m['items']) > self.LIST_VISIBLE:
                draw.text((x0 + W - pad - 120, y), f"휠로 더 보기 ({len(m['items'])}개)", font=f_small,
                          fill=(160, 160, 160))
            y += 6

        if m['error']:
            draw.text((x0 + pad, y + 2), m['error'], font=f_body, fill=(80, 80, 255))
            y += 24
        y += 8
        bx = x0 + W - pad
        for label, action, style in reversed(m['buttons']):
            box = (bx - 90, y, bx, y + 32)
            fill = accent if style == 'primary' else (60, 60, 185) if style == 'danger' else (85, 85, 85)
            draw.rectangle(box, fill=fill)
            center_text(box, label, f_body, txt)
            hits.append((*box, ('button', action)))
            bx -= 98
        if m['kind'] in ('form', 'list'):
            hint = "Enter: 확인   Esc: 취소   Tab/↑↓: 칸 이동" if m['kind'] == 'form' \
                else "더블클릭 또는 Enter: 선택   Esc: 취소   ↑↓: 이동"
            draw.text((x0 + pad, y + 40), hint, font=f_small, fill=(150, 150, 150))
        self._modal_hits = hits

    def _dialog_root(self):
        """파일 선택 창용 Tk 창: 프로그램 창 가운데·맨 앞에 두어 파일 창이 같은 모니터, 맨 앞에 뜨게 함"""
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-alpha", 0.0)
        root.attributes("-topmost", True)
        try:
            wx, wy, ww, wh = cv2.getWindowImageRect('Vision Inspector')
            root.geometry(f"1x1+{wx + ww // 2 - 300}+{wy + wh // 2 - 200}")
        except cv2.error:
            pass
        root.update()
        root.focus_force()
        return root

    def _font(self, name, size):
        """폰트를 매 프레임 다시 읽지 않도록 캐시"""
        key = (name, size)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(name, size)
            except Exception:
                self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    def _select_last_crosshair(self):
        """마지막 십자선을 선택 대상으로 고정"""
        if not self.crosshairs:
            self.cross_selected_idx = None
            self.cross_edit_idx = None
            return None
        idx = len(self.crosshairs) - 1
        self.cross_selected_idx = idx
        self.cross_edit_idx = idx
        return idx

    def _apply_crosshair_zoom(self, delta_size):
        """선택된 십자선이 있으면 그 크기를, 없으면 도면 배율을 조절"""
        idx = self.cross_selected_idx
        if idx is not None and 0 <= idx < len(self.crosshairs):
            px, py, pangle, psize = self.crosshairs[idx]
            new_size = max(0.2, psize + delta_size)
            self.crosshairs[idx] = (px, py, pangle, new_size)
            self.cross_size = new_size
            self.cross_selected_idx = idx
            self.cross_edit_idx = idx
            return

        # 배율에 비례해 변하도록 곱셈 방식 사용 (휠 한 칸 = 약 15%)
        self.scale = max(0.05, self.scale * 1.15 ** (delta_size / 0.35))

    def _apply_crosshair_rotation(self, delta_deg):
        """선택된 십자선(또는 기준 도형)이 있으면 그 각도를, 없으면 도면 각도를 조절"""
        idx = self.cross_selected_idx
        if idx is None or not (0 <= idx < len(self.crosshairs)):
            t = self.tpl_selected
            if t is not None and 0 <= t < len(self.templates):
                self.templates[t]['angle'] = (self.templates[t]['angle'] + delta_deg) % 360
            else:
                self.angle = (self.angle + delta_deg) % 360
            return
        px, py, pangle, psize = self.crosshairs[idx]
        new_angle = (pangle + delta_deg) % 360
        self.crosshairs[idx] = (px, py, new_angle, psize)
        self.cross_selected_idx = idx
        self.cross_edit_idx = idx

    # ──────────────────────────────────────────────
    # 오버레이 그리기
    #   img: 그릴 영상, to_img: 원본 프레임 좌표 (N,2) → img 좌표 변환 함수
    #   unit: 선 두께·글자 크기 배율 (화면=1, 고해상도 저장 이미지는 더 크게)
    #   화면에서는 확대해도 선이 1픽셀로 유지되어 경계를 가리지 않음
    # ──────────────────────────────────────────────
    SHIFT = 4   # cv2 그리기 좌표를 1/16 픽셀 단위로 넘겨 위치를 정밀하게

    @classmethod
    def _fix(cls, pts):
        return np.round(np.asarray(pts) * (1 << cls.SHIFT)).astype(np.int32)

    def _line(self, img, to_img, p1, p2, color, unit):
        q = self._fix(to_img([p1, p2]))
        cv2.line(img, tuple(q[0]), tuple(q[1]), color, max(1, int(round(unit))), cv2.LINE_AA, self.SHIFT)

    def _text(self, img, to_img, pt, text, color, unit, scale=0.5):
        q = to_img([pt])[0]
        cv2.putText(img, text, (int(q[0]), int(q[1])), cv2.FONT_HERSHEY_SIMPLEX,
                    scale * unit, color, max(1, int(round(unit))), cv2.LINE_AA)

    def _point_mark(self, img, to_img, pt, color, unit):
        """측정점 표시 (작은 원 + 중심점) - 경계 맞춤된 위치를 확인할 수 있게"""
        q = self._fix(to_img([pt]))[0]
        cv2.circle(img, tuple(q), int(4 * unit) << self.SHIFT, color, max(1, int(round(unit))),
                   cv2.LINE_AA, self.SHIFT)
        cv2.circle(img, tuple(q), max(1, int(round(unit))) << self.SHIFT, color, -1,
                   cv2.LINE_AA, self.SHIFT)

    def _draw_overlays(self, img, to_img, unit=1.0, live=True):
        """도면, 캘리브레이션 선, 측정선, 십자선을 그림. live=True면 마우스 미리보기도 그림"""
        meas_clr = self.color_palette[self.idx_meas_color]
        calib_clr = self.color_palette[self.idx_calib_color]
        thick = max(1, int(round(unit)))

        # ── DXF 도면 (여러 개, 각자 위치·회전·배율·색) ──
        for i, d in enumerate(self.drawings):
            color = self.color_palette[d['color_idx']]
            top_left = None
            for ctype, pts in self._dwg_frame_contours(d):
                pts_img = self._fix(to_img(pts)).reshape(-1, 1, 2)
                cv2.polylines(img, [pts_img], ctype == 'poly', color, thick, cv2.LINE_AA, self.SHIFT)
                corner = pts.min(axis=0)
                top_left = corner if top_left is None else np.minimum(top_left, corner)
            if top_left is not None and len(self.drawings) > 1:
                # 도면 번호 표시 (선택된 도면은 [#n])
                label = f"[#{i + 1}]" if (live and i == self.dxf_active) else f"#{i + 1}"
                q = to_img([top_left])[0]
                cv2.putText(img, label, (int(q[0]), int(q[1]) - int(6 * unit)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5 * unit, color, max(1, int(round(unit))), cv2.LINE_AA)

        # ── 캘리브 고정선 ──
        if self.fixed_calib_line:
            p1, p2, val, pt = self.fixed_calib_line
            self._line(img, to_img, p1, p2, calib_clr, unit)
            self._point_mark(img, to_img, p1, calib_clr, unit)
            self._point_mark(img, to_img, p2, calib_clr, unit)
            self._text(img, to_img, pt, f"REF: {val:.3f}mm", calib_clr, unit)

        # ── 측정선 ──
        for m1, m2, val_text, m_type, pt in self.measurements:
            p2 = m2
            if m_type == 'MEAS_HV':
                p2 = (m2[0], m1[1]) if abs(m1[0] - m2[0]) > abs(m1[1] - m2[1]) else (m1[0], m2[1])
            self._line(img, to_img, m1, p2, meas_clr, unit)
            self._point_mark(img, to_img, m1, meas_clr, unit)
            self._point_mark(img, to_img, m2, meas_clr, unit)
            self._text(img, to_img, pt, val_text, meas_clr, unit)

        # ── 기준 도형 · 십자선 ──
        self._draw_templates(img, to_img, unit, live)
        self.draw_crosshair(img, to_img, unit, live)

        if not live:
            return

        mouse_f = self._s2f(self.curr_mx, self.curr_my)
        mouse_in_view = (self.curr_mx <= self.view_w and
                         self.cam_y_offset <= self.curr_my < self.cam_y_offset + self.cam_display_h)

        # ── 드래그 마커 ──
        if self.is_dragging and self.current_mode in ['PAN', 'ZOOM', 'ROTATE']:
            q = to_img([mouse_f])[0]
            cv2.drawMarker(img, (int(q[0]), int(q[1])), (0, 255, 255),
                           markerType=cv2.MARKER_CROSS, markerSize=25, thickness=1)

        # ── 측정 임시 표시 ──
        if self.measure_p1 and not self.measure_p2:
            p1 = self.measure_p1
            p2 = mouse_f
            if self.current_mode == 'MEAS_HV':
                p2 = (p2[0], p1[1]) if abs(p2[0] - p1[0]) > abs(p2[1] - p1[1]) else (p1[0], p2[1])
            self._line(img, to_img, p1, p2, meas_clr, unit)
            self._point_mark(img, to_img, p1, meas_clr, unit)
            preview_len = np.hypot(p1[0] - p2[0], p1[1] - p2[1])
            q = to_img([p2])[0]
            cv2.putText(img, self._measure_text(preview_len), (int(q[0]) + 8, int(q[1]) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, meas_clr, 1, cv2.LINE_AA)
        elif self.measure_p2:
            self._line(img, to_img, self.measure_p1, self.measure_p2, meas_clr, unit)
            self._point_mark(img, to_img, self.measure_p1, meas_clr, unit)
            self._point_mark(img, to_img, self.measure_p2, meas_clr, unit)
            self._text(img, to_img, mouse_f, self._measure_text(self.measure_temp_val), meas_clr, unit)

        # ── 캘리브 임시 표시 ──
        if self.calib_temp_data:
            p1, p2, val = self.calib_temp_data
            self._line(img, to_img, p1, p2, calib_clr, unit)
            self._text(img, to_img, mouse_f, f"REF: {val:.3f}mm", calib_clr, unit)
        elif self.calib_p1 and self.calib_p2:
            self._line(img, to_img, self.calib_p1, self.calib_p2, calib_clr, unit)
            self._point_mark(img, to_img, self.calib_p1, calib_clr, unit)

        # ── 경계 맞춤 미리보기: 클릭하면 점이 붙을 위치를 미리 보여줌 ──
        placing_point = (('MEAS' in self.current_mode and self.measure_p2 is None) or
                         (self.current_mode == 'CALIB' and not self.calib_temp_data))
        if self.edge_snap and placing_point and mouse_in_view and not self.view_dragging:
            snapped = self._snap_to_edge(*mouse_f)
            if snapped is not None:
                q = to_img([snapped])[0]
                cv2.drawMarker(img, (int(round(q[0])), int(round(q[1]))), (0, 255, 255),
                               markerType=cv2.MARKER_DIAMOND, markerSize=10, thickness=1)

    def draw_crosshair(self, img, to_img, unit=1.0, live=True):
        """십자선 오버레이 - 배치된 십자선 + CROSS 모드 미리보기"""
        # 수평 13mm, 수직 5mm (측정 기준 px/mm, 미보정이면 도면 배율 사용)
        px_per_mm = self.calib_px_per_mm or self.scale

        def _draw_one(cx, cy, angle_deg, size, color, label=None):
            h_arm = 6.5 * px_per_mm * size   # 수평 반길이 (원본 px)
            v_arm = 2.5 * px_per_mm * size   # 수직 반길이 (원본 px)
            rad = np.radians(angle_deg)
            hx, hy = np.cos(rad), np.sin(rad)    # 수평축 단위벡터
            vx, vy = -np.sin(rad), np.cos(rad)   # 수직축 단위벡터
            self._line(img, to_img, (cx - hx * h_arm, cy - hy * h_arm),
                       (cx + hx * h_arm, cy + hy * h_arm), color, unit)
            self._line(img, to_img, (cx - vx * v_arm, cy - vy * v_arm),
                       (cx + vx * v_arm, cy + vy * v_arm), color, unit)
            q = self._fix(to_img([(cx, cy)]))[0]
            cv2.circle(img, tuple(q), int(2 * unit) << self.SHIFT, color, -1, cv2.LINE_AA, self.SHIFT)
            if label is not None:
                self._text(img, to_img, (cx + hx * h_arm, cy + hy * h_arm), label, color, unit, 0.4)

        cross_clr = self.color_palette[self.idx_cross_color]
        preview_clr = tuple(int(c * 0.6) for c in cross_clr)   # 미리보기용 어두운 색

        for i, (px, py, pangle, psize) in enumerate(self.crosshairs):
            color = cross_clr
            if live and self.cross_selected_idx == i:
                color = (255, 255, 255)
                q = to_img([(px, py)])[0]
                cv2.circle(img, (int(q[0]), int(q[1])), 8, (255, 255, 255), 1)
            _draw_one(px, py, pangle, psize, color, label=f"#{i+1}")

        if live and self.current_mode == 'CROSS' and self.cross_preview_pos is not None:
            px, py = self.cross_preview_pos
            _draw_one(px, py, self.cross_angle, self.cross_size, preview_clr,
                      label=f"{self.cross_angle:.1f}deg")

    def draw_weight_overlay(self, img, unit=1.0):
        """영상 우하단에 무게값 표시 (저장 이미지에도 포함됨)"""
        with self.scale_lock:
            w_val = self.scale_weight

        # 연결되지 않았으면 값이 남아 있어도 표시하지 않음
        weight_str = f"{w_val:.2f} g" if (self.scale_connected and w_val is not None) else "-- g"

        if self.scale_connected:
            border_rgb = (0, 200, 100)
            tag = self.scale_com_port or "COM"
        else:
            border_rgb = (120, 120, 120)
            tag = "미연결"

        # 박스 크기·위치 (unit 배율 적용)
        box_w, box_h = int(220 * unit), int(70 * unit)
        margin = int(20 * unit)
        bx1 = max(0, img.shape[1] - box_w - margin)
        by1 = max(0, img.shape[0] - box_h - margin)
        bx2 = min(img.shape[1], bx1 + box_w)
        by2 = min(img.shape[0], by1 + box_h)

        # 반투명 배경 (박스 부분만 계산)
        roi = img[by1:by2, bx1:bx2]
        roi[:] = cv2.addWeighted(roi, 0.35, np.full_like(roi, 20), 0.65, 0)

        # PIL로 한글 텍스트 (박스 부분만 변환해서 빠르게)
        img_pil = Image.fromarray(roi)
        draw = ImageDraw.Draw(img_pil)
        font_large = self._font("malgunbd.ttf", max(8, int(26 * unit)))
        font_small = self._font("malgun.ttf", max(8, int(11 * unit)))
        u = lambda v: int(v * unit)
        draw.text((u(10), u(6)), "정밀저울", font=font_small, fill=(180, 180, 180))
        draw.text((box_w - u(48), u(6)), tag, font=font_small, fill=border_rgb)
        draw.text((u(10), u(24)), weight_str, font=font_large, fill=(255, 220, 60))
        draw.text((u(10), u(54)), f"저장 {len(self.weight_log)}건", font=font_small, fill=(140, 140, 140))
        roi[:] = np.array(img_pil)

        # 테두리
        b = border_rgb
        cv2.rectangle(img, (bx1, by1), (bx2 - 1, by2 - 1), (b[2], b[1], b[0]), max(2, int(2 * unit)))
        return img

    # ──────────────────────────────────────────────
    # 마우스
    # ──────────────────────────────────────────────
    def mouse_callback(self, event, x, y, flags, param):
        if self._modal is not None:   # 입력판이 열려 있으면 입력판만 조작
            self._modal_mouse(event, x, y, flags)
            return
        self.curr_mx, self.curr_my = x, y

        self.hovered_button = None
        if x > self.view_w:
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    self.hovered_button = m
                    break

        if event == cv2.EVENT_LBUTTONDOWN and x > self.view_w:
            if self.camera_list_visible:
                for idx in range(6):
                    x1, y1, x2, y2 = self._camera_list_bounds(idx)
                    if x1 <= x <= x2 and y1 <= y <= y2:
                        self.camera_list_visible = False
                        self._start_camera_switch(idx)
                        return
            for m, (bx1, by1, bx2, by2) in self.buttons.items():
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    self.pressed_button = m
                    self._handle_button(m)
                    return

        if event == cv2.EVENT_LBUTTONUP:
            self.pressed_button = None

        # ── 영상 확대/이동 (모든 모드 공통) ──
        in_view = x <= self.view_w
        if event == cv2.EVENT_RBUTTONDBLCLK and in_view:
            self._reset_view()
            return
        if event == cv2.EVENT_RBUTTONDOWN and in_view:
            self.view_dragging = True
            self.view_lmx, self.view_lmy = x, y
            return
        if event == cv2.EVENT_RBUTTONUP:
            self.view_dragging = False
            return
        if event == cv2.EVENT_MOUSEMOVE and self.view_dragging:
            s = self._view_scale()
            self.view_cx -= (x - self.view_lmx) / s
            self.view_cy -= (y - self.view_lmy) / s
            self.view_lmx, self.view_lmy = x, y
            self._clamp_view()
            return
        if event == cv2.EVENT_MOUSEWHEEL and in_view:
            # 도면/십자선 조절 모드가 아니거나 Ctrl을 누르고 있으면 휠 = 영상 확대
            if (self.current_mode not in ('ZOOM', 'ROTATE', 'CROSS')
                    or flags & cv2.EVENT_FLAG_CTRLKEY):
                self._zoom_view_at(x, y, 1.25 if flags > 0 else 1 / 1.25)
                return

        # 마우스 위치를 원본 프레임 좌표로 변환 (측정은 모두 이 좌표로 계산)
        rx, ry = self._s2f(x, y)

        # 측정점/캘리브레이션 점을 찍는 순간에는 가까운 경계로 맞춤
        placing_point = (('MEAS' in self.current_mode and self.measure_p2 is None) or
                         (self.current_mode == 'CALIB' and not self.calib_temp_data))
        if event in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONUP) and in_view and placing_point:
            rx, ry = self._maybe_snap(rx, ry)

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

        if event == cv2.EVENT_MOUSEWHEEL:
            step = 1.0 if (flags > 0) else -1.0
            if self.current_mode == 'CROSS':
                if flags & cv2.EVENT_FLAG_SHIFTKEY:
                    self.cross_angle = (self.cross_angle + step * 0.1) % 360
                    self.cross_size = max(0.2, self.cross_size + step * 0.08)
                else:
                    self.cross_angle = (self.cross_angle + step) % 360
                    self.cross_size = max(0.2, self.cross_size + step * 0.15)
                return

            if self.current_mode == 'ZOOM':
                self._apply_crosshair_zoom(step * 0.35)
                return

            if self.current_mode == 'ROTATE':
                self._apply_crosshair_rotation(step * 3.0)
                return

        # 기준 도형 더블클릭 → 치수 변경
        if (event == cv2.EVENT_LBUTTONDBLCLK and in_view
                and self.current_mode in ['PAN', 'ZOOM', 'ROTATE']):
            idx = self._template_hit(rx, ry)
            if idx is not None:
                self.is_dragging = False
                self.tpl_selected = idx
                self._edit_template(idx)
                return

        if event == cv2.EVENT_LBUTTONDOWN and x <= self.view_w:
            if self.current_mode in ['PAN', 'ZOOM', 'ROTATE']:
                self.is_dragging = True
                self.lmx, self.lmy = x, y
                best_idx = None
                if self.crosshairs:
                    best_dist = 12 / self._view_scale()   # 화면 기준 12픽셀 이내
                    for idx, (cx, cy, _, _) in enumerate(self.crosshairs):
                        dist = np.hypot(rx - cx, ry - cy)
                        if dist < best_dist:
                            best_dist = dist
                            best_idx = idx
                # 십자선 근처 → 십자선, 기준 도형 테두리/가운데 → 도형, 빈 곳 → 도면 편집
                self.cross_selected_idx = best_idx
                self.cross_edit_idx = best_idx
                self.tpl_selected = self._template_hit(rx, ry) if best_idx is None else None
                if best_idx is None and self.tpl_selected is None:
                    # 도면 선 위를 누르면 그 도면을 선택 (빈 곳이면 지금 선택된 도면을 그대로 조작)
                    hit = self._drawing_hit(rx, ry)
                    if hit is not None:
                        self.dxf_active = hit
                return
            if self.current_mode == 'CROSS':
                self.crosshairs.append((rx, ry, self.cross_angle, self.cross_size))
                self.cross_selected_idx = len(self.crosshairs) - 1
                self.cross_edit_idx = len(self.crosshairs) - 1
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
                        self._measure_text(self.measure_temp_val),   # 측정 시점 기준으로 확정된 값
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

        # CROSS 모드: 미리보기 위치 항상 갱신
        if self.current_mode == 'CROSS' and x <= self.view_w:
            self.cross_preview_pos = (rx, ry)

        if event == cv2.EVENT_MOUSEMOVE and self.is_dragging:
            if self.current_mode == 'CALIB':
                self.calib_p2 = (rx, ry)
            else:
                dx = (x - self.lmx) / self._view_scale()   # 화면 이동량 → 원본 픽셀 이동량
                dy = (y - self.lmy) / self._view_scale()
                tpl = (self.templates[self.tpl_selected]
                       if self.tpl_selected is not None and 0 <= self.tpl_selected < len(self.templates)
                       else None)
                if self.current_mode == 'PAN':
                    if self.crosshairs and self.cross_selected_idx is not None:
                        idx = self.cross_selected_idx
                        if 0 <= idx < len(self.crosshairs):
                            px, py, pangle, psize = self.crosshairs[idx]
                            self.crosshairs[idx] = (px + dx, py + dy, pangle, psize)
                    elif tpl is not None:
                        tpl['cx'] += dx
                        tpl['cy'] += dy
                    else:
                        self.offset_x += dx
                        self.offset_y += dy
                elif self.current_mode == 'ZOOM':
                    if self.cross_edit_idx is not None and x <= self.view_w:
                        idx = self.cross_edit_idx
                        if 0 <= idx < len(self.crosshairs):
                            px, py, pangle, psize = self.crosshairs[idx]
                            factor = 1.0 - (y - self.lmy) * 0.005
                            new_size = max(0.2, psize * factor)
                            self.crosshairs[idx] = (px, py, pangle, new_size)
                            self.cross_selected_idx = idx
                            self.cross_edit_idx = idx
                            self.cross_size = new_size
                        else:
                            self.scale *= (1 - (y - self.lmy) * 0.005)
                            self.scale = max(0.05, self.scale)
                    elif tpl is not None:
                        pass   # 기준 도형은 입력한 치수로 고정 (크기 변경은 치수 입력으로)
                    else:
                        self.scale *= (1 - (y - self.lmy) * 0.005)
                        self.scale = max(0.05, self.scale)
                elif self.current_mode == 'ROTATE':
                    if self.cross_edit_idx is not None and x <= self.view_w:
                        idx = self.cross_edit_idx
                        if 0 <= idx < len(self.crosshairs):
                            px, py, pangle, psize = self.crosshairs[idx]
                            new_angle = (pangle + (x - self.lmx) * 0.2) % 360
                            self.crosshairs[idx] = (px, py, new_angle, psize)
                    elif tpl is not None:
                        tpl['angle'] = (tpl['angle'] + (x - self.lmx) * 0.2) % 360
                    else:
                        self.angle += (x - self.lmx) * 0.2
                self.lmx, self.lmy = x, y

        if event == cv2.EVENT_LBUTTONUP:
            if self.is_dragging and self.current_mode == 'CALIB' and self.calib_p1:
                # 입력창이 떠 있는 동안 마우스 이벤트가 다시 들어와 calib_p1을 지울 수 있으므로
                # 시작/끝점을 먼저 따로 저장하고 드래그 상태를 정리한 뒤 입력창을 띄움
                calib_start, calib_end = self.calib_p1, (rx, ry)
                self.is_dragging = False
                dist_px = np.linalg.norm(np.array(calib_start) - np.array(calib_end))
                if dist_px > 10:
                    self._ask_calibration_length(calib_start, calib_end, dist_px)
                    return   # 입력하는 동안 기준선이 보이도록 calib_p1/p2를 남겨 둠
            self.is_dragging = False
            if self.current_mode not in ['PAN', 'ZOOM', 'ROTATE']:
                self.cross_edit_idx = None
                self.cross_selected_idx = None
            self.calib_p1 = self.calib_p2 = None

    def _ask_calibration_length(self, start, end, dist_px):
        """그린 기준선의 실제 길이(mm)를 화면 안 입력판으로 받음"""
        self.calib_p1, self.calib_p2 = start, end

        def ok(vals):
            try:
                val = float(vals['len'])
            except ValueError:
                return "길이를 숫자로 입력하세요."
            if val <= 0:
                return "길이는 0보다 커야 합니다."
            # 측정 기준을 정하고, 도면도 실제 치수 크기로 맞춤
            self.calib_px_per_mm = dist_px / val
            self._set_all_drawings_real_size()
            self.calib_notice = ""
            self.calib_temp_data = (start, end, val)
            self.calib_p1 = self.calib_p2 = None

        def cancel():
            self.calib_p1 = self.calib_p2 = None

        self._form("캘리브레이션", [{'key': 'len', 'label': '실제 길이 (mm)', 'value': ''}], ok,
                   text=f"그린 기준선: {dist_px:.1f} px\n이 선의 실제 길이를 입력하세요.", on_cancel=cancel)

    def _handle_button(self, m):
        """버튼 클릭 처리 분리"""
        if m == 'FREEZE_LIVE':
            if self.loaded_frame is not None:
                self.loaded_frame = None
                self.is_frozen = False
                self._reset_calibration("사진에서 라이브로 돌아와")
            elif not self.is_frozen:
                if self.cap is None:   # 카메라 전환 중
                    return
                ret, frame = self.cap.read()
                if ret:
                    self.frozen_frame = frame.copy()
                    self.is_frozen = True
            else:
                self.is_frozen = False

        elif m == 'SWITCH_CAM':
            self.switch_camera()

        elif m == 'LOAD_IMAGE':
            self.load_image_action()

        elif m == 'DXF_COLOR':
            self.idx_dxf_color = (self.idx_dxf_color + 1) % len(self.color_palette)

        elif m == 'MEAS_COLOR':
            self.idx_meas_color = (self.idx_meas_color + 1) % len(self.color_palette)

        elif m == 'CALIB_COLOR':
            self.idx_calib_color = (self.idx_calib_color + 1) % len(self.color_palette)

        elif m == 'CROSS_COLOR':
            self.idx_cross_color = (self.idx_cross_color + 1) % len(self.color_palette)

        elif m == 'CROSS_UNDO':
            if self.crosshairs:
                self.crosshairs.pop()

        elif m == 'MEAS_UNDO':
            if self.measure_p2:
                self.measure_p2 = None
                self.measure_p1 = None
            elif self.measure_p1:
                self.measure_p1 = None
            elif self.measurements:
                self.measurements.pop()

        elif m == 'CAM_SETTINGS':
            # 카메라 드라이버의 설정 창 (초점, 노출, 선명도 등) - 지원하는 카메라만 열림
            if self.cap is not None and self.loaded_frame is None:
                if not self.cap.set(cv2.CAP_PROP_SETTINGS, 1):
                    self._message("카메라 설정", "이 카메라는 설정 창을 지원하지 않습니다.")

        elif m == 'VIEW_FIT':
            self._reset_view()

        elif m == 'TPL_ADD':
            self._add_template()

        elif m == 'TPL_EDIT':
            self._edit_template()

        elif m == 'TPL_GAUGE':
            self._add_template(gauge=True)

        elif m == 'EDGE_SNAP':
            self.edge_snap = not self.edge_snap

        elif m == 'SAVE_IMG':
            if self.last_frame is not None:
                # 화면 캡처가 아니라 원본 해상도 영상 위에 측정선·도면을 다시 그려서 저장
                full = self.last_frame.copy()
                unit = max(1.0, self.frame_w / self.view_w)
                self._draw_overlays(full, lambda p: np.asarray(p, dtype=np.float64), unit, live=False)
                self.draw_weight_overlay(full, unit)
                root = self._dialog_root()
                path = filedialog.asksaveasfilename(
                    defaultextension=".jpg",
                    initialfile=f'검사결과_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg',
                    parent=root
                )
                root.destroy()
                if path:
                    try:
                        res, buffer = cv2.imencode('.jpg', full,
                                                   [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                        if res:
                            with open(path, "wb") as f:
                                f.write(buffer.tobytes())
                            self._message("저장 완료", "이미지가 성공적으로 저장되었습니다.")
                    except Exception as ex:
                        self._message("저장 실패", f"저장 오류:\n{ex}")

        elif m == 'LOAD_DXF':
            root = self._dialog_root()
            path = filedialog.askopenfilename(filetypes=[("DXF 도면", "*.dxf")], parent=root)
            root.destroy()
            if path:
                self.load_dxf_action(path)

        elif m == 'DXF_NEXT':
            if self.drawings:
                cur = self.dxf_active if self.dxf_active is not None else -1
                self.dxf_active = (cur + 1) % len(self.drawings)

        elif m == 'DXF_DEL':
            i = self.dxf_active
            if i is None or not (0 <= i < len(self.drawings)):
                self._message("도면 빼기", "화면에 불러온 도면이 없습니다.")
            else:
                target = self.drawings[i]

                def remove():
                    if target in self.drawings:
                        j = self.drawings.index(target)
                        self.drawings.pop(j)
                        self.dxf_active = min(j, len(self.drawings) - 1) if self.drawings else None

                self._confirm("도면 빼기", f"도면 {i + 1} ({target['name']})을 화면에서 뺄까요?\n"
                                         "(DXF 파일은 지워지지 않습니다)", remove)

        elif m == 'DXF_REAL':
            if self.calib_px_per_mm:
                self._set_all_drawings_real_size()
            else:
                self._message("도면 실측크기", "먼저 캘리브레이션을 해야 도면을 실제 치수로 표시할 수 있습니다.")

        elif m == 'CLEAR':
            self.measurements = []
            self.measure_p1 = None
            self.measure_p2 = None
            self.crosshairs = []
            self.templates = []
            self.tpl_selected = None
            self.fixed_calib_line = None
            self.calib_temp_data = None

        elif m == 'SCALE_CONNECT':
            if self.scale_connected:
                self.disconnect_scale()
                self._message("저울", "저울 연결을 해제했습니다.")
            else:
                if not SERIAL_AVAILABLE:
                    self._message(
                        "저울 연결",
                        "저울이 연결되어 있지 않습니다.\n\n"
                        "실제 저울 연결 방법:\n"
                        "1. pip install pyserial\n"
                        "2. 저울 RS-232 → USB 변환 케이블 연결\n"
                        "3. 장치관리자에서 COM 포트 확인\n"
                        "4. 이 버튼을 다시 클릭"
                    )
                else:
                    ports = [p.device for p in serial.tools.list_ports.comports()]
                    if not ports:
                        self._message("저울", "연결된 COM 포트가 없습니다.")
                        return
                    # 포트를 직접 입력하지 않고 목록에서 고름 (기존 기록에 있던 COM4가 있으면 먼저 선택)
                    self._pick("저울 연결", ports, lambda i: self.connect_scale(ports[i]),
                               sel=ports.index('COM4') if 'COM4' in ports else 0,
                               text="저울이 연결된 COM 포트를 고르세요.")

        elif m == 'SCALE_SAVE':
            self.save_weight()

        elif m == 'ZOOM_IN':
            self.current_mode = 'ZOOM'
            self._apply_crosshair_zoom(0.8)

        elif m == 'ZOOM_OUT':
            self.current_mode = 'ZOOM'
            self._apply_crosshair_zoom(-0.8)

        elif m == 'QUIT':
            self.is_running = False

        else:
            self.current_mode = m
            self.measure_p1 = None
            self.measure_p2 = None
            if self.crosshairs:
                self._select_last_crosshair()
            if m != 'CROSS':
                self.cross_preview_pos = None

    # ──────────────────────────────────────────────
    # 메인 루프
    # ──────────────────────────────────────────────
    def run(self):
        cv2.namedWindow('Vision Inspector', cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback('Vision Inspector', self.mouse_callback)

        window_shown = False
        while self.is_running:
            # 창이 처음 그려지기 전에는 '닫힘'으로 잘못 판단될 수 있어 첫 표시 이후에만 확인
            if window_shown and cv2.getWindowProperty('Vision Inspector', cv2.WND_PROP_VISIBLE) < 1:
                break

            self._apply_pending_camera()

            if self.cap is None:
                cv2.waitKey(1)
                continue

            if self.loaded_frame is not None:
                frame = self.loaded_frame.copy()
            elif self.is_frozen:
                frame = self.frozen_frame.copy()
            else:
                ret, frame = self.cap.read()
                if not ret:
                    cv2.waitKey(1)   # 카메라가 끊겨도 창이 '응답 없음'으로 굳지 않게 이벤트 처리
                    continue

            self.last_frame = frame
            fh, fw = frame.shape[:2]
            if (fw, fh) != (self.frame_w, self.frame_h):
                # 사진 불러오기·라이브 복귀 등으로 영상 크기가 바뀌면 확대 상태를 처음으로
                self.frame_w, self.frame_h = fw, fh
                self._reset_view()
            self._clamp_view()

            # ── 확대된 영상 + 오버레이 (선은 화면 기준 1픽셀로 그림) ──
            view = self._render_view(frame)
            self._draw_overlays(view, self._f2s_arr, unit=1.0, live=True)
            self.draw_weight_overlay(view, unit=1.0)

            # ── 화면 출력 ─────────────────────────
            display_img = np.zeros((self.view_h, self.total_w, 3), dtype=np.uint8)
            display_img[self.cam_y_offset:self.cam_y_offset + self.cam_display_h, :self.view_w] = view
            display_img = self.draw_ui(display_img)

            cv2.imshow('Vision Inspector', display_img)
            window_shown = True
            key_ex = cv2.waitKeyEx(1)
            if key_ex != -1 and self._modal is not None:
                self._modal_key(key_ex)   # 입력판이 열려 있으면 키보드는 입력판으로
                continue
            key = key_ex & 0xFF
            if key == ord('q'):
                break
            elif key in (ord('f'), ord('F')):
                self._reset_view()
            elif key in (ord('e'), ord('E')):
                self.edge_snap = not self.edge_snap

        self.cap.release()
        cv2.destroyAllWindows()
        sys.exit()


if __name__ == "__main__":
    inspector = VisionInspector()
    inspector.run()
