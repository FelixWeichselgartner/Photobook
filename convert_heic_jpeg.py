#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()


def convert_one(src: Path, dst: Path, quality: int) -> None:
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)  # applies the 270° rotation correctly

        # Convert to RGB for JPEG output
        if im.mode != "RGB":
            im = im.convert("RGB")

        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=quality, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", help="Folder to scan recursively")
    ap.add_argument("--quality", type=int, default=92, help="JPEG quality 1-100 (default 92)")
    ap.add_argument("--output-dir", default=None, help='Default: "<input_dir>/converted_jpeg"')
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing JPEGs")
    args = ap.parse_args()

    in_dir = Path(args.input_dir).expanduser().resolve()
    if not in_dir.is_dir():
        print(f"Not a directory: {in_dir}", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else in_dir / "converted_jpeg"
    quality = max(1, min(100, args.quality))

    exts = {".heic", ".heif"}
    files = [p for p in in_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts]

    if not files:
        print("No .heic/.heif files found.")
        return 0

    converted = skipped = failed = 0

    for src in files:
        rel = src.relative_to(in_dir)
        dst = (out_dir / rel).with_suffix(".jpg")

        if dst.exists() and not args.overwrite:
            skipped += 1
            continue

        try:
            convert_one(src, dst, quality)
            converted += 1
        except Exception as e:
            failed += 1
            print(f"FAILED: {src}\n  {e}", file=sys.stderr)

    print(f"Done. Converted={converted}, Skipped={skipped}, Failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
