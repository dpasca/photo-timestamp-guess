from __future__ import annotations

import csv
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".png", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
DATE_NAMED_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})(?:-\d+)?$"
)
LOCAL_TZ = datetime.now().astimezone().tzinfo


@dataclass
class MediaRow:
    filename: str
    group: str
    best_timestamp: datetime
    best_source: str
    confidence: str
    timestamp_bucket: str
    embedded_creation: str
    filename_timestamp: str
    filesystem_birthtime: str
    camera_make: str
    camera_model: str


def run_command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def parse_local_timestamp(value: str, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=LOCAL_TZ)
    except ValueError:
        return None


def parse_filename_timestamp(path: Path) -> datetime | None:
    match = DATE_NAMED_RE.match(path.stem)
    if not match:
        return None
    return parse_local_timestamp(match.group("stamp"), "%Y-%m-%d %H.%M.%S")


def classify_group(path: Path) -> str:
    if DATE_NAMED_RE.match(path.stem):
        return "date_named_export"
    if path.name.startswith("S__"):
        return "app_saved_copy"
    if re.fullmatch(r"\d+(?:\.\d+)?", path.stem):
        return "numeric_download_name"
    return "other"


def classify_timestamp_bucket(confidence: str) -> str:
    return "reference" if confidence in {"high", "medium"} else "target"


def derive_timestamp_bucket(row: dict[str, str]) -> str:
    bucket = row.get("timestamp_bucket", "")
    if bucket:
        return bucket
    return classify_timestamp_bucket(row["confidence"])


def extract_image_metadata(path: Path) -> tuple[datetime | None, str, str]:
    output = run_command(["sips", "-g", "all", str(path)])
    creation = None
    make = ""
    model = ""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("creation:"):
            value = stripped.split(":", 1)[1].strip()
            creation = parse_local_timestamp(value, "%Y:%m:%d %H:%M:%S")
        elif stripped.startswith("make:"):
            make = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("model:"):
            model = stripped.split(":", 1)[1].strip()
    return creation, make, model


def extract_video_metadata(path: Path) -> tuple[datetime | None, str, str]:
    output = run_command(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format_tags=creation_time",
            "-of",
            "json",
            str(path),
        ]
    )
    if not output:
        return None, "", ""
    try:
        payload = json.loads(output)
        value = payload["format"]["tags"]["creation_time"]
    except (KeyError, json.JSONDecodeError, TypeError):
        return None, "", ""
    try:
        utc_dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "", ""
    return utc_dt.astimezone(LOCAL_TZ), "", ""


def iso_or_blank(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat(timespec="seconds")


def build_row(path: Path) -> MediaRow:
    stat_result = path.stat()
    birthtime = datetime.fromtimestamp(stat_result.st_birthtime, LOCAL_TZ)
    filename_time = parse_filename_timestamp(path)
    embedded_time = None
    make = ""
    model = ""

    if path.suffix.lower() in IMAGE_EXTENSIONS:
        embedded_time, make, model = extract_image_metadata(path)
    elif path.suffix.lower() in VIDEO_EXTENSIONS:
        embedded_time, make, model = extract_video_metadata(path)

    if embedded_time is not None:
        best_time = embedded_time
        best_source = "embedded_metadata"
        confidence = "high"
    elif filename_time is not None:
        best_time = filename_time
        best_source = "filename_datetime"
        confidence = "medium"
    else:
        best_time = birthtime
        best_source = "filesystem_birthtime"
        confidence = "low"

    return MediaRow(
        filename=path.name,
        group=classify_group(path),
        best_timestamp=best_time,
        best_source=best_source,
        confidence=confidence,
        timestamp_bucket=classify_timestamp_bucket(confidence),
        embedded_creation=iso_or_blank(embedded_time),
        filename_timestamp=iso_or_blank(filename_time),
        filesystem_birthtime=iso_or_blank(birthtime),
        camera_make=make,
        camera_model=model,
    )


def build_timeline(base_dir: Path, output_name: str = "media_timeline.csv") -> Path:
    rows = [
        build_row(path)
        for path in sorted(base_dir.iterdir())
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    ]
    rows.sort(key=lambda row: (row.best_timestamp, row.filename.lower()))

    output_path = base_dir / output_name
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "best_timestamp",
                "best_source",
                "confidence",
                "timestamp_bucket",
                "group",
                "filename",
                "embedded_creation",
                "filename_timestamp",
                "filesystem_birthtime",
                "camera_make",
                "camera_model",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.best_timestamp.isoformat(timespec="seconds"),
                    row.best_source,
                    row.confidence,
                    row.timestamp_bucket,
                    row.group,
                    row.filename,
                    row.embedded_creation,
                    row.filename_timestamp,
                    row.filesystem_birthtime,
                    row.camera_make,
                    row.camera_model,
                ]
            )
    return output_path
