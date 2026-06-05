"""PC에서 실행: 최신 데이터를 클라우드(GitHub)로 밀어넣는다.

흐름:
  1. RAW + portfolio.xlsx 를 읽어 cloud_data/portfolio_data.json 생성 (export_bundle)
  2. git add / commit / push → Streamlit Cloud가 자동으로 새 데이터 반영

`push_to_cloud.bat` 더블클릭으로 실행.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    # 1) 데이터 export (암호화)
    from core.datasource import export_bundle
    path = export_bundle()
    print(f"[1/3] 암호화 데이터 생성: {path.name} ({path.stat().st_size:,} bytes)")

    # 2) git 존재 확인
    code, _ = run(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        print("[오류] 아직 git 저장소가 아닙니다. 최초 1회 배포 설정이 필요합니다.")
        print("       클로드에게 '클라우드 배포 설정'을 요청하세요.")
        return 1

    # 3) commit + push (암호문 .enc 만 — 공개 저장소에 올라가도 안전)
    run(["git", "add", "cloud_data/portfolio_data.enc"])
    msg = f"data update {datetime.now():%Y-%m-%d %H:%M}"
    code, out = run(["git", "commit", "-m", msg])
    if code != 0 and "nothing to commit" in out:
        print("[2/3] 변경된 데이터 없음 (이미 최신).")
        return 0
    print(f"[2/3] 커밋: {msg}")

    code, out = run(["git", "push"])
    if code != 0:
        print("[오류] push 실패:")
        print(out)
        return 1
    print("[3/3] 클라우드 전송 완료! 1~2분 후 폰에서 새로고침하면 반영됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
