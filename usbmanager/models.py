"""데이터 모델과 선택 항목 정의."""

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# 관리 항목 선택지
STATUS_CHOICES = ("사용중", "보관", "대여중", "분실", "폐기")
SECURITY_CHOICES = ("일반", "대외비", "기밀")
PURPOSE_SUGGESTIONS = ("업무용", "백업", "자료반출", "설치/부팅", "개인용", "기타")
DEVICE_TYPE_CHOICES = ("USB메모리", "SD카드", "CF카드", "외장HDD/SSD", "기타")

# 종류별 관리번호 접두어 (SD 카드는 SD-001 로 제안된다)
ASSET_PREFIXES = {
    "USB메모리": "USB-",
    "SD카드": "SD-",
    "CF카드": "CF-",
    "외장HDD/SSD": "HDD-",
    "기타": "ETC-",
}
DEFAULT_ASSET_PREFIX = "USB-"


def asset_prefix(device_type: str) -> str:
    return ASSET_PREFIXES.get(device_type, DEFAULT_ASSET_PREFIX)

# MSFT_PhysicalDisk.BusType 값
BUS_USB = 7
BUS_SD = 12
BUS_MMC = 13

# 모델명/장치 식별자를 토막냈을 때 나오는 메모리카드 표시어
_SD_TOKENS = frozenset({"SD", "MMC", "SDHC", "SDXC", "MICROSD", "SDCARD", "MINISD", "TF"})
_CF_TOKENS = frozenset({"CF", "CFAST", "COMPACTFLASH"})
_TOKEN_SPLIT = re.compile(r"[^A-Z0-9]+")


def _tokens(*texts: str) -> set[str]:
    """'Generic- Micro SD/M2 USB Device' -> {GENERIC, MICRO, SD, M2, USB, DEVICE}

    'SanDisk'가 'SD'로 오인되지 않도록 부분 문자열이 아닌 토큰 단위로 비교한다.
    """
    result: set[str] = set()
    for text in texts:
        result.update(t for t in _TOKEN_SPLIT.split((text or "").upper()) if t)
    return result


def classify_device_type(
    model: str = "",
    pnp_device_id: str = "",
    bus_type: Optional[int] = None,
    media_type: str = "",
) -> str:
    """장치가 USB 메모리인지 SD 카드인지 등을 판별한다.

    USB 카드리더에 꽂힌 SD 카드도 BusType은 USB(7)로 잡히므로,
    버스 종류만으로는 구분되지 않는다. 모델명과 장치 식별자에 남는
    'SD/MMC', 'Micro SD' 같은 표시어를 함께 본다.
    """
    if bus_type in (BUS_SD, BUS_MMC):  # 노트북 내장 SD 리더
        return "SD카드"

    tokens = _tokens(model, pnp_device_id)
    if tokens & _SD_TOKENS:
        return "SD카드"
    if tokens & _CF_TOKENS:
        return "CF카드"
    if "FIXED" in (media_type or "").upper():  # 외장 케이스에 담긴 HDD/SSD
        return "외장HDD/SSD"
    if bus_type == BUS_USB or "REMOVABLE" in (media_type or "").upper():
        return "USB메모리"
    return "기타"


@dataclass
class UsbRecord:
    """관리 대장에 등록된 USB/SD 카드 한 건."""

    id: Optional[int] = None
    # 사용자 입력 항목
    asset_no: str = ""          # 관리번호
    label: str = ""             # 라벨/별칭
    device_type: str = ""       # 종류 (USB메모리 / SD카드 등, 자동 판별 후 수정 가능)
    owner: str = ""             # 담당자
    department: str = ""        # 부서
    purpose: str = ""           # 용도
    security_level: str = "일반"  # 보안등급
    status: str = "사용중"        # 상태
    note: str = ""              # 비고
    # 장치에서 자동 수집한 항목
    device_key: str = ""        # PNPDeviceID (장치/슬롯 식별자)
    serial_number: str = ""     # 장치 시리얼번호
    volume_serial: str = ""     # 볼륨 일련번호 (SD 카드 등 매체 식별용)
    model: str = ""             # 모델명
    capacity_bytes: Optional[int] = None
    file_system: str = ""
    # 날짜 이력
    registered_at: str = ""     # 등록일
    updated_at: str = ""        # 최종수정일
    last_seen_at: str = ""      # 최종연결일


@dataclass
class ScannedVolume:
    """연결된 장치의 볼륨(드라이브) 정보."""

    drive_letter: str = ""
    volume_label: str = ""
    file_system: str = ""
    volume_size: Optional[int] = None
    free_space: Optional[int] = None
    volume_serial: str = ""


@dataclass
class ScannedDevice:
    """현재 PC에 연결되어 있는 USB 저장장치."""

    device_key: str = ""
    model: str = ""
    serial_number: str = ""
    size: Optional[int] = None
    media_type: str = ""
    interface: str = ""
    bus_type: Optional[int] = None
    volumes: list[ScannedVolume] = field(default_factory=list)

    @property
    def primary_volume(self) -> Optional[ScannedVolume]:
        return self.volumes[0] if self.volumes else None

    @property
    def volume_serial(self) -> str:
        vol = self.primary_volume
        return vol.volume_serial if vol else ""

    @property
    def file_system(self) -> str:
        """파티션이 여러 개면 'FAT32, NTFS'처럼 모두 보여준다."""
        seen: list[str] = []
        for vol in self.volumes:
            if vol.file_system and vol.file_system not in seen:
                seen.append(vol.file_system)
        return ", ".join(seen)

    @property
    def device_type(self) -> str:
        return classify_device_type(
            model=self.model,
            pnp_device_id=self.device_key,
            bus_type=self.bus_type,
            media_type=self.media_type,
        )

    @property
    def drive_letters(self) -> str:
        return ", ".join(v.drive_letter for v in self.volumes if v.drive_letter)

    @property
    def volume_label(self) -> str:
        return ", ".join(v.volume_label for v in self.volumes if v.volume_label)

    @property
    def capacity_bytes(self) -> Optional[int]:
        """디스크 용량. 없으면 볼륨 용량으로 대체한다."""
        if self.size:
            return self.size
        vol = self.primary_volume
        return vol.volume_size if vol else None

    @property
    def is_media_present(self) -> bool:
        """카드리더기 빈 슬롯처럼 매체가 없는 상태인지 구분."""
        return bool(self.volumes) or bool(self.size)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ScannedDevice":
        raw_volumes = data.get("volumes") or []
        if isinstance(raw_volumes, dict):  # PowerShell이 단일 항목을 객체로 직렬화하는 경우
            raw_volumes = [raw_volumes]
        volumes = [
            ScannedVolume(
                drive_letter=(v.get("drive_letter") or "").strip(),
                volume_label=(v.get("volume_label") or "").strip(),
                file_system=(v.get("file_system") or "").strip(),
                volume_size=v.get("volume_size"),
                free_space=v.get("free_space"),
                volume_serial=(v.get("volume_serial") or "").strip().upper(),
            )
            for v in raw_volumes
        ]
        bus_type = data.get("bus_type")
        return cls(
            device_key=(data.get("pnp_device_id") or "").strip().upper(),
            model=" ".join((data.get("model") or "").split()),
            serial_number=(data.get("serial_number") or "").strip(),
            size=data.get("size"),
            media_type=(data.get("media_type") or "").strip(),
            interface=(data.get("interface") or "").strip(),
            bus_type=int(bus_type) if bus_type is not None else None,
            volumes=volumes,
        )
