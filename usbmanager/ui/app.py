"""메인 창."""

import csv
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .. import APP_NAME, __version__
from ..db import Database, DuplicateAssetNo
from ..models import DEVICE_TYPE_CHOICES, STATUS_CHOICES, ScannedDevice, UsbRecord
from ..scanner import ScanError, scan_devices
from ..util import format_bytes, now_iso, short_date
from .dialogs import DevicePickerDialog, RecordDialog, apply_device

PAD = 8
AUTO_SCAN_INTERVAL_MS = 8000
QUEUE_POLL_MS = 200

COLUMNS = (
    ("conn", "연결", 44, "center"),
    ("asset_no", "관리번호", 90, "w"),
    ("device_type", "종류", 90, "center"),
    ("label", "라벨/별칭", 130, "w"),
    ("owner", "담당자", 80, "w"),
    ("department", "부서", 90, "w"),
    ("purpose", "용도", 90, "w"),
    ("status", "상태", 70, "center"),
    ("model", "모델", 180, "w"),
    ("capacity", "용량", 80, "e"),
    ("file_system", "파일시스템", 90, "center"),
    ("serial_number", "시리얼번호", 150, "w"),
    ("registered_at", "등록일", 90, "center"),
    ("last_seen_at", "최종연결일", 90, "center"),
)


class UsbManagerApp(tk.Tk):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.title(f"{APP_NAME} v{__version__}")
        self.geometry("1240x640")
        self.minsize(900, 480)

        self.records: list[UsbRecord] = []
        self.connected: list[ScannedDevice] = []
        self.connected_ids: set[int] = set()
        self.last_scan_at: str = ""

        self._scan_queue: queue.Queue = queue.Queue()
        self._poll_job: Optional[str] = None
        self._auto_scan_job: Optional[str] = None
        self._scanning = False
        self._modal_open = False
        self._sort_column = "asset_no"
        self._sort_reverse = False

        self._search_var = tk.StringVar()
        self._status_var = tk.StringVar(value="전체")
        self._type_var = tk.StringVar(value="전체")
        self._auto_scan_var = tk.BooleanVar(value=True)
        self._statusbar_var = tk.StringVar(value="준비 중...")

        self._build()
        self.refresh_list()

        # 창을 닫을 때 취소해야 하는 예약 작업들
        self._poll_job = self.after(QUEUE_POLL_MS, self._poll_scan_queue)
        self.start_scan()
        self._auto_scan_job = self.after(AUTO_SCAN_INTERVAL_MS, self._auto_scan_tick)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="연결 장치 스캔", command=self.open_scan_dialog).pack(side="left")
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=PAD)
        ttk.Button(toolbar, text="등록", command=self.add_record).pack(side="left")
        ttk.Button(toolbar, text="수정", command=self.edit_record).pack(side="left", padx=4)
        ttk.Button(toolbar, text="삭제", command=self.delete_records).pack(side="left")
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=PAD)
        ttk.Button(toolbar, text="장치 재연결", command=self.relink_device).pack(side="left")
        ttk.Button(toolbar, text="CSV 내보내기", command=self.export_csv).pack(side="left", padx=4)

        ttk.Checkbutton(
            toolbar, text="자동 감지", variable=self._auto_scan_var
        ).pack(side="right")
        ttk.Button(toolbar, text="새로고침 (F5)", command=self.manual_refresh).pack(
            side="right", padx=PAD
        )

        # --- 검색 / 필터 ------------------------------------------------
        filters = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        filters.pack(fill="x")
        ttk.Label(filters, text="검색").pack(side="left")
        search = ttk.Entry(filters, textvariable=self._search_var, width=32)
        search.pack(side="left", padx=(6, PAD))
        search.bind("<KeyRelease>", lambda _e: self.refresh_list())
        ttk.Label(filters, text="종류").pack(side="left")
        type_combo = ttk.Combobox(
            filters,
            textvariable=self._type_var,
            values=["전체", *DEVICE_TYPE_CHOICES],
            state="readonly",
            width=12,
        )
        type_combo.pack(side="left", padx=(6, PAD))
        type_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_list())
        ttk.Label(filters, text="상태").pack(side="left")
        status_combo = ttk.Combobox(
            filters,
            textvariable=self._status_var,
            values=["전체", *STATUS_CHOICES],
            state="readonly",
            width=10,
        )
        status_combo.pack(side="left", padx=(6, PAD))
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_list())
        ttk.Button(filters, text="초기화", command=self._reset_filters).pack(side="left")

        # --- 목록 -------------------------------------------------------
        table = ttk.Frame(self, padding=PAD)
        table.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            table, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended"
        )
        for key, caption, width, anchor in COLUMNS:
            self.tree.heading(key, text=caption, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key in ("label", "model")))

        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)

        self.tree.tag_configure("connected", background="#e6f7e6")
        self.tree.tag_configure("disposed", foreground="#999999")

        self.tree.bind("<Double-1>", lambda _e: self.edit_record())
        self.tree.bind("<Return>", lambda _e: self.edit_record())
        self.tree.bind("<Delete>", lambda _e: self.delete_records())

        # --- 상태 표시줄 -------------------------------------------------
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        ttk.Label(self, textvariable=self._statusbar_var, anchor="w", padding=(PAD, 4)).pack(
            fill="x"
        )

        self.bind("<F5>", lambda _e: self.manual_refresh())
        self.bind("<Control-n>", lambda _e: self.add_record())

    # ------------------------------------------------------------------ 목록
    def refresh_list(self) -> None:
        status = self._status_var.get()
        device_type = self._type_var.get()
        self.records = self.db.list_records(
            keyword=self._search_var.get(),
            status="" if status == "전체" else status,
            device_type="" if device_type == "전체" else device_type,
        )
        self._apply_sort()
        self._render()

    def _render(self) -> None:
        selected = {int(iid) for iid in self.tree.selection()}
        self.tree.delete(*self.tree.get_children())

        for record in self.records:
            connected = record.id in self.connected_ids
            tags: list[str] = []
            if connected:
                tags.append("connected")
            if record.status == "폐기":
                tags.append("disposed")

            self.tree.insert(
                "",
                "end",
                iid=str(record.id),
                values=(
                    "●" if connected else "",
                    record.asset_no,
                    record.device_type,
                    record.label,
                    record.owner,
                    record.department,
                    record.purpose,
                    record.status,
                    record.model,
                    format_bytes(record.capacity_bytes),
                    record.file_system,
                    record.serial_number,
                    short_date(record.registered_at),
                    short_date(record.last_seen_at),
                ),
                tags=tuple(tags),
            )

        restore = [str(rid) for rid in selected if self.tree.exists(str(rid))]
        if restore:
            self.tree.selection_set(restore)
        self._update_statusbar()

    def _update_statusbar(self) -> None:
        total = self.db.count()
        shown = len(self.records)
        parts = [f"전체 {total}건"]
        if shown != total:
            parts.append(f"표시 {shown}건")
        parts.append(f"연결됨 {len(self.connected_ids)}건")
        unregistered = sum(1 for d in self.connected if self.db.find_matching(d) is None)
        if unregistered:
            parts.append(f"미등록 장치 {unregistered}개")
        if self._scanning:
            parts.append("장치 검색 중...")
        elif self.last_scan_at:
            parts.append(f"마지막 스캔 {self.last_scan_at}")
        self._statusbar_var.set("  ·  ".join(parts))

    def _sort_by(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self._apply_sort()
        self._render()

    def _apply_sort(self) -> None:
        column = self._sort_column

        def key(record: UsbRecord):
            if column == "conn":
                return (0 if record.id in self.connected_ids else 1,)
            if column == "capacity":
                return (record.capacity_bytes or 0,)
            value = getattr(record, column, "") or ""
            return (str(value).lower(),)

        self.records.sort(key=key, reverse=self._sort_reverse)

    def _reset_filters(self) -> None:
        self._search_var.set("")
        self._status_var.set("전체")
        self._type_var.set("전체")
        self.refresh_list()

    def _selected_records(self) -> list[UsbRecord]:
        ids = {int(iid) for iid in self.tree.selection()}
        return [r for r in self.records if r.id in ids]

    # ------------------------------------------------------------------ 스캔
    def start_scan(self) -> None:
        """백그라운드에서 연결 장치를 조회한다."""
        if self._scanning:
            return
        self._scanning = True
        self._update_statusbar()

        def work() -> None:
            try:
                self._scan_queue.put(scan_devices())
            except ScanError as exc:
                self._scan_queue.put(exc)
            except Exception as exc:  # 예기치 못한 오류로 스레드가 조용히 죽지 않도록
                self._scan_queue.put(ScanError(str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _poll_scan_queue(self) -> None:
        try:
            while True:
                result = self._scan_queue.get_nowait()
                self._scanning = False
                if isinstance(result, ScanError):
                    self._statusbar_var.set(f"장치 검색 실패: {result}")
                else:
                    self._handle_scan_result(result)
        except queue.Empty:
            pass
        self._poll_job = self.after(QUEUE_POLL_MS, self._poll_scan_queue)

    def _handle_scan_result(self, devices: list[ScannedDevice], force_render: bool = False) -> None:
        self.connected = devices
        self.last_scan_at = now_iso().split(" ")[1]

        matched_ids = set()
        for device in devices:
            match = self.db.find_matching(device)
            if match and match.id is not None:
                matched_ids.add(match.id)

        changed = matched_ids != self.connected_ids
        self.connected_ids = matched_ids
        self.db.mark_seen(matched_ids)

        if changed or force_render:
            self.refresh_list()
        else:
            self._update_statusbar()

    def _auto_scan_tick(self) -> None:
        if self._auto_scan_var.get() and not self._modal_open:
            self.start_scan()
        self._auto_scan_job = self.after(AUTO_SCAN_INTERVAL_MS, self._auto_scan_tick)

    def _scan_now_blocking(self) -> list[ScannedDevice]:
        """대화창에서 '다시 스캔'을 눌렀을 때 쓰는 동기 스캔."""
        try:
            devices = scan_devices()
        except ScanError as exc:
            messagebox.showerror("장치 검색 실패", str(exc), parent=self)
            return self.connected
        self._handle_scan_result(devices, force_render=True)
        return devices

    # ------------------------------------------------------------------ 동작
    def manual_refresh(self) -> None:
        self.refresh_list()
        self.start_scan()

    def open_scan_dialog(self) -> None:
        """연결된 장치를 보여주고 선택한 장치를 등록한다."""
        self._modal_open = True
        try:
            devices = self._scan_now_blocking()
            picker = DevicePickerDialog(
                self,
                devices=devices,
                matcher=self.db.find_matching,
                rescan=self._scan_now_blocking,
                title="연결된 USB / SD 카드",
                action_text="선택 장치 등록",
            )
            device = picker.selected
        finally:
            self._modal_open = False

        if device is not None:
            self._register_device(device)

    def _register_device(self, device: ScannedDevice) -> None:
        record = UsbRecord(
            asset_no=self.db.next_asset_no(),
            label=device.volume_label or device.model,
        )
        apply_device(record, device)
        self._open_record_dialog("장치 등록", record, device=device, is_new=True)

    def add_record(self) -> None:
        """장치 없이 직접 등록한다."""
        record = UsbRecord(asset_no=self.db.next_asset_no(), device_type="USB")
        self._open_record_dialog("등록 (직접 입력)", record, device=None, is_new=True)

    def edit_record(self) -> None:
        selected = self._selected_records()
        if not selected:
            messagebox.showinfo("선택 필요", "수정할 항목을 선택하세요.", parent=self)
            return
        record = self.db.get(selected[0].id)  # 최신 상태로 다시 읽는다
        if record is None:
            messagebox.showwarning("항목 없음", "이미 삭제된 항목입니다.", parent=self)
            self.refresh_list()
            return
        self._open_record_dialog(f"USB 수정 - {record.asset_no}", record, device=None, is_new=False)

    def _open_record_dialog(
        self,
        title: str,
        record: UsbRecord,
        device: Optional[ScannedDevice],
        is_new: bool,
    ) -> None:
        self._modal_open = True
        try:
            dialog = RecordDialog(
                self,
                title,
                record,
                device=device,
                # 수정 중인 자기 자신은 중복으로 보지 않는다.
                is_asset_no_taken=lambda no: self.db.is_asset_no_taken(no, exclude_id=record.id),
            )
            result = dialog.result
        finally:
            self._modal_open = False

        if result is None:
            return
        try:
            if is_new:
                self.db.insert(result)
            else:
                self.db.update(result)
        except DuplicateAssetNo as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self)
            return

        self.refresh_list()
        self.start_scan()

    def delete_records(self) -> None:
        selected = self._selected_records()
        if not selected:
            messagebox.showinfo("선택 필요", "삭제할 항목을 선택하세요.", parent=self)
            return

        if len(selected) == 1:
            message = f"'{selected[0].asset_no} / {selected[0].label}' 항목을 삭제할까요?"
        else:
            message = f"선택한 {len(selected)}건을 삭제할까요?"
        if not messagebox.askyesno("삭제 확인", message + "\n삭제하면 되돌릴 수 없습니다.", parent=self):
            return

        self.db.delete([r.id for r in selected if r.id is not None])
        self.refresh_list()

    def relink_device(self) -> None:
        """포맷 등으로 정보가 바뀐 기록에 현재 연결된 장치를 다시 연결한다."""
        selected = self._selected_records()
        if not selected:
            messagebox.showinfo(
                "선택 필요",
                "장치 정보를 다시 연결할 항목을 선택하세요.\n"
                "포맷하거나 다른 PC에서 인식이 달라진 경우에 사용합니다.",
                parent=self,
            )
            return
        record = self.db.get(selected[0].id)
        if record is None:
            self.refresh_list()
            return

        self._modal_open = True
        try:
            devices = self._scan_now_blocking()
            picker = DevicePickerDialog(
                self,
                devices=devices,
                matcher=self.db.find_matching,
                rescan=self._scan_now_blocking,
                title=f"장치 재연결 - {record.asset_no} / {record.label}",
                action_text="이 장치로 연결",
                allow_registered=True,
            )
            device = picker.selected
        finally:
            self._modal_open = False

        if device is None:
            return

        other = self.db.find_matching(device)
        if other and other.id != record.id:
            if not messagebox.askyesno(
                "장치 중복",
                f"이 장치는 '{other.asset_no} / {other.label}' 로도 등록되어 있습니다.\n"
                "그래도 연결할까요?",
                parent=self,
            ):
                return

        apply_device(record, device)
        self.db.update(record)
        self.refresh_list()
        self.start_scan()

    def export_csv(self) -> None:
        if not self.records:
            messagebox.showinfo("내보낼 항목 없음", "목록이 비어 있습니다.", parent=self)
            return

        path = filedialog.asksaveasfilename(
            parent=self,
            title="CSV로 내보내기",
            defaultextension=".csv",
            initialfile="usb_list.csv",
            filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")],
        )
        if not path:
            return

        headers = [
            "관리번호", "종류", "라벨/별칭", "담당자", "부서", "용도", "상태",
            "모델", "용량", "파일시스템", "시리얼번호", "볼륨 일련번호",
            "등록일", "최종수정일", "최종연결일", "비고",
        ]
        try:
            # Excel에서 한글이 깨지지 않도록 BOM 포함 UTF-8로 저장한다.
            with open(path, "w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(headers)
                for r in self.records:
                    writer.writerow([
                        r.asset_no, r.device_type, r.label, r.owner, r.department,
                        r.purpose, r.status, r.model,
                        format_bytes(r.capacity_bytes), r.file_system,
                        r.serial_number, r.volume_serial,
                        r.registered_at, r.updated_at, r.last_seen_at, r.note,
                    ])
        except OSError as exc:
            messagebox.showerror("내보내기 실패", str(exc), parent=self)
            return

        messagebox.showinfo("내보내기 완료", f"{len(self.records)}건을 저장했습니다.\n{path}", parent=self)

    def _on_close(self) -> None:
        # 예약된 콜백을 먼저 취소해야 창이 사라진 뒤 실행되며 오류를 내지 않는다.
        for job in (self._poll_job, self._auto_scan_job):
            if job:
                self.after_cancel(job)
        self._poll_job = self._auto_scan_job = None
        self.db.close()
        self.destroy()
