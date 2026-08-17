"""SQLite 저장소."""

import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import Iterable, Optional

from .models import (
    ASSET_NO_MAX,
    ScannedDevice,
    UsbRecord,
    asset_no_sequence,
    format_asset_no,
)
from .util import now_iso

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "usb_manager.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usb_devices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_no       TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    device_type    TEXT NOT NULL DEFAULT '',
    owner          TEXT NOT NULL DEFAULT '',
    department     TEXT NOT NULL DEFAULT '',
    purpose        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '사용중',
    note           TEXT NOT NULL DEFAULT '',
    device_key     TEXT NOT NULL DEFAULT '',
    serial_number  TEXT NOT NULL DEFAULT '',
    volume_serial  TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    capacity_bytes INTEGER,
    file_system    TEXT NOT NULL DEFAULT '',
    registered_at  TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    last_seen_at   TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usb_asset_no ON usb_devices (asset_no);
CREATE INDEX IF NOT EXISTS idx_usb_device_key ON usb_devices (device_key);
CREATE INDEX IF NOT EXISTS idx_usb_volume_serial ON usb_devices (volume_serial);
"""

# UsbRecord에서 id를 뺀 컬럼 목록 (INSERT/UPDATE에 사용)
_COLUMNS = [f.name for f in fields(UsbRecord) if f.name != "id"]
_FIELD_NAMES = {f.name for f in fields(UsbRecord)}

# 예전 버전에는 있었지만 지금은 쓰지 않는 컬럼
_DROPPED_COLUMNS = ("security_level",)


class DuplicateAssetNo(Exception):
    """관리번호 중복."""


class Database:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """예전 버전에서 만든 DB의 스키마를 현재 모델에 맞춘다."""
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(usb_devices)").fetchall()
        }
        for column in _COLUMNS:
            if column in existing:
                continue
            spec = "INTEGER" if column == "capacity_bytes" else "TEXT NOT NULL DEFAULT ''"
            self.conn.execute(f"ALTER TABLE usb_devices ADD COLUMN {column} {spec}")

        for column in _DROPPED_COLUMNS:
            if column in existing:
                self.conn.execute(f"ALTER TABLE usb_devices DROP COLUMN {column}")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ 조회
    def list_records(
        self,
        keyword: str = "",
        status: str = "",
        device_type: str = "",
    ) -> list[UsbRecord]:
        """키워드/상태/종류로 필터링한 목록을 관리번호 순으로 반환한다."""
        sql = "SELECT * FROM usb_devices"
        clauses: list[str] = []
        params: list[str] = []

        if keyword.strip():
            like = f"%{keyword.strip()}%"
            searchable = (
                "asset_no", "label", "device_type", "owner", "department",
                "purpose", "model", "serial_number", "volume_serial",
                "file_system", "note",
            )
            clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in searchable) + ")")
            params.extend([like] * len(searchable))

        if status:
            clauses.append("status = ?")
            params.append(status)

        if device_type:
            clauses.append("device_type = ?")
            params.append(device_type)

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY asset_no COLLATE NOCASE, id"

        rows = self.conn.execute(sql, params).fetchall()
        return [self._to_record(r) for r in rows]

    def get(self, record_id: int) -> Optional[UsbRecord]:
        row = self.conn.execute(
            "SELECT * FROM usb_devices WHERE id = ?", (record_id,)
        ).fetchone()
        return self._to_record(row) if row else None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM usb_devices").fetchone()[0]

    def used_sequences(self) -> set[int]:
        """이미 쓰이고 있는 관리번호의 숫자 부분."""
        rows = self.conn.execute("SELECT asset_no FROM usb_devices").fetchall()
        numbers = (asset_no_sequence(asset_no) for (asset_no,) in rows)
        return {n for n in numbers if n is not None}

    def next_asset_no(self) -> str:
        """MS001 형식의 다음 관리번호를 제안한다.

        쓰던 번호를 다시 쓰지 않도록 가장 큰 번호 다음을 고른다.
        MS999까지 다 찼으면 빈 번호를 찾아 채운다.
        """
        used = self.used_sequences()
        nxt = (max(used) + 1) if used else 1
        if nxt > ASSET_NO_MAX:
            gaps = set(range(1, ASSET_NO_MAX + 1)) - used
            if not gaps:
                return ""
            nxt = min(gaps)
        return format_asset_no(nxt)

    def is_asset_no_taken(self, asset_no: str, exclude_id: Optional[int] = None) -> bool:
        """이미 쓰이고 있는 관리번호인지. 수정 중인 자기 자신은 제외한다."""
        sql = "SELECT 1 FROM usb_devices WHERE asset_no = ?"
        params: list = [asset_no.strip()]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        return self.conn.execute(sql, params).fetchone() is not None

    # ------------------------------------------------------------------ 매칭
    def find_matching(self, device: ScannedDevice) -> Optional[UsbRecord]:
        """연결된 장치와 일치하는 등록 기록을 찾는다.

        1) 볼륨 일련번호가 같으면 같은 매체로 본다. (SD 카드 구분에 필요)
        2) 볼륨 일련번호로 못 찾으면 장치 식별자(PNPDeviceID)로 찾되,
           볼륨 일련번호가 기록되지 않은 기록만 대상으로 한다.
           카드리더기는 슬롯마다 식별자가 고정이라 다른 카드를 같은 것으로
           오인할 수 있기 때문이다.
        """
        if device.volume_serial:
            row = self.conn.execute(
                "SELECT * FROM usb_devices WHERE volume_serial = ? AND volume_serial <> ''",
                (device.volume_serial,),
            ).fetchone()
            if row:
                return self._to_record(row)

        if device.device_key:
            row = self.conn.execute(
                "SELECT * FROM usb_devices WHERE device_key = ?"
                " AND (volume_serial = '' OR ? = '') ORDER BY id LIMIT 1",
                (device.device_key, device.volume_serial),
            ).fetchone()
            if row:
                return self._to_record(row)

        return None

    # ------------------------------------------------------------------ 변경
    def insert(self, record: UsbRecord) -> int:
        stamp = now_iso()
        record.registered_at = record.registered_at or stamp
        record.updated_at = stamp
        placeholders = ", ".join("?" for _ in _COLUMNS)
        sql = f"INSERT INTO usb_devices ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
        try:
            cur = self.conn.execute(sql, [getattr(record, c) for c in _COLUMNS])
        except sqlite3.IntegrityError as exc:
            raise DuplicateAssetNo(f"관리번호 '{record.asset_no}'는 이미 등록되어 있습니다.") from exc
        self.conn.commit()
        record.id = cur.lastrowid
        return cur.lastrowid

    def update(self, record: UsbRecord) -> None:
        if record.id is None:
            raise ValueError("수정하려면 id가 필요합니다.")
        record.updated_at = now_iso()
        assignments = ", ".join(f"{c} = ?" for c in _COLUMNS)
        sql = f"UPDATE usb_devices SET {assignments} WHERE id = ?"
        try:
            self.conn.execute(sql, [getattr(record, c) for c in _COLUMNS] + [record.id])
        except sqlite3.IntegrityError as exc:
            raise DuplicateAssetNo(f"관리번호 '{record.asset_no}'는 이미 등록되어 있습니다.") from exc
        self.conn.commit()

    def delete(self, record_ids: Iterable[int]) -> int:
        ids = list(record_ids)
        if not ids:
            return 0
        marks = ", ".join("?" for _ in ids)
        cur = self.conn.execute(f"DELETE FROM usb_devices WHERE id IN ({marks})", ids)
        self.conn.commit()
        return cur.rowcount

    def mark_seen(self, record_ids: Iterable[int]) -> None:
        """연결이 확인된 기록의 최종연결일을 갱신한다."""
        ids = list(record_ids)
        if not ids:
            return
        stamp = now_iso()
        self.conn.executemany(
            "UPDATE usb_devices SET last_seen_at = ? WHERE id = ?",
            [(stamp, rid) for rid in ids],
        )
        self.conn.commit()

    # ------------------------------------------------------------------ 내부
    @staticmethod
    def _to_record(row: sqlite3.Row) -> UsbRecord:
        # 예전 DB에 남아 있는 모르는 컬럼은 무시한다.
        return UsbRecord(**{k: row[k] for k in row.keys() if k in _FIELD_NAMES})
