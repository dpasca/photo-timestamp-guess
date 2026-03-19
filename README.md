# photo-timestamp-guess

`photo-timestamp-guess` is a small utility for mixed media folders where:

- some files still have trustworthy timestamps from embedded metadata or exported filenames
- some files only have weak timestamps such as download time or filesystem creation time
- you want a best-effort reconstruction of where the weak-timestamp photos likely belong

## What It Does

It helps when messenger apps, downloads, exports, or cloud sync preserve some photos well and strip metadata from others.

The workflow is:

1. scan the folder and classify files into trustworthy timestamp references vs weak timestamp targets
2. build a chronological timeline from embedded metadata, filename timestamps, and filesystem timestamps
3. compare target images to nearby reference images using cheap local visual similarity
4. produce a review page so you can visually judge whether the guessed placement looks right
5. write a rename plan that can make filename sorting reflect the reconstructed order

The tool does four things:

1. builds a media timeline from embedded metadata, filename timestamps, and filesystem birth time
2. compares weak-timestamp target images against nearby trustworthy reference images using cheap local image similarity
3. generates an HTML review page so you can visually judge the inferred time windows yourself
4. writes a dry-run rename plan so filename sorting can reflect the reconstructed timestamps

## Install

```bash
python3 -m pip install -e .
```

## Usage

Run the full workflow against a media folder:

```bash
photo-timestamp-guess all /path/to/media/folder
```

Or run the steps one by one:

```bash
photo-timestamp-guess timeline /path/to/media/folder
photo-timestamp-guess match /path/to/media/folder
photo-timestamp-guess review /path/to/media/folder
photo-timestamp-guess rename /path/to/media/folder
```

`rename` is a dry run by default. To actually rename files:

```bash
photo-timestamp-guess rename /path/to/media/folder --apply
```

## Outputs

The tool writes these files into the target media folder:

- `media_timeline.csv`
- `timestamp_candidate_matches.csv`
- `timestamp_batch_burst_summary.csv`
- `timestamp_batch_summary.txt`
- `timestamp_review.html`
- `timestamp_rename_plan.csv`

## Naming behavior

- reference files keep their original names by default
- trusted videos with non-sortable names are normalized to `YYYY-MM-DD HH.MM.SS.ext`
- inferred target files are anchored to the matched reference filename, for example `2026-03-16 10.14.09_02.jpg`
- additional inferred files matching the same reference become `..._03`, `..._04`, and so on
- unmatched weak-timestamp files keep their original names

## Notes

- The inference step is heuristic. It is meant to suggest likely event windows, not recover true capture timestamps.
- The review page is the main safety valve. You should visually confirm the suggested matches before applying any rename plan.
- The anchored rename strategy works best when the reference filenames already sort in the order you want.
