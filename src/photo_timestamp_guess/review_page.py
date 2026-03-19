from __future__ import annotations

import csv
import html
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .matcher import assign_time_groups, load_media_items


OUTPUT_HTML = "timestamp_review.html"
PAIR_SAMPLE_LIMIT = 18
BURST_LIMIT = 4


def load_batch_summaries(base_dir: Path) -> dict[str, list[dict[str, str]]]:
    summaries: dict[str, list[dict[str, str]]] = defaultdict(list)
    with (base_dir / "timestamp_batch_burst_summary.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            summaries[row["target_batch_id"]].append(row)
    for batch_id in summaries:
        summaries[batch_id].sort(
            key=lambda row: (
                -float(row["weighted_score"]),
                -int(row["matched_target_images"]),
                row["reference_burst_start"],
            )
        )
    return summaries


def load_pair_samples(base_dir: Path) -> dict[str, list[dict[str, str]]]:
    samples: dict[str, list[dict[str, str]]] = defaultdict(list)
    with (base_dir / "timestamp_candidate_matches.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["rank"] == "1":
                samples[row["target_batch_id"]].append(row)
    for batch_id in samples:
        samples[batch_id].sort(
            key=lambda row: (
                -float(row["score"]),
                row["reference_timestamp"],
                row["target_filename"],
            )
        )
    return samples


def image_src(path: Path) -> str:
    return quote(path.name)


def format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def image_card(path: Path, label: str, subtitle: str = "") -> str:
    safe_label = html.escape(label)
    safe_subtitle = html.escape(subtitle)
    src = image_src(path)
    caption = f"<div class='caption'>{safe_label}</div>"
    if subtitle:
        caption += f"<div class='subcaption'>{safe_subtitle}</div>"
    return (
        "<a class='thumb-card' href='{src}' target='_blank' rel='noopener'>"
        "<img loading='lazy' src='{src}' alt='{alt}'>"
        "{caption}"
        "</a>"
    ).format(src=src, alt=safe_label, caption=caption)


def render_image_grid(items, label_fn, subtitle_fn=None) -> str:
    cards = []
    for item in items:
        subtitle = subtitle_fn(item) if subtitle_fn else ""
        cards.append(image_card(item.path, label_fn(item), subtitle))
    return "<div class='thumb-grid'>" + "".join(cards) + "</div>"


def build_review_page(base_dir: Path, output_name: str = OUTPUT_HTML) -> Path:
    reference_items, target_items = load_media_items(base_dir)
    assign_time_groups(reference_items, 20, "reference_burst_")
    assign_time_groups(target_items, 30, "target_batch_")

    reference_bursts: dict[str, list] = defaultdict(list)
    target_batches: dict[str, list] = defaultdict(list)
    reference_by_name = {}

    for item in reference_items:
        reference_bursts[item.cluster_id].append(item)
        reference_by_name[item.filename] = item
    for item in target_items:
        target_batches[item.cluster_id].append(item)

    summaries = load_batch_summaries(base_dir)
    pair_samples = load_pair_samples(base_dir)

    batch_sections = []
    for batch_id in sorted(target_batches):
        batch_items = target_batches[batch_id]
        batch_start = batch_items[0].timestamp
        batch_end = batch_items[-1].timestamp
        summary_rows = summaries.get(batch_id, [])[:BURST_LIMIT]
        sample_rows = pair_samples.get(batch_id, [])[:PAIR_SAMPLE_LIMIT]

        burst_cards = []
        for row in summary_rows:
            burst_id = row["reference_burst_id"]
            burst_items = reference_bursts.get(burst_id, [])
            burst_grid = render_image_grid(
                burst_items,
                label_fn=lambda item: item.filename,
                subtitle_fn=lambda item: format_time(item.timestamp),
            )
            burst_cards.append(
                """
                <section class="burst-card">
                  <h4>{burst_id}</h4>
                  <p class="meta">
                    {start} to {end} | {size} reference photos | coverage {matched}/{batch_size} | score {score}
                  </p>
                  {grid}
                </section>
                """.format(
                    burst_id=html.escape(burst_id),
                    start=html.escape(row["reference_burst_start"].replace("T", " ")),
                    end=html.escape(row["reference_burst_end"].replace("T", " ")),
                    size=html.escape(row["reference_burst_size"]),
                    matched=html.escape(row["matched_target_images"]),
                    batch_size=html.escape(row["target_batch_size"]),
                    score=html.escape(row["weighted_score"]),
                    grid=burst_grid,
                )
            )

        pair_cards = []
        for row in sample_rows:
            reference_item = reference_by_name[row["reference_filename"]]
            target_item = next(item for item in batch_items if item.filename == row["target_filename"])
            pair_cards.append(
                """
                <div class="pair-card">
                  <div class="pair-meta">
                    <strong>score {score}</strong>
                    <span>{reference_burst}</span>
                  </div>
                  <div class="pair-images">
                    {target_card}
                    {reference_card}
                  </div>
                </div>
                """.format(
                    score=html.escape(row["score"]),
                    reference_burst=html.escape(row["reference_burst_id"]),
                    target_card=image_card(
                        target_item.path,
                        f"Target: {target_item.filename}",
                        f"anchor {format_time(target_item.timestamp)}",
                    ),
                    reference_card=image_card(
                        reference_item.path,
                        f"Reference: {reference_item.filename}",
                        format_time(reference_item.timestamp),
                    ),
                )
            )

        batch_sections.append(
            """
            <section class="batch-section" id="{batch_id}">
              <div class="batch-header">
                <h2>{batch_id}</h2>
                <p>{count} target images | anchor time {start} to {end}</p>
              </div>
              <div class="two-col">
                <section>
                  <h3>Target Batch</h3>
                  {target_grid}
                </section>
                <section>
                  <h3>Most Likely Reference Bursts</h3>
                  {burst_cards}
                </section>
              </div>
              <section class="pairs-section">
                <h3>Strongest Individual Pairings</h3>
                <p class="meta">These are the top rank-1 matches from the cheap local similarity pass.</p>
                <div class="pair-grid">
                  {pair_cards}
                </div>
              </section>
            </section>
            """.format(
                batch_id=html.escape(batch_id),
                count=len(batch_items),
                start=html.escape(format_time(batch_start)),
                end=html.escape(format_time(batch_end)),
                target_grid=render_image_grid(
                    batch_items,
                    label_fn=lambda item: item.filename,
                    subtitle_fn=lambda item: f"{format_time(item.timestamp)} | {item.confidence}",
                ),
                burst_cards="".join(burst_cards),
                pair_cards="".join(pair_cards),
            )
        )

    page_html = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Timestamp Reconstruction Review</title>
      <style>
        :root {{
          --bg: #f6f1e8;
          --card: #fffdf8;
          --ink: #24312a;
          --muted: #5f6f67;
          --line: #d8cdbb;
          --accent: #326b5b;
          --shadow: 0 12px 28px rgba(36, 49, 42, 0.08);
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
          color: var(--ink);
          background:
            radial-gradient(circle at top left, rgba(80, 142, 117, 0.16), transparent 28rem),
            linear-gradient(180deg, #f8f4ec, var(--bg));
        }}
        a {{ color: inherit; }}
        .page {{
          max-width: 1480px;
          margin: 0 auto;
          padding: 32px 24px 72px;
        }}
        .intro {{
          background: rgba(255, 253, 248, 0.88);
          border: 1px solid var(--line);
          border-radius: 24px;
          padding: 24px 28px;
          box-shadow: var(--shadow);
          margin-bottom: 28px;
        }}
        h1, h2, h3, h4 {{
          margin: 0 0 10px;
          line-height: 1.1;
        }}
        h1 {{ font-size: 2.2rem; }}
        h2 {{ font-size: 1.6rem; }}
        h3 {{ font-size: 1.15rem; }}
        h4 {{ font-size: 1rem; }}
        p {{ margin: 0; line-height: 1.45; }}
        .intro p + p {{ margin-top: 10px; }}
        .jump-links {{
          display: flex;
          gap: 12px;
          flex-wrap: wrap;
          margin-top: 16px;
        }}
        .jump-links a {{
          text-decoration: none;
          background: var(--accent);
          color: white;
          padding: 10px 14px;
          border-radius: 999px;
          font-size: 0.95rem;
        }}
        .batch-section {{
          margin-top: 28px;
          background: rgba(255, 253, 248, 0.92);
          border: 1px solid var(--line);
          border-radius: 24px;
          padding: 22px;
          box-shadow: var(--shadow);
        }}
        .batch-header {{
          margin-bottom: 18px;
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: baseline;
          flex-wrap: wrap;
        }}
        .two-col {{
          display: grid;
          grid-template-columns: 1.2fr 1fr;
          gap: 22px;
        }}
        .thumb-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
          gap: 10px;
        }}
        .thumb-card {{
          display: block;
          text-decoration: none;
          background: var(--card);
          border: 1px solid var(--line);
          border-radius: 16px;
          overflow: hidden;
          box-shadow: 0 6px 14px rgba(36, 49, 42, 0.05);
        }}
        .thumb-card img {{
          display: block;
          width: 100%;
          height: 112px;
          object-fit: cover;
          background: #e7e0d3;
        }}
        .caption, .subcaption {{
          padding: 0 10px;
          overflow-wrap: anywhere;
        }}
        .caption {{
          padding-top: 8px;
          font-size: 0.82rem;
        }}
        .subcaption {{
          padding-bottom: 10px;
          color: var(--muted);
          font-size: 0.74rem;
        }}
        .burst-card + .burst-card {{
          margin-top: 18px;
        }}
        .meta {{
          color: var(--muted);
          margin-bottom: 10px;
        }}
        .pairs-section {{
          margin-top: 24px;
        }}
        .pair-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
          gap: 14px;
          margin-top: 12px;
        }}
        .pair-card {{
          background: var(--card);
          border: 1px solid var(--line);
          border-radius: 18px;
          padding: 12px;
        }}
        .pair-meta {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: baseline;
          margin-bottom: 10px;
          color: var(--muted);
          font-size: 0.9rem;
        }}
        .pair-images {{
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
        }}
        @media (max-width: 980px) {{
          .two-col {{
            grid-template-columns: 1fr;
          }}
        }}
        @media (max-width: 640px) {{
          .page {{
            padding: 18px 14px 48px;
          }}
          h1 {{ font-size: 1.7rem; }}
          .pair-images {{
            grid-template-columns: 1fr;
          }}
        }}
      </style>
    </head>
    <body>
      <main class="page">
        <section class="intro">
          <h1>Timestamp Reconstruction Review</h1>
          <p>This page compares each weak-timestamp target batch with the most likely reference bursts found by the cheap local image matcher.</p>
          <p>The scores are only hints. The goal is to visually judge whether a target batch belongs near a specific reference time window.</p>
          <div class="jump-links">
            {jump_links}
          </div>
        </section>
        {batch_sections}
      </main>
    </body>
    </html>
    """.format(
        jump_links="".join(
            f"<a href='#{html.escape(batch_id)}'>{html.escape(batch_id)}</a>"
            for batch_id in sorted(target_batches)
        ),
        batch_sections="".join(batch_sections),
    )

    output_path = base_dir / output_name
    output_path.write_text(page_html)
    return output_path
