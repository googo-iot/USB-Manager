"""등록/수정 폼과 연결 장치 선택 창."""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from ..models import (
    ASSET_NO_HINT,
    DEVICE_TYPE_CHOICES,
    PURPOSE_SUGGESTIONS,
    STATUS_CHOICES,
    ScannedDevice,
    UsbRecord,
    is_partial_asset_no,
    is_valid_asset_no,
)
from ..util import format_bytes

PAD = 8

# 관리번호 입력 상태 표시 색
OK_COLOR = "#1e7a34"
WARN_COLOR = "#c0392b"
HINT_COLOR = "#666666"


class RecordDialog(tk.Toplevel):
    """USB 한 건을 등록하거나 수정하는 창.

    result 에 저장된 UsbRecord 를 담고, 취소하면 None 으로 남는다.
    """

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        record: UsbRecord,
        device: Optional[ScannedDevice] = None,
        is_asset_no_taken: Optional[Callable[[str], bool]] = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.record = record
        self.device = device
        # 자기 자신은 제외하고 중복 여부를 알려주는 콜백
        self._is_taken = is_asset_no_taken or (lambda _no: False)
        self.result: Optional[UsbRecord] = None

        self.transient(parent)
        self.resizable(False, False)

        self._vars: dict[str, tk.StringVar] = {
            name: tk.StringVar(value=getattr(record, name))
            for name in (
                "asset_no", "label", "device_type", "owner", "department",
                "purpose", "status",
            )
        }

        self._build()
        self._populate_device_info()
        self._vars["asset_no"].trace_add("write", lambda *_: self._refresh_asset_status())
        self._refresh_asset_status()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.grab_set()
        self._first_entry.focus_set()
        self.wait_window(self)

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        outer = ttk.Frame(self, padding=PAD)
        outer.pack(fill="both", expand=True)

        # --- 관리 정보 -------------------------------------------------
        form = ttk.LabelFrame(outer, text="관리 정보", padding=PAD)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        self._add_asset_no_field(form, 0, 0)
        self._add_entry(form, 0, 2, "라벨/별칭 *", "label")
        # 종류는 연결된 장치에서 자동 판별하지만, 틀리면 직접 고칠 수 있게 둔다.
        self._add_combo(form, 1, 0, "종류", "device_type", DEVICE_TYPE_CHOICES)
        self._add_entry(form, 1, 2, "담당자", "owner")
        self._add_entry(form, 2, 0, "부서", "department")
        self._add_combo(form, 2, 2, "용도", "purpose", PURPOSE_SUGGESTIONS, readonly=False)
        self._add_combo(form, 3, 0, "상태", "status", STATUS_CHOICES)

        # --- 장치 정보 (자동 수집) --------------------------------------
        info = ttk.LabelFrame(outer, text="장치 정보 (자동 수집)", padding=PAD)
        info.pack(fill="x", pady=(PAD, 0))
        info.columnconfigure(1, weight=1)
        info.columnconfigure(3, weight=1)

        self._info_labels: dict[str, ttk.Label] = {}
        for idx, (key, caption) in enumerate(
            [
                ("model", "모델"),
                ("capacity", "용량"),
                ("serial_number", "시리얼번호"),
                ("volume_serial", "볼륨 일련번호"),
                ("file_system", "파일시스템"),
                ("device_key", "장치 식별자"),
            ]
        ):
            row, col = divmod(idx, 2)
            ttk.Label(info, text=caption).grid(
                row=row, column=col * 2, sticky="w", padx=(0, 6), pady=3
            )
            label = ttk.Label(info, text="-", foreground="#333")
            label.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, PAD), pady=3)
            self._info_labels[key] = label

        # --- 날짜 이력 --------------------------------------------------
        history = ttk.LabelFrame(outer, text="날짜 이력", padding=PAD)
        history.pack(fill="x", pady=(PAD, 0))
        for idx, (key, caption) in enumerate(
            [("registered_at", "등록일"), ("updated_at", "최종수정일"), ("last_seen_at", "최종연결일")]
        ):
            ttk.Label(history, text=caption).grid(row=0, column=idx * 2, sticky="w", padx=(0, 6))
            ttk.Label(history, text=getattr(self.record, key) or "-").grid(
                row=0, column=idx * 2 + 1, sticky="w", padx=(0, PAD)
            )

        # --- 비고 -------------------------------------------------------
        note_frame = ttk.LabelFrame(outer, text="비고", padding=PAD)
        note_frame.pack(fill="both", expand=True, pady=(PAD, 0))
        self._note = tk.Text(note_frame, height=4, width=70, wrap="word")
        self._note.insert("1.0", self.record.note)
        self._note.pack(fill="both", expand=True)

        # --- 버튼 -------------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text="취소", command=self._on_cancel).pack(side="right")
        ttk.Button(buttons, text="저장", command=self._on_save).pack(side="right", padx=(0, 6))

    def _add_asset_no_field(self, parent: tk.Misc, row: int, col: int) -> None:
        """관리번호 칸. 규칙에 맞지 않는 글자는 아예 입력되지 않는다."""
        ttk.Label(parent, text="관리번호 *").grid(
            row=row, column=col, sticky="w", padx=(0, 6), pady=4
        )
        box = ttk.Frame(parent)
        box.grid(row=row, column=col + 1, sticky="ew", padx=(0, PAD), pady=4)

        guard = (self.register(self._guard_asset_no), "%P")
        self._first_entry = ttk.Entry(
            box,
            textvariable=self._vars["asset_no"],
            width=9,
            validate="key",
            validatecommand=guard,
        )
        self._first_entry.pack(side="left")
        self._asset_status = ttk.Label(box, text="", foreground=HINT_COLOR)
        self._asset_status.pack(side="left", padx=(8, 0))

    @staticmethod
    def _guard_asset_no(proposed: str) -> bool:
        """MS+숫자로 커나가는 문자열만 통과시킨다 (소문자는 이후 대문자로 바뀐다)."""
        return is_partial_asset_no(proposed.upper())

    def _refresh_asset_status(self) -> None:
        """입력 중인 관리번호가 쓸 수 있는 번호인지 옆에 바로 알려준다."""
        value = self._vars["asset_no"].get()
        upper = value.upper()
        if upper != value:  # 소문자로 쳐도 대문자로 맞춰준다
            self._vars["asset_no"].set(upper)
            return

        if not upper:
            text, color = "필수 입력", HINT_COLOR
        elif not is_valid_asset_no(upper):
            text, color = ASSET_NO_HINT, HINT_COLOR
        elif self._is_taken(upper):
            text, color = "이미 사용 중인 번호", WARN_COLOR
        else:
            text, color = "사용 가능", OK_COLOR
        self._asset_status.config(text=text, foreground=color)

    def _add_entry(self, parent: tk.Misc, row: int, col: int, caption: str, key: str) -> ttk.Entry:
        ttk.Label(parent, text=caption).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=4)
        entry = ttk.Entry(parent, textvariable=self._vars[key], width=24)
        entry.grid(row=row, column=col + 1, sticky="ew", padx=(0, PAD), pady=4)
        return entry

    def _add_combo(
        self,
        parent: tk.Misc,
        row: int,
        col: int,
        caption: str,
        key: str,
        values: tuple[str, ...],
        readonly: bool = True,
    ) -> ttk.Combobox:
        ttk.Label(parent, text=caption).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=4)
        combo = ttk.Combobox(
            parent,
            textvariable=self._vars[key],
            values=list(values),
            width=22,
            state="readonly" if readonly else "normal",
        )
        combo.grid(row=row, column=col + 1, sticky="ew", padx=(0, PAD), pady=4)
        return combo

    def _populate_device_info(self) -> None:
        """스캔한 장치가 있으면 그 값을, 없으면 기존 기록의 값을 보여준다."""
        if self.device is not None:
            values = {
                "model": self.device.model,
                "capacity": format_bytes(self.device.capacity_bytes),
                "serial_number": self.device.serial_number,
                "volume_serial": self.device.volume_serial,
                "file_system": self.device.file_system,
                "device_key": self.device.device_key,
            }
        else:
            values = {
                "model": self.record.model,
                "capacity": format_bytes(self.record.capacity_bytes),
                "serial_number": self.record.serial_number,
                "volume_serial": self.record.volume_serial,
                "file_system": self.record.file_system,
                "device_key": self.record.device_key,
            }
        for key, label in self._info_labels.items():
            text = values.get(key) or "-"
            if key == "device_key" and len(text) > 60:
                text = text[:57] + "..."
            label.config(text=text)

    # ------------------------------------------------------------------ 동작
    def _on_save(self) -> None:
        asset_no = self._vars["asset_no"].get().strip().upper()
        label = self._vars["label"].get().strip()

        if not is_valid_asset_no(asset_no):
            messagebox.showwarning(
                "관리번호 확인",
                f"관리번호는 {ASSET_NO_HINT}로 입력하세요.\n예: MS001",
                parent=self,
            )
            self._first_entry.focus_set()
            return
        if self._is_taken(asset_no):
            messagebox.showwarning(
                "중복 관리번호",
                f"'{asset_no}'는 이미 등록된 관리번호입니다.\n다른 번호를 입력하세요.",
                parent=self,
            )
            self._first_entry.focus_set()
            self._first_entry.selection_range(0, "end")
            return
        if not label:
            messagebox.showwarning("입력 확인", "라벨/별칭을 입력하세요.", parent=self)
            return

        record = self.record
        record.asset_no = asset_no
        record.label = label
        record.owner = self._vars["owner"].get().strip()
        record.department = self._vars["department"].get().strip()
        record.purpose = self._vars["purpose"].get().strip()
        record.status = self._vars["status"].get() or "사용중"
        record.note = self._note.get("1.0", "end").strip()

        if self.device is not None:
            apply_device(record, self.device, update_type=False)
        # 자동 판별이 틀렸을 수 있으므로 사용자가 고른 종류를 우선한다.
        record.device_type = self._vars["device_type"].get().strip()

        self.result = record
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


def apply_device(record: UsbRecord, device: ScannedDevice, update_type: bool = True) -> None:
    """스캔한 장치 정보를 기록에 반영한다.

    update_type=False 면 종류는 건드리지 않는다. 등록/수정 폼에서 사용자가
    직접 고른 종류를 자동 판별값이 덮어쓰지 않도록 하기 위한 것이다.
    """
    from ..util import now_iso

    if update_type:
        record.device_type = device.device_type
    record.device_key = device.device_key
    record.serial_number = device.serial_number
    record.volume_serial = device.volume_serial
    record.model = device.model
    record.capacity_bytes = device.capacity_bytes
    record.file_system = device.file_system
    record.last_seen_at = now_iso()


class DevicePickerDialog(tk.Toplevel):
    """현재 연결된 장치를 보여주고 하나를 고르게 하는 창.

    selected 에 고른 ScannedDevice 가 담긴다.
    """

    COLUMNS = (
        ("state", "등록상태", 140),
        ("type", "종류", 90),
        ("drive", "드라이브", 70),
        ("volume", "볼륨명", 110),
        ("model", "모델", 200),
        ("capacity", "용량", 80),
        ("fs", "파일시스템", 90),
        ("serial", "시리얼번호", 170),
    )

    def __init__(
        self,
        parent: tk.Misc,
        devices: list[ScannedDevice],
        matcher: Callable[[ScannedDevice], Optional[UsbRecord]],
        rescan: Callable[[], list[ScannedDevice]],
        title: str = "연결된 장치",
        action_text: str = "선택 장치 등록",
        allow_registered: bool = False,
    ):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.geometry("900x340")

        self._matcher = matcher
        self._rescan = rescan
        self._action_text = action_text
        self._allow_registered = allow_registered
        self._devices: list[ScannedDevice] = devices
        self._matches: dict[str, Optional[UsbRecord]] = {}
        self.selected: Optional[ScannedDevice] = None

        self._build()
        self._fill()

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.wait_window(self)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=PAD)
        outer.pack(fill="both", expand=True)

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="다시 스캔", command=self._on_rescan).pack(side="left")
        self._hint = ttk.Label(bar, text="", foreground="#555")
        self._hint.pack(side="left", padx=PAD)

        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table, columns=[c[0] for c in self.COLUMNS], show="headings", selectmode="browse"
        )
        for key, caption, width in self.COLUMNS:
            self.tree.heading(key, text=caption)
            self.tree.column(key, width=width, anchor="w", stretch=(key == "model"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.tag_configure("registered", foreground="#888")
        self.tree.bind("<Double-1>", lambda _e: self._on_action())

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(PAD, 0))
        ttk.Button(buttons, text="닫기", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text=self._action_text, command=self._on_action).pack(
            side="right", padx=(0, 6)
        )

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._matches.clear()

        for idx, device in enumerate(self._devices):
            match = self._matcher(device)
            iid = str(idx)
            self._matches[iid] = match
            state = f"등록됨 ({match.asset_no})" if match else "미등록"
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    state,
                    device.device_type,
                    device.drive_letters or "-",
                    device.volume_label or "-",
                    device.model or "-",
                    format_bytes(device.capacity_bytes),
                    device.file_system or "-",
                    device.serial_number or "-",
                ),
                tags=("registered",) if match and not self._allow_registered else (),
            )

        if not self._devices:
            self._hint.config(text="연결된 USB / SD 카드가 없습니다.")
        else:
            new_count = sum(1 for m in self._matches.values() if m is None)
            self._hint.config(
                text=f"장치 {len(self._devices)}개 · 미등록 {new_count}개"
            )
        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])

    def _on_rescan(self) -> None:
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self._devices = self._rescan()
        finally:
            self.config(cursor="")
        self._fill()

    def _on_action(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("선택 필요", "장치를 선택하세요.", parent=self)
            return
        iid = selection[0]
        match = self._matches.get(iid)
        if match and not self._allow_registered:
            messagebox.showinfo(
                "이미 등록된 장치",
                f"이 장치는 이미 '{match.asset_no} / {match.label}' 로 등록되어 있습니다.\n"
                "내용을 바꾸려면 목록에서 해당 항목을 수정하세요.",
                parent=self,
            )
            return
        self.selected = self._devices[int(iid)]
        self.destroy()
