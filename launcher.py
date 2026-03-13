import urllib.request
import ssl
import sys
import os

GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/MoonJuHyuk/Vision_Project/main/Vison%20Camera.py"
)

# 런처 exe와 같은 폴더에 캐시 저장
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

cache_path = os.path.join(base_dir, "_vision_cache.py")

_last_error = ""

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
            code = resp.read().decode("utf-8")
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(code)
        return code
    except Exception as e:
        _last_error = str(e)
        return None


def load_cache():
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    return None


if __name__ == "__main__":
    # 로컬 파일이 있으면 우선 사용, 없으면 GitHub 다운로드
    local_path = os.path.join(base_dir, "Vison Camera.py")
    if os.path.exists(local_path):
        with open(local_path, "r", encoding="utf-8") as f:
            code = f.read()
    else:
        code = download_latest()

    if code is None:
        code = load_cache()

    if code is None:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "실행 오류",
            f"최신 버전 다운로드 실패:\n{_last_error}\n\n"
            "인터넷 연결을 확인하거나 관리자에게 문의하세요."
        )
        sys.exit(1)

    try:
        exec(compile(code, "Vison Camera.py", "exec"), {"__name__": "__main__"})
    except Exception as e:
        import traceback
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("실행 오류", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        sys.exit(1)
