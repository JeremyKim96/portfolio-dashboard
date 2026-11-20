"""PC에서 실행: 최신 데이터를 클라우드(GitHub)로 밀어넣는다.

흐름:
  1. RAW + portfolio.xlsx 를 읽어 암호화 번들(cloud_data/portfolio_data.enc) 생성
  2. git add / commit / push → Streamlit Cloud가 자동으로 새 데이터 반영
  3. 올린 RAW 파일명을 cloud_data/last_push.json 에 기록 (PC 대시보드 '업로드 필요' 알림용)

실행 방법: 바탕화면 「자산 클라우드 올리기」 더블클릭, 또는 새 RAW 감지 팝업에서 [예].
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENC_REL = "cloud_data/portfolio_data.enc"


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def push(log=print) -> tuple[bool, str]:
    """업로드 실행. (성공여부, 메시지)."""
    sys.path.insert(0, str(ROOT))
    from core.datasource import export_bundle, write_last_push
    from core.raw_loader import latest_raw_path

    raw = latest_raw_path()
    raw_name = raw.name if raw else None

    # 1) 데이터 export (암호화)
    path = export_bundle()
    log(f"[1/3] 암호화 데이터 생성: {path.name} ({path.stat().st_size:,} bytes) · RAW: {raw_name}")

    # 2) git 존재 확인
    code, _ = run(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        return False, "git 저장소가 아닙니다. 클로드에게 '클라우드 배포 설정'을 요청하세요."

    # 3) commit + push — 암호문 .enc 만 (gitignore 부정규칙 이슈로 -f 필요)
    run(["git", "add", "-f", ENC_REL])
    msg = f"data update {datetime.now():%Y-%m-%d %H:%M}"
    code, out = run(["git", "commit", "-m", msg, "--", ENC_REL])
    if code != 0 and ("nothing to commit" in out or "no changes added" in out):
        log("[2/3] 변경된 데이터 없음 (이미 최신).")
        write_last_push(raw_name)
        return True, "이미 최신 데이터가 올라가 있습니다."
    if code != 0:
        return False, "커밋 실패:\n" + out[-600:]
    log(f"[2/3] 커밋: {msg}")

    code, out = run(["git", "push"])
    if code != 0:
        # 원격에 새 커밋(코드 업데이트)이 있으면 합친 뒤 재시도
        run(["git", "pull", "--rebase", "--autostash"])
        code, out = run(["git", "push"])
    if code != 0:
        return False, "업로드(push) 실패:\n" + out[-600:]
    write_last_push(raw_name)
    log("[3/3] 클라우드 전송 완료! 1~2분 후 폰에서 새로고침하면 반영됩니다.")
    return True, f"업로드 완료 ({raw_name})\n1~2분 후 폰에서 새로고침하면 반영됩니다."


def main() -> int:
    try:
        ok, msg = push()
    except Exception as e:  # noqa: BLE001
        ok, msg = False, f"오류: {e}"
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
