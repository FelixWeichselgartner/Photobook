#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()

ORIENTATION_TAG = 274  # EXIF Orientation


def _get_exif_for_write(im: Image.Image) -> bytes | None:
    """
    Return EXIF bytes suitable for saving, preferring a parsed Exif object when possible.
    """
    # 1) If PIL already parsed it, this is usually best
    exif_obj = im.getexif()
    if exif_obj and len(exif_obj) > 0:
        return exif_obj.tobytes()

    # 2) Some HEIC loaders provide raw bytes here
    exif_bytes = im.info.get("exif")
    if exif_bytes:
        return exif_bytes

    return None


def _set_orientation_normal(exif_bytes: bytes) -> bytes:
    """
    Load EXIF bytes, set Orientation=1, and return updated bytes.
    """
    exif = Image.Exif()
    exif.load(exif_bytes)
    exif[ORIENTATION_TAG] = 1
    return exif.tobytes()


def convert_one(src: Path, dst: Path, quality: int, preserve_fs_times: bool) -> None:
    with Image.open(src) as im:
        # Capture metadata before operations
        exif_bytes = _get_exif_for_write(im)
        icc_profile = im.info.get("icc_profile")

        # Rotate pixels according to EXIF Orientation
        im = ImageOps.exif_transpose(im)

        # If we had EXIF, normalize Orientation to 1 after transpose
        if exif_bytes:
            try:
                exif_bytes = _set_orientation_normal(exif_bytes)
            except Exception:
                # If EXIF parsing fails for any reason, fall back to original bytes
                pass

        if im.mode != "RGB":
            im = im.convert("RGB")

        dst.parent.mkdir(parents=True, exist_ok=True)

        save_kwargs = dict(format="JPEG", quality=quality, optimize=True)
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile

        im.save(dst, **save_kwargs)

    # Optional: preserve filesystem timestamps (mtime/atime) as well
    if preserve_fs_times:
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", help="Folder to scan recursively")
    ap.add_argument("--quality", type=int, default=92, help="JPEG quality 1-100 (default 92)")
    ap.add_argument("--output-dir", default=None, help='Default: "<input_dir>/converted_jpeg"')
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing JPEGs")
    ap.add_argument(
        "--preserve-fs-times",
        action="store_true",
        help="Also copy filesystem timestamps (mtime/atime) from source to output",
    )
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
            convert_one(src, dst, quality, args.preserve_fs_times)
            converted += 1
        except Exception as e:
            failed += 1
            print(f"FAILED: {src}\n  {e}", file=sys.stderr)

    print(f"Done. Converted={converted}, Skipped={skipped}, Failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
