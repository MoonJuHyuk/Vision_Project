import urllib.request
import ssl
import sys
import os

GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/MoonJuHyuk/Vision_Project/main/Vison%20Camera.py"
)

# exe로 실행했는지(frozen), 파이썬으로 launcher.py를 실행했는지 구분
IS_EXE = getattr(sys, 'frozen', False)

# 런처 exe와 같은 폴더에 캐시 저장
if IS_EXE:
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

cache_path = os.path.join(base_dir, "_vision_cache.py")
local_path = os.path.join(base_dir, "Vison Camera.py")

_last_error = ""


def _is_valid_code(code):
    """문법 오류가 없는 코드인지 확인 (받다가 끊긴 파일로 캐시를 덮어쓰지 않기 위함)"""
    try:
        compile(code, "Vison Camera.py", "exec")
        return True
    except SyntaxError:
        return False


def download_latest():
    global _last_error
    # SSL 인증서 검증 우회 (일부 윈도우 환경에서 필요)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(
            GITHUB_RAW_URL,
            headers={"Cache-Control": "no-cache", "User-Agent": "VisionLauncher/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = resp.read()
        code = data.decode("utf-8")
        if not _is_valid_code(code):
            _last_error = "받은 파일이 손상되었습니다."
            return None
        # 받은 내용을 그대로(바이너리) 저장해야 윈도우에서 줄바꿈이 두 번 들어가지 않음
        try:
            with open(cache_path, "wb") as f:
                f.write(data)
        except OSError:
            pass   # 캐시 저장에 실패해도 이번 실행은 계속
        return code
    except Exception as e:
        _last_error = str(e)
        return None


def load_cache():
    if not os.path.exists(cache_path):
        return None
    with open(cache_path, "rb") as f:
        code = f.read().decode("utf-8")
    # 이전 런처가 줄바꿈을 두 번 넣어 저장한 캐시(\r\r\n)도 복구해서 사용
    code = code.replace("\r\r\n", "\n")
    return code if _is_valid_code(code) else None


def _show_message(kind, title, text):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    getattr(messagebox, kind)(title, text)
    root.destroy()


if __name__ == "__main__":
    code = None

    # 개발용: 파이썬으로 launcher.py를 실행하면 같은 폴더의 수정본을 먼저 사용
    # (exe는 항상 GitHub 최신본을 받아 모든 PC가 같은 버전을 쓰도록 함)
    if not IS_EXE and os.path.exists(local_path):
        with open(local_path, "r", encoding="utf-8") as f:
            code = f.read()

    if code is None:
        code = download_latest()

    if code is None:
        code = load_cache()
        if code is not None:
            _show_message(
                "showwarning", "오프라인 실행",
                "최신 버전을 받지 못해 마지막으로 받은 버전으로 실행합니다.\n\n"
                f"원인: {_last_error}"
            )

    if code is None:
        _show_message(
            "showerror", "실행 오류",
            f"최신 버전 다운로드 실패:\n{_last_error}\n\n"
            "인터넷 연결을 확인하거나 관리자에게 문의하세요."
        )
        sys.exit(1)

    try:
        exec(compile(code, "Vison Camera.py", "exec"), {"__name__": "__main__"})
    except Exception as e:
        import traceback
        _show_message("showerror", "실행 오류", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        sys.exit(1)
