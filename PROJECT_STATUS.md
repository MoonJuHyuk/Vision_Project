# Vision Project 진행 상황

작성일: 2026-08-20
최종 수정: 2026-09-30

## ▶ 다음 작업 (여기서부터 이어서 진행)

**할 일: 일반 PC(인텔/AMD)에서 새 `Vision_Launcher.exe` 만들기 → 각 PC에 배포**

- 이유: 2026-09-30에 `launcher.py`를 고쳐서, 인터넷이 끊겨도 마지막으로 받은 버전으로 실행되게 했음. 이 수정은 exe 안에 들어가므로 exe를 새로 만들어야 적용됨.
- 프로그램 기능(`Vison Camera.py`) 수정은 이미 GitHub에 올라가 모든 PC에 적용됨 (프로그램을 다시 켜면 적용).
- 순서: 아래 **"exe 다시 만들기"** 1~4단계

### 다른 PC에서 이 프로젝트 열기

방법 A - GitHub에서 받기 (추천, NAS 경로 문제 없음)

```powershell
cd C:\
git clone https://github.com/MoonJuHyuk/Vision_Project.git
code C:\Vision_Project
```

방법 B - NAS 폴더를 VS Code로 열기: `Synology\★문주혁\Python\Vision_Project`
(단, exe 빌드는 NAS가 아닌 로컬 폴더에서 해야 함)

VS Code에서 Claude Code를 열고 "PROJECT_STATUS.md 보고 다음 작업 이어서 하자"라고 하면 됩니다.

## 프로젝트 목적

카메라 영상 위에 DXF 도면을 겹쳐 표시하고, 화면에서 측정과 캘리브레이션을 수행하며, 정밀저울의 무게를 읽고 기록하는 Windows용 비전 검사 프로그램입니다.

## 현재 상태

- Git 브랜치: `main`
- 원격 기준 최신 커밋: `6f58048 Launcher: run last downloaded version when offline`
- 현재 프로그램 본체: `Vison Camera.py`
- 실행 진입점: `launcher.py`
- PyInstaller 설정: `Vision_Launcher.spec`

### 2026-09-30 변경 내용

- 도면 확대/축소가 곱셈 방식으로 바뀌어 배율과 무관하게 잘 동작
- 십자선 가운데 근처를 누르면 십자선, 빈 곳을 누르면 도면을 이동/확대/회전
- 캘리브레이션 입력창을 띄운 사이 시작점이 지워져 에러 나던 문제 수정
- 불러온 사진을 카메라 비율에 맞게 검은 여백으로 채워, 사진과 십자선이 찌그러지지 않게 함
- **측정 기준(캘리브레이션)과 도면 표시 배율을 분리**: 도면을 확대해도 측정값은 바뀌지 않음
  - 캘리브레이션하면 도면이 실제 치수 크기로 자동 표시
  - 도면이 실측과 다르면 화면에 "도면 배율 ≠ 실측" 경고, `도면 실측크기` 버튼으로 복귀
  - 캘리브레이션 전 측정값은 `px (NO CAL)`로 표시하고 "미보정" 경고
  - 카메라 전환 / 사진 불러오기 / 사진→라이브 전환 시 캘리브레이션 초기화
- **저울 시뮬레이션 제거**: 연결 안 되면 `-- g` / `미연결` 표시, 무게 저장 차단
- exe로 실행할 때 무게 저장 시 에러 나던 문제 수정
- 저울 데이터가 끊겨 들어올 때 12.34 → 12.3, 4.0처럼 잘못 읽던 문제 수정
- 저울 연결이 끊기면 마지막 값을 지워 실제 값처럼 저장되지 않게 함
- 카메라가 끊겨도 창이 "응답 없음"으로 멈추지 않게 함
- 카메라를 못 찾으면 조용히 꺼지지 않고 안내창 표시
- 런처: 인터넷이 끊기면 마지막으로 받은 버전으로 실행 (새 exe를 만들어 배포해야 적용됨, 아래 "exe 다시 만들기" 참고)

## 구현된 기능

### 카메라와 화면

- 사용 가능한 카메라 자동 검색 및 연결
- 카메라 전환 및 장치명 표시
- 라이브 영상 정지/재개
- 사진 파일 불러오기
- 이미지 저장
- PAN 이동, 확대/축소, 회전

### 도면과 측정

- DXF 파일 불러오기
- DXF 오버레이 색상 변경
- 두 점 직선 측정
- 수평/수직 측정
- 측정 색상 변경 및 마지막 측정 취소
- 전체 오버레이 삭제
- 마우스 기반 십자선 배치
- 십자선 선택, 회전, 크기 조절, 색상 변경 및 취소

### 캘리브레이션

- 두 점 기준 캘리브레이션 (측정 기준 px/mm를 정함)
- 캘리브레이션 색상 변경
- 측정값은 캘리브레이션 값으로만 계산하고, 도면 확대/축소와는 무관
- 캘리브레이션 시 도면을 실제 치수 크기로 표시, `도면 실측크기` 버튼으로 복귀

### 정밀저울

- `pyserial`이 있으면 RS-232 연결 지원
- 기본 통신 설정: `9600, 8N1`, timeout 1초
- 실시간 수신 문자열에서 무게값 파싱
- 연결이 없으면 `-- g` / `미연결` 표시, 무게 저장 불가 (가짜 값 없음)
- 무게와 측정 개수를 실행 파일과 같은 폴더의 `weight_log.csv`에 저장
- 기존 로그에는 COM4 기록과 예전 시뮬레이션 기록이 섞여 있음

## 실행 방법

### 현장 PC (exe)

`Vision_Launcher.exe`를 더블클릭합니다. exe는 켤 때마다 GitHub에서 최신 `Vison Camera.py`를 받아 실행합니다.

- **exe는 로컬 폴더(예: `C:\VisionInspector\`)에 두고 실행**합니다. NAS(Z: 드라이브)에서 바로 실행하면 켜지자마자 꺼지는 문제가 있었습니다.
- 받은 코드는 exe 옆에 `_vision_cache.py`(예비 파일)로 저장됩니다. 인터넷이 끊기면 새 런처는 이 예비 파일로 실행합니다.
- GitHub에 새 `Vison Camera.py`를 올리면, 각 PC는 프로그램을 **다시 켤 때** 새 버전이 됩니다.

### 개발 PC (수정본 시험)

프로젝트 폴더에서 아래처럼 실행하면 GitHub이 아니라 **이 폴더의 `Vison Camera.py`(수정본)**로 실행됩니다. GitHub에 올리기 전에 이 방법으로 먼저 확인합니다.

```powershell
.\.venv\Scripts\python.exe .\launcher.py
```

### 파일 역할

| 파일 | 역할 | 수정 후 적용 방법 |
|---|---|---|
| `Vison Camera.py` | 프로그램 기능 전체 | GitHub에 올리면 각 PC가 다음 실행 때 자동 적용 |
| `launcher.py` | 최신 `Vison Camera.py`를 받아 실행하는 도우미 | exe 안에 묶여 있으므로 **exe를 다시 만들어 배포**해야 적용 |
| `Vision_Launcher.spec` | exe를 만드는 설정 파일 (작업지시서) | 보통 수정할 일 없음 |
| `Vision_Launcher.exe` | `launcher.py` + 파이썬 + 필요 부품을 묶은 실행 파일 | - |

## 필요한 Python 패키지

소스에서 사용하는 주요 모듈은 다음과 같습니다.

- `opencv-python` (`cv2`)
- `numpy`
- `ezdxf`
- `Pillow`
- `pyserial` (실제 저울 연결 시 필요)

가상환경이 준비되어 있지 않거나 패키지가 빠져 있으면 다음처럼 설치합니다.

```powershell
python -m pip install opencv-python numpy ezdxf Pillow pyserial
```

## 하드웨어 및 파일 전제

- 카메라가 Windows에서 정상적으로 인식되어야 함
- 실제 저울 연결 시 올바른 COM 포트와 저울 출력 프로토콜이 필요함
- USB-Serial 케이블 드라이버는 `저울케이블 RS232 드라이버` 폴더에 있음
  - ARM64용 드라이버 ZIP
  - 일반 Windows용 드라이버 ZIP
- 저울은 `저울 연결` 버튼으로 COM 포트를 입력해 연결함
- DXF 파일은 사용자가 프로그램 안에서 선택해서 불러옴
- DXF 단위는 mm로 가정함 (인치 도면은 크기가 틀림), 블록(INSERT) 안의 도형은 표시되지 않음

## exe 다시 만들기 (빌드)

`launcher.py`를 고쳤을 때만 필요합니다. `Vison Camera.py`만 고쳤다면 GitHub에 올리는 것으로 충분합니다.

### 주의

- **반드시 일반 PC(인텔/AMD, x64)에서 만듭니다.** Surface 같은 ARM64 PC에서 만든 exe는 일반 PC에서 실행되지 않습니다.
  - 확인 방법: PowerShell에서 `$env:PROCESSOR_ARCHITECTURE` 입력 → `AMD64`가 나와야 함
- **NAS가 아닌 로컬 폴더에서 작업**합니다 (예: `C:\Vision_Project`).
- 기존 exe는 Python 3.14로 만들어졌습니다.

### 1단계: 준비 (처음 한 번만)

1. python.org에서 **Python 3.14 (Windows installer 64-bit)** 를 받아 설치합니다.
   - 설치 첫 화면에서 **"Add python.exe to PATH"** 에 체크합니다.
2. 프로젝트를 로컬 폴더로 받습니다 (위 "다른 PC에서 이 프로젝트 열기" 방법 A → `C:\Vision_Project`).
   `launcher.py`와 `Vision_Launcher.spec`이 모두 들어 있습니다.

### 2단계: exe 만들기

`C:\Vision_Project` 폴더에서 PowerShell(또는 VS Code 터미널)을 열고 차례로 실행합니다.

```powershell
# 이 폴더 전용 파이썬 환경 만들기 (처음 한 번만)
python -m venv .venv

# exe에 넣을 부품과 PyInstaller 설치 (처음 한 번만, 몇 분 걸림)
.\.venv\Scripts\python.exe -m pip install pyinstaller opencv-python numpy ezdxf Pillow pyserial

# exe 만들기 (launcher.py를 고칠 때마다 이것만 다시 실행)
.\.venv\Scripts\pyinstaller.exe .\Vision_Launcher.spec --noconfirm
```

완료되면 `C:\Vision_Project\dist\Vision_Launcher.exe`가 생깁니다 (약 120MB).

### 3단계: 새 exe 시험

1. `dist\Vision_Launcher.exe`를 더블클릭해 프로그램 창이 뜨는지 확인합니다.
2. 인터넷을 끊고 다시 실행해 "오프라인 실행 - 마지막으로 받은 버전으로 실행합니다" 안내 후 프로그램이 뜨는지 확인합니다.

### 4단계: 각 PC에 배포

1. 각 PC에서 실행 중인 프로그램을 닫습니다.
2. 새 `Vision_Launcher.exe`를 그 PC의 로컬 폴더(예: `C:\VisionInspector\`)에 복사해 기존 exe를 바꿉니다.
3. 바탕화면 바로가기가 그 exe를 가리키는지 확인합니다.
4. 인터넷이 연결된 상태로 한 번 실행합니다. 이때 예비 파일(`_vision_cache.py`)이 새로 저장되어, 이후에는 인터넷이 끊겨도 실행됩니다.

## 다음에 재개할 때 확인할 순서

1. `.\.venv\Scripts\python.exe .\launcher.py`로 실행
2. 카메라 자동 연결과 카메라 전환 목록 확인
3. 사진 및 DXF 불러오기 확인
4. PAN/ZOOM/회전, 십자선, 두 종류 측정, 캘리브레이션 확인
5. 실제 RS-232 저울을 연결해 COM 포트, 무게 표시, `weight_log.csv` 기록 확인
6. 이상이 없으면 GitHub에 올리고, `launcher.py`를 고쳤다면 exe를 다시 만들어 배포

## 우선 점검할 항목

- **새 exe 빌드 및 배포** (2026-09-30 런처 수정 반영, 위 "exe 다시 만들기" 참고)
- 실제 저울 모델의 출력 형식이 `_parse_weight()`가 처리하는 형식과 맞는지 확인 (저울 값 읽기는 가짜 데이터로만 시험함)
- 저울의 안정(ST)/흔들림(US) 표시를 구분하지 않아 흔들리는 중의 값도 저장될 수 있음
- 사진을 새로 불러와도 이전 측정선·십자선이 남음 (필요하면 `전체 삭제`)
- 측정 클릭 정밀도: 화면 1픽셀 ≈ 카메라 1.6픽셀 (보정 20px/mm 기준 클릭당 약 ±0.04mm)
- 캘리브레이션 결과가 현장 측정값과 일치하는지 확인
- `build`, `dist`, `__pycache__`, `weight_log.csv`는 Git에 올리지 않음 (exe는 용량이 커서 GitHub에 올리지 않음)
- `launcher.py`의 GitHub URL이 실제 배포 브랜치/파일 경로와 계속 일치하는지 확인
- 런처가 SSL 인증서 검사를 끄고 코드를 받음 (일부 PC 호환용). 보안상 가능하면 검사를 켜는 것이 좋음

## 참고 메모

- 파일명은 현재 `Vison Camera.py`로 유지되어 있습니다. `Vision`으로 이름을 바꾸려면 런처, spec, GitHub raw URL을 함께 수정해야 합니다.
- 프로그램 본체가 단일 대형 파일이므로, 기능 안정화 후 카메라/측정/저울/UI를 별도 모듈로 나누는 것을 고려할 수 있습니다.
- 현재 작업 트리에는 소스 외에 빌드 산출물과 로그 파일이 미추적 상태입니다. 다음 작업 시작 시 `git status`로 먼저 확인합니다.