from __future__ import annotations

import argparse
from pathlib import Path

from .matcher import infer_timestamps
from .renamer import apply_rename_plan, build_rename_plan
from .review_page import build_review_page
from .timeline import build_timeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-timestamp-guess",
        description="Build timeline, inference, review, and rename-plan artifacts for mixed media folders.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ["timeline", "match", "review", "all"]:
        command = subparsers.add_parser(name)
        command.add_argument("directory", nargs="?", default=".")

    rename_command = subparsers.add_parser("rename")
    rename_command.add_argument("directory", nargs="?", default=".")
    rename_command.add_argument(
        "--apply",
        action="store_true",
        help="Apply the rename plan after writing the dry-run CSV.",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()
    base_dir = Path(args.directory).expanduser().resolve()

    if args.command == "timeline":
        output = build_timeline(base_dir)
        print(f"Wrote {output}")
        return

    if args.command == "match":
        outputs = infer_timestamps(base_dir)
        print("Wrote " + ", ".join(str(path) for path in outputs))
        return

    if args.command == "review":
        output = build_review_page(base_dir)
        print(f"Wrote {output}")
        return

    if args.command == "rename":
        plan_path, planned_rows = build_rename_plan(base_dir)
        print(f"Wrote {plan_path}")
        if args.apply:
            renamed = apply_rename_plan(base_dir, planned_rows)
            print(f"Renamed {renamed} files")
        else:
            print("Dry run only. Re-run with --apply to perform the renames.")
        return

    timeline = build_timeline(base_dir)
    match_outputs = infer_timestamps(base_dir)
    review = build_review_page(base_dir)
    plan_path, _ = build_rename_plan(base_dir)
    print(f"Wrote {timeline}")
    print("Wrote " + ", ".join(str(path) for path in match_outputs))
    print(f"Wrote {review}")
    print(f"Wrote {plan_path}")


if __name__ == "__main__":
    main()
