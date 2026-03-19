from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .timeline import DATE_NAMED_RE, VIDEO_EXTENSIONS


TIMELINE_CSV = "media_timeline.csv"
MATCHES_CSV = "timestamp_candidate_matches.csv"
RENAME_PLAN_CSV = "timestamp_rename_plan.csv"


@dataclass
class RenamePlanRow:
    old_name: str
    new_name: str
    timestamp_used: datetime
    timestamp_origin: str
    naming_status: str
    original_confidence: str
    match_score: str
    reference_filename: str


def load_top_matches(base_dir: Path, matches_name: str = MATCHES_CSV) -> dict[str, dict[str, str]]:
    path = base_dir / matches_name
    if not path.exists():
        return {}

    top_matches: dict[str, dict[str, str]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["rank"] == "1":
                top_matches[row["target_filename"]] = row
    return top_matches


def next_anchored_name(
    reference_filename: str,
    extension: str,
    occupied_names: set[str],
) -> str:
    reference_path = Path(reference_filename)
    base_stem = reference_path.stem
    index = 2
    while True:
        candidate = f"{base_stem}_{index:02d}{extension}"
        if candidate not in occupied_names:
            return candidate
        index += 1


def next_timestamp_name(
    timestamp_used: datetime,
    extension: str,
    occupied_names: set[str],
) -> str:
    base_stem = timestamp_used.strftime("%Y-%m-%d %H.%M.%S")
    candidate = f"{base_stem}{extension}"
    if candidate not in occupied_names:
        return candidate

    index = 2
    while True:
        candidate = f"{base_stem}_{index:02d}{extension}"
        if candidate not in occupied_names:
            return candidate
        index += 1


def build_rename_plan(
    base_dir: Path,
    timeline_name: str = TIMELINE_CSV,
    matches_name: str = MATCHES_CSV,
    output_name: str = RENAME_PLAN_CSV,
) -> tuple[Path, list[RenamePlanRow]]:
    top_matches = load_top_matches(base_dir, matches_name)
    rows: list[dict[str, object]] = []

    with (base_dir / timeline_name).open(newline="") as handle:
        for row in csv.DictReader(handle):
            filename = row["filename"]
            timestamp_used = datetime.fromisoformat(row["best_timestamp"])
            timestamp_origin = row["best_source"]
            naming_status = "kept_original"
            match_score = ""
            reference_filename = ""

            top_match = top_matches.get(filename)
            if row["timestamp_bucket"] == "target" and top_match:
                timestamp_used = datetime.fromisoformat(top_match["reference_timestamp"])
                timestamp_origin = "similarity_match"
                naming_status = "anchored_inferred"
                match_score = top_match["score"]
                reference_filename = top_match["reference_filename"]
            elif (
                Path(filename).suffix.lower() in VIDEO_EXTENSIONS
                and row["timestamp_bucket"] == "reference"
                and not DATE_NAMED_RE.match(Path(filename).stem)
            ):
                naming_status = "normalized_video"
            elif row["timestamp_bucket"] == "target":
                naming_status = "kept_unmatched"

            rows.append(
                {
                    "old_name": filename,
                    "new_name": filename,
                    "extension": Path(filename).suffix,
                    "timestamp_used": timestamp_used,
                    "timestamp_origin": timestamp_origin,
                    "naming_status": naming_status,
                    "original_confidence": row["confidence"],
                    "match_score": match_score,
                    "reference_filename": reference_filename,
                }
            )

    occupied_names = {
        row["old_name"]
        for row in rows
        if row["naming_status"] not in {"anchored_inferred", "normalized_video"}
    }

    rows.sort(
        key=lambda row: (
            row["timestamp_used"],
            row["reference_filename"],
            row["old_name"],
        )
    )

    for row in rows:
        if row["naming_status"] == "anchored_inferred":
            new_name = next_anchored_name(
                row["reference_filename"],
                row["extension"],
                occupied_names,
            )
            row["new_name"] = new_name
            occupied_names.add(new_name)
        elif row["naming_status"] == "normalized_video":
            new_name = next_timestamp_name(
                row["timestamp_used"],
                row["extension"],
                occupied_names,
            )
            row["new_name"] = new_name
            occupied_names.add(new_name)

    planned_rows = [
        RenamePlanRow(
            old_name=row["old_name"],
            new_name=row["new_name"],
            timestamp_used=row["timestamp_used"],
            timestamp_origin=row["timestamp_origin"],
            naming_status=row["naming_status"],
            original_confidence=row["original_confidence"],
            match_score=row["match_score"],
            reference_filename=row["reference_filename"],
        )
        for row in rows
    ]
    planned_rows.sort(key=lambda row: (row.timestamp_used, row.new_name, row.old_name))

    output_path = base_dir / output_name
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "old_name",
                "new_name",
                "would_rename",
                "timestamp_used",
                "timestamp_origin",
                "naming_status",
                "original_confidence",
                "match_score",
                "reference_filename",
            ]
        )
        for row in planned_rows:
            writer.writerow(
                [
                    row.old_name,
                    row.new_name,
                    "yes" if row.old_name != row.new_name else "no",
                    row.timestamp_used.isoformat(timespec="seconds"),
                    row.timestamp_origin,
                    row.naming_status,
                    row.original_confidence,
                    row.match_score,
                    row.reference_filename,
                ]
            )

    return output_path, planned_rows


def apply_rename_plan(base_dir: Path, planned_rows: list[RenamePlanRow]) -> int:
    rows_to_rename = [row for row in planned_rows if row.old_name != row.new_name]
    temp_paths: list[tuple[Path, Path, Path]] = []

    for index, row in enumerate(rows_to_rename, start=1):
        old_path = base_dir / row.old_name
        new_path = base_dir / row.new_name
        temp_path = base_dir / f".photo_timestamp_guess_tmp_{index:04d}{old_path.suffix}"
        if temp_path.exists():
            raise RuntimeError(f"Temporary rename path already exists: {temp_path.name}")
        temp_paths.append((old_path, temp_path, new_path))

    for old_path, temp_path, _ in temp_paths:
        old_path.rename(temp_path)
    for _, temp_path, new_path in temp_paths:
        temp_path.rename(new_path)

    return len(rows_to_rename)
