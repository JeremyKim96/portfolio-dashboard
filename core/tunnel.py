"""Cloudflare Tunnel 외부 URL 추출.

autostart.bat이 띄운 cloudflared 프로세스가 `tunnel.log`에 출력하는
trycloudflare.com URL을 파싱한다. PC 부팅 후 매번 새 URL이 발급되므로
사이드바에 표시하여 사용자가 폰 즐겨찾기를 갱신할 수 있게 한다.
"""
from __future__ import annotations

import re
from pathlib import Path

TUNNEL_LOG = Path(__file__).resolve().parent.parent / "tunnel.log"
_URL_PATTERN = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")


def public_url() -> str | None:
    """tunnel.log 에서 가장 최근 외부 URL 추출. 없으면 None."""
    if not TUNNEL_LOG.exists():
        return None
    try:
        text = TUNNEL_LOG.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    matches = _URL_PATTERN.findall(text)
    return matches[-1] if matches else None


def tunnel_log_mtime():
    """로그 파일 마지막 수정 시각 (없으면 None)."""
    if not TUNNEL_LOG.exists():
        return None
    from datetime import datetime
    try:
        return datetime.fromtimestamp(TUNNEL_LOG.stat().st_mtime)
    except Exception:
        return None
