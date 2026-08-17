"""Windows에 연결된 USB / SD 카드 장치를 조회한다.

WMI(Win32_DiskDrive)를 PowerShell로 질의하므로 외부 패키지가 필요 없다.
"""

import json
import subprocess
import sys
from pathlib import Path

from .models import ScannedDevice

SCRIPT_PATH = Path(__file__).with_name("scan.ps1")
SCAN_TIMEOUT_SEC = 30

# 콘솔 창이 깜빡이지 않도록 숨긴다.
_CREATE_NO_WINDOW = 0x08000000


class ScanError(RuntimeError):
    """장치 조회 실패."""


def scan_devices() -> list[ScannedDevice]:
    """현재 연결된 USB 저장장치 목록을 반환한다.

    카드리더기의 빈 슬롯도 장치로 잡히므로 매체가 없는 항목은 제외한다.
    """
    if sys.platform != "win32":
        raise ScanError("이 프로그램은 Windows에서만 장치를 조회할 수 있습니다.")
    if not SCRIPT_PATH.exists():
        raise ScanError(f"스캔 스크립트를 찾을 수 없습니다: {SCRIPT_PATH}")

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT_PATH),
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=SCAN_TIMEOUT_SEC,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise ScanError("PowerShell을 찾을 수 없습니다.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanError("장치 조회 시간이 초과되었습니다.") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise ScanError(f"장치 조회에 실패했습니다.\n{detail[:500]}")

    stdout = proc.stdout.decode("utf-8", "replace").strip()
    if not stdout:
        return []

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ScanError(f"장치 정보를 해석하지 못했습니다: {exc}") from exc

    raw_devices = payload.get("devices") or []
    if isinstance(raw_devices, dict):  # 단일 장치일 때 객체로 직렬화되는 경우 보정
        raw_devices = [raw_devices]

    devices = [ScannedDevice.from_json(item) for item in raw_devices]
    return [d for d in devices if d.is_media_present]
