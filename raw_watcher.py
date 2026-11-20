"""새 RAW 파일 감지 → '폰(클라우드)에 올릴까요?' 팝업.

- 윈도우 로그온 시 자동 실행 (작업 스케줄러 RawUploadWatcher, pythonw = 창 없음)
- RAW 폴더를 30초마다 확인. 아직 올리지 않은 최신 RAW 가 있으면 확인 창을 띄움
- [예] → push_to_cloud 실행 후 결과 알림 / [아니오] → 그 파일은 다시 묻지 않음
  (PC 대시보드 상단에는 '업로드 필요' 안내가 계속 표시되고, 바탕화면 아이콘으로 언제든 업로드 가능)
- 업로드는 항상 사용자가 [예]를 눌렀을 때만 실행됨
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
STATE = ROOT / "cloud_data" / "watcher_state.json"
LOG = ROOT / "cloud_data" / "watcher.log"

MB_YESNO, MB_OK = 0x04, 0x00
MB_ICONQUESTION, MB_ICONINFO, MB_ICONERROR = 0x20, 0x40, 0x10
MB_TOPMOST, MB_SETFOREGROUND = 0x40000, 0x10000
IDYES = 6


def _box(text: str, title: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST | MB_SETFOREGROUND)


def _log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _stable(p: Path, wait: float = 4.0) -> bool:
    """파일 저장이 끝났는지 (크기 변화 없음)."""
    try:
        a = p.stat().st_size
        time.sleep(wait)
        return a == p.stat().st_size and a > 0
    except OSError:
        return False


def check_once() -> None:
    from core.datasource import read_last_push
    from core.raw_loader import latest_raw_path

    p = latest_raw_path()
    if p is None:
        return
    pushed = read_last_push().get("raw_name")
    st = _state()
    if p.name == pushed or p.name in st.get("asked", []):
        return
    if not _stable(p):
        return

    st.setdefault("asked", []).append(p.name)
    st["asked"] = st["asked"][-50:]
    _save_state(st)

    ans = _box(f"새 자산 RAW 파일이 감지됐어요.\n\n📄 {p.name}\n\n"
               "폰(클라우드)에 지금 올릴까요?\n(약 10~30초 소요)",
               "자산 대시보드 · 클라우드 업로드", MB_YESNO | MB_ICONQUESTION)
    if ans != IDYES:
        _log(f"skip {p.name}")
        return
    try:
        from push_to_cloud import push
        ok, msg = push(log=_log)
    except Exception as e:  # noqa: BLE001
        ok, msg = False, f"오류: {e}"
    _log(("OK " if ok else "FAIL ") + msg.replace("\n", " | "))
    _box(("✅ " if ok else "⚠️ ") + msg, "자산 대시보드 · 클라우드 업로드",
         MB_OK | (MB_ICONINFO if ok else MB_ICONERROR))


def main() -> None:
    # pythonw 는 콘솔이 없음 → 출력은 로그 파일로
    if sys.stdout is None or sys.stderr is None:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            f = open(LOG, "a", encoding="utf-8")  # noqa: SIM115
            sys.stdout = sys.stdout or f
            sys.stderr = sys.stderr or f
        except Exception:
            pass
    # 중복 실행 방지 (이름 있는 뮤텍스)
    ctypes.windll.kernel32.CreateMutexW(None, False, "PortfolioRawWatcherMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    _log("watcher start")
    time.sleep(20)  # 로그온 직후 여유
    while True:
        try:
            check_once()
        except Exception as e:  # noqa: BLE001
            _log(f"error {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
