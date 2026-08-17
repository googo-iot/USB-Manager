"""공용 유틸리티."""

from datetime import datetime
from typing import Optional


def now_iso() -> str:
    """초 단위까지의 현재 시각 문자열."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def format_bytes(size: Optional[int]) -> str:
    """바이트 수를 사람이 읽는 용량 문자열로 변환."""
    if not size:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit in ("B", "KB"):
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def short_date(value: str) -> str:
    """'2026-08-17 22:40:00' -> '2026-08-17'."""
    return value.split(" ")[0] if value else ""
