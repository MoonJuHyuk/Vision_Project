import urllib.request
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


def download_latest():
    try:
        req = urllib.request.Request(
            GITHUB_RAW_URL,
            headers={"Cache-Control": "no-cache", "User-Agent": "VisionLauncher/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            code = resp.read().decode("utf-8")
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(code)
        return code
    except Exception as e:
        print(f"[런처] 최신 버전 다운로드 실패: {e}")
        return None


def load_cache():
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    return None


if __name__ == "__main__":
    print("[런처] 최신 버전 확인 중...")
    code = download_latest()

    if code is None:
        print("[런처] 캐시 버전으로 실행합니다.")
        code = load_cache()

    if code is None:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "실행 오류",
            "인터넷 연결이 없고 캐시 파일도 없습니다.\n\n"
            "최초 실행은 인터넷 연결이 필요합니다."
        )
        sys.exit(1)

    exec(compile(code, "Vison Camera.py", "exec"), {"__name__": "__main__"})
