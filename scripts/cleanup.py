"""Retention: posts/ 아래 7일보다 오래된 날짜 폴더 삭제. 워크플로우 0단계에서 실행.

사용법: python scripts/cleanup.py [--days 7] [--dry-run]
폴더명 맨 앞 10자(YYYY-MM-DD)를 기준일로 보고, 오늘 기준 그만큼 지난 폴더를 삭제한다.
(예: 2026-08-25, 2026-08-25-2 처럼 뒤에 접미사가 붙어도 앞 10자로 날짜를 판단한다.)
"""

import argparse
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

POSTS_DIR = Path(__file__).resolve().parent.parent / "posts"


def parse_folder_date(name: str) -> date | None:
    try:
        return datetime.strptime(name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="보관 기간(일). 기본 7일")
    parser.add_argument("--dry-run", action="store_true", help="삭제하지 않고 대상만 출력")
    args = parser.parse_args()

    if not POSTS_DIR.is_dir():
        print(f"posts 폴더를 찾을 수 없습니다: {POSTS_DIR}", file=sys.stderr)
        sys.exit(1)

    today = date.today()
    removed = 0

    for entry in sorted(POSTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        folder_date = parse_folder_date(entry.name)
        if folder_date is None:
            continue
        age_days = (today - folder_date).days
        if age_days > args.days:
            if args.dry_run:
                print(f"[dry-run] 삭제 대상: {entry.name} ({age_days}일 경과)")
            else:
                shutil.rmtree(entry)
                print(f"삭제 완료: {entry.name} ({age_days}일 경과)")
            removed += 1

    if removed == 0:
        print(f"삭제 대상 없음 (보관 기간 {args.days}일 이내)")


if __name__ == "__main__":
    main()
