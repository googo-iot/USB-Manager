"""USB 관리 프로그램 실행 진입점.

    python main.py                # 기본 DB(usb_manager.db) 사용
    python main.py --db 경로.db   # 다른 DB 파일 사용
"""

import argparse
import sys
from pathlib import Path

from usbmanager.db import DEFAULT_DB_PATH, Database
from usbmanager.ui.app import UsbManagerApp


def main() -> int:
    parser = argparse.ArgumentParser(description="USB / SD 카드 관리 프로그램")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"데이터베이스 파일 경로 (기본: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    db = Database(Path(args.db))
    app = UsbManagerApp(db)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
