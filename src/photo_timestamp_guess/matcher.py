from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageFilter, ImageOps

from .timeline import IMAGE_EXTENSIONS, derive_timestamp_bucket


TIMELINE_CSV = "media_timeline.csv"
PER_IMAGE_OUTPUT = "timestamp_candidate_matches.csv"
BURST_OUTPUT = "timestamp_batch_burst_summary.csv"
SUMMARY_OUTPUT = "timestamp_batch_summary.txt"
WINDOW_DAYS = 2
REFERENCE_BURST_GAP_MINUTES = 20
TARGET_BATCH_GAP_MINUTES = 30
TOP_MATCHES_PER_IMAGE = 5
TOP_BURSTS_PER_BATCH = 10

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


@dataclass
class MediaItem:
    filename: str
    path: Path
    timestamp: datetime
    group: str
    confidence: str
    bucket: str
    orientation: str = ""
    aspect_ratio: float = 1.0
    cluster_id: str = ""


@dataclass
class PhotoFeatures:
    gray_pad: list[float]
    gray_fit: list[float]
    edge_pad: list[float]
    hsv_hist: list[float]
    brightness: float
    orientation: str
    aspect_ratio: float


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_media_items(
    base_dir: Path, timeline_name: str = TIMELINE_CSV
) -> tuple[list[MediaItem], list[MediaItem]]:
    timeline_path = base_dir / timeline_name
    reference_items: list[MediaItem] = []
    target_items: list[MediaItem] = []

    with timeline_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            path = base_dir / row["filename"]
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            item = MediaItem(
                filename=row["filename"],
                path=path,
                timestamp=parse_timestamp(row["best_timestamp"]),
                group=row["group"],
                confidence=row["confidence"],
                bucket=derive_timestamp_bucket(row),
            )

            if item.bucket == "reference":
                reference_items.append(item)
            else:
                target_items.append(item)

    reference_items.sort(key=lambda item: (item.timestamp, item.filename))
    target_items.sort(key=lambda item: (item.timestamp, item.filename))
    return reference_items, target_items


def assign_time_groups(
    items: list[MediaItem],
    gap_minutes: int,
    prefix: str,
) -> None:
    current_index = 0
    last_timestamp: datetime | None = None
    for item in items:
        if last_timestamp is None or item.timestamp - last_timestamp > timedelta(
            minutes=gap_minutes
        ):
            current_index += 1
        item.cluster_id = f"{prefix}{current_index:02d}"
        last_timestamp = item.timestamp


def normalize_centered(values: Iterable[float]) -> list[float]:
    values = list(values)
    if not values:
        return []
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    magnitude = math.sqrt(sum(value * value for value in centered))
    if magnitude == 0:
        return [0.0 for _ in centered]
    return [value / magnitude for value in centered]


def normalize(values: Iterable[float]) -> list[float]:
    values = list(values)
    total = sum(values)
    if total == 0:
        return [0.0 for _ in values]
    return [value / total for value in values]


def square_variant(image: Image.Image, size: int, mode: str, fit: bool) -> Image.Image:
    converted = image.convert(mode)
    if fit:
        return ImageOps.fit(converted, (size, size), method=RESAMPLE)
    return ImageOps.pad(converted, (size, size), method=RESAMPLE, color=0)


def orientation_label(width: int, height: int) -> str:
    if width > height * 1.05:
        return "landscape"
    if height > width * 1.05:
        return "portrait"
    return "square"


def build_hsv_histogram(image: Image.Image) -> list[float]:
    small = image.convert("HSV").resize((48, 48), RESAMPLE)
    bins = [0] * (12 * 4 * 4)
    for hue, sat, value in small.getdata():
        hue_bin = min(hue * 12 // 256, 11)
        sat_bin = min(sat * 4 // 256, 3)
        value_bin = min(value * 4 // 256, 3)
        index = (hue_bin * 4 + sat_bin) * 4 + value_bin
        bins[index] += 1
    return normalize(bins)


def build_features(path: Path) -> PhotoFeatures:
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        width, height = image.size
        gray_pad = square_variant(image, 24, "L", fit=False)
        gray_fit = square_variant(image, 24, "L", fit=True)
        edge_pad = gray_pad.filter(ImageFilter.FIND_EDGES)
        brightness = sum(gray_pad.getdata()) / (255 * 24 * 24)
        return PhotoFeatures(
            gray_pad=normalize_centered(value / 255 for value in gray_pad.getdata()),
            gray_fit=normalize_centered(value / 255 for value in gray_fit.getdata()),
            edge_pad=normalize_centered(value / 255 for value in edge_pad.getdata()),
            hsv_hist=build_hsv_histogram(image),
            brightness=brightness,
            orientation=orientation_label(width, height),
            aspect_ratio=width / height if height else 1.0,
        )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def histogram_intersection(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(min(a, b) for a, b in zip(left, right))


def aspect_similarity(left: PhotoFeatures, right: PhotoFeatures) -> float:
    ratio_gap = abs(math.log(max(left.aspect_ratio, 1e-6) / max(right.aspect_ratio, 1e-6)))
    scaled = max(0.0, 1.0 - min(ratio_gap / math.log(2), 1.0))
    if left.orientation != right.orientation:
        scaled *= 0.8
    return scaled


def brightness_similarity(left: PhotoFeatures, right: PhotoFeatures) -> float:
    return max(0.0, 1.0 - abs(left.brightness - right.brightness))


def compare_features(left: PhotoFeatures, right: PhotoFeatures) -> float:
    gray_pad = (cosine_similarity(left.gray_pad, right.gray_pad) + 1.0) / 2.0
    gray_fit = (cosine_similarity(left.gray_fit, right.gray_fit) + 1.0) / 2.0
    edge_pad = (cosine_similarity(left.edge_pad, right.edge_pad) + 1.0) / 2.0
    color = histogram_intersection(left.hsv_hist, right.hsv_hist)
    aspect = aspect_similarity(left, right)
    brightness = brightness_similarity(left, right)
    return (
        0.26 * gray_pad
        + 0.18 * gray_fit
        + 0.18 * edge_pad
        + 0.26 * color
        + 0.07 * aspect
        + 0.05 * brightness
    )


def top_matches_for_item(
    target_item: MediaItem,
    target_feature: PhotoFeatures,
    reference_items: list[MediaItem],
    reference_features: dict[str, PhotoFeatures],
    window_days: int = WINDOW_DAYS,
) -> list[tuple[float, MediaItem]]:
    window_start = target_item.timestamp - timedelta(days=window_days)
    candidates: list[tuple[float, MediaItem]] = []
    for reference_item in reference_items:
        if reference_item.timestamp > target_item.timestamp:
            break
        if reference_item.timestamp < window_start:
            continue
        score = compare_features(target_feature, reference_features[reference_item.filename])
        candidates.append((score, reference_item))
    candidates.sort(key=lambda item: (-item[0], item[1].timestamp, item[1].filename))
    return candidates[:TOP_MATCHES_PER_IMAGE]


def summarize_batches(
    target_items: list[MediaItem],
    reference_items: list[MediaItem],
    per_image_matches: dict[str, list[tuple[float, MediaItem]]],
) -> list[dict[str, object]]:
    target_batches: dict[str, list[MediaItem]] = defaultdict(list)
    reference_bursts: dict[str, list[MediaItem]] = defaultdict(list)
    for item in target_items:
        target_batches[item.cluster_id].append(item)
    for item in reference_items:
        reference_bursts[item.cluster_id].append(item)

    summaries: list[dict[str, object]] = []
    for batch_id, items in sorted(target_batches.items()):
        burst_scores: dict[str, float] = defaultdict(float)
        burst_hits: dict[str, set[str]] = defaultdict(set)

        for item in items:
            best_for_burst: dict[str, float] = {}
            for rank, (score, reference_item) in enumerate(
                per_image_matches[item.filename], start=1
            ):
                boost = score / rank
                if boost > best_for_burst.get(reference_item.cluster_id, 0.0):
                    best_for_burst[reference_item.cluster_id] = boost
            for burst_id, boost in best_for_burst.items():
                burst_scores[burst_id] += boost
                burst_hits[burst_id].add(item.filename)

        ordered = sorted(
            burst_scores.items(),
            key=lambda item: (
                -item[1],
                -len(burst_hits[item[0]]),
                reference_bursts[item[0]][0].timestamp,
            ),
        )

        for burst_id, weighted_score in ordered[:TOP_BURSTS_PER_BATCH]:
            burst_members = reference_bursts[burst_id]
            summaries.append(
                {
                    "target_batch_id": batch_id,
                    "target_batch_size": len(items),
                    "reference_burst_id": burst_id,
                    "reference_burst_start": burst_members[0].timestamp.isoformat(
                        timespec="seconds"
                    ),
                    "reference_burst_end": burst_members[-1].timestamp.isoformat(
                        timespec="seconds"
                    ),
                    "reference_burst_size": len(burst_members),
                    "matched_target_images": len(burst_hits[burst_id]),
                    "coverage_ratio": f"{len(burst_hits[burst_id]) / len(items):.3f}",
                    "weighted_score": f"{weighted_score:.4f}",
                }
            )
    return summaries


def write_per_image_matches(
    path: Path,
    target_items: list[MediaItem],
    matches: dict[str, list[tuple[float, MediaItem]]],
) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "target_batch_id",
                "target_filename",
                "target_time_anchor",
                "target_confidence",
                "rank",
                "score",
                "reference_filename",
                "reference_timestamp",
                "reference_burst_id",
            ]
        )
        for item in target_items:
            for rank, (score, reference_item) in enumerate(matches[item.filename], start=1):
                writer.writerow(
                    [
                        item.cluster_id,
                        item.filename,
                        item.timestamp.isoformat(timespec="seconds"),
                        item.confidence,
                        rank,
                        f"{score:.4f}",
                        reference_item.filename,
                        reference_item.timestamp.isoformat(timespec="seconds"),
                        reference_item.cluster_id,
                    ]
                )


def write_batch_summary_csv(path: Path, summaries: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "target_batch_id",
                "target_batch_size",
                "reference_burst_id",
                "reference_burst_start",
                "reference_burst_end",
                "reference_burst_size",
                "matched_target_images",
                "coverage_ratio",
                "weighted_score",
            ],
        )
        writer.writeheader()
        for row in summaries:
            writer.writerow(row)


def write_text_summary(path: Path, summaries: list[dict[str, object]]) -> None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in summaries:
        grouped[str(row["target_batch_id"])].append(row)

    with path.open("w") as handle:
        for batch_id in sorted(grouped):
            rows = grouped[batch_id]
            batch_size = rows[0]["target_batch_size"]
            handle.write(f"{batch_id} ({batch_size} target images)\n")
            for row in rows[:5]:
                handle.write(
                    "  "
                    f"{row['reference_burst_id']} start={row['reference_burst_start']} "
                    f"end={row['reference_burst_end']} size={row['reference_burst_size']} "
                    f"coverage={row['matched_target_images']}/{batch_size} "
                    f"weighted_score={row['weighted_score']}\n"
                )
            handle.write("\n")


def infer_timestamps(
    base_dir: Path,
    timeline_name: str = TIMELINE_CSV,
    per_image_name: str = PER_IMAGE_OUTPUT,
    burst_name: str = BURST_OUTPUT,
    summary_name: str = SUMMARY_OUTPUT,
) -> tuple[Path, Path, Path]:
    reference_items, target_items = load_media_items(base_dir, timeline_name=timeline_name)
    if not reference_items or not target_items:
        raise RuntimeError(
            "Need both reference images and target images in the timeline CSV."
        )

    assign_time_groups(reference_items, REFERENCE_BURST_GAP_MINUTES, "reference_burst_")
    assign_time_groups(target_items, TARGET_BATCH_GAP_MINUTES, "target_batch_")

    reference_features = {
        item.filename: build_features(item.path) for item in reference_items
    }
    target_features = {item.filename: build_features(item.path) for item in target_items}

    for item in reference_items:
        feature = reference_features[item.filename]
        item.orientation = feature.orientation
        item.aspect_ratio = feature.aspect_ratio
    for item in target_items:
        feature = target_features[item.filename]
        item.orientation = feature.orientation
        item.aspect_ratio = feature.aspect_ratio

    per_image_matches: dict[str, list[tuple[float, MediaItem]]] = {}
    for item in target_items:
        per_image_matches[item.filename] = top_matches_for_item(
            item, target_features[item.filename], reference_items, reference_features
        )

    summaries = summarize_batches(target_items, reference_items, per_image_matches)

    per_image_path = base_dir / per_image_name
    burst_path = base_dir / burst_name
    summary_path = base_dir / summary_name
    write_per_image_matches(per_image_path, target_items, per_image_matches)
    write_batch_summary_csv(burst_path, summaries)
    write_text_summary(summary_path, summaries)
    return per_image_path, burst_path, summary_path


def infer_context(*args, **kwargs):
    return infer_timestamps(*args, **kwargs)
