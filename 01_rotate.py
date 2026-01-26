import os
import json
import argparse
from datetime import datetime
from typing import Optional, List, Tuple
from PIL import Image, ExifTags

Image.MAX_IMAGE_PIXELS = None
VALID_EXTS = (".jpg", ".jpeg", ".png")


def load_config(config_file: str) -> dict:
    with open(config_file, "r", encoding="utf8") as f:
        return json.load(f)


def get_exif_date_taken(file_path: str) -> Optional[datetime]:
    """
    Preferred order:
    DateTimeOriginal -> DateTimeDigitized -> DateTime
    """
    try:
        img = Image.open(file_path)
        exif = img._getexif()
        if not exif:
            return None

        decoded = {}
        for tag, value in exif.items():
            decoded_tag = ExifTags.TAGS.get(tag, tag)
            decoded[decoded_tag] = value

        for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
            value = decoded.get(key)
            if value:
                try:
                    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                except Exception:
                    pass
    except Exception:
        return None

    return None


def get_image_sort_key(file_path: str):
    exif_date = get_exif_date_taken(file_path)
    if exif_date:
        return exif_date
    try:
        return datetime.fromtimestamp(os.path.getmtime(file_path))
    except Exception:
        return os.path.basename(file_path).lower()


def collect_images(folder_path: str) -> List[str]:
    files = []
    for fn in os.listdir(folder_path):
        fp = os.path.join(folder_path, fn)
        if not os.path.isfile(fp):
            continue
        fn_l = fn.lower()
        if not fn_l.endswith(VALID_EXTS):
            continue
        # exclude artifacts
        if "_processed" in fn_l:
            continue
        if fn_l.startswith("pdfprep__"):
            continue
        files.append(fp)
    return files


from PIL import Image, ExifTags, ImageOps


def suggested_rotation_degrees(image_path: str) -> int:
    """
    Suggestion baseline:
    - After applying EXIF orientation:
      portrait -> 0
      landscape -> 90  (to better fit A4 portrait pages)
    """
    try:
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
        return 0 if h >= w else 270
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Create rotation plan TXT for photobook images.")
    parser.add_argument("config", type=str, help="Path to config JSON.")
    args = parser.parse_args()

    config = load_config(args.config)
    output_folder = config["output_folder"]
    os.makedirs(output_folder, exist_ok=True)

    plan_path = os.path.join(output_folder, "rotation_plan.txt")

    lines: List[str] = []
    lines.append("# rotation plan v1")
    lines.append("# format: relative_path | rotation_degrees")
    lines.append("# allowed degrees: 0, 90, 180, 270")
    lines.append("# edit the degrees manually, then run 02_build_photobook_from_plan.py")
    lines.append("")

    # build a stable ordered list across all chapters
    # and store as paths relative to config location (or current working dir)
    all_items: List[Tuple[str, str]] = []  # (heading, abs_path)

    for folder_path, heading, _thumb_rel in config["input_folders"]:
        images = collect_images(folder_path)
        images_sorted = sorted(images, key=lambda p: (get_image_sort_key(p), os.path.basename(p).lower()))
        for p in images_sorted:
            all_items.append((heading, p))

    current_heading = None
    for heading, abs_path in all_items:
        if heading != current_heading:
            lines.append(f"# Chapter: {heading}")
            current_heading = heading

        rel_path = abs_path.replace("\\", "/")
        deg = suggested_rotation_degrees(abs_path)
        lines.append(f"{rel_path} | {deg}")

    with open(plan_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Wrote rotation plan: {plan_path}")
    print("Edit that file, then run: 02_build_photobook_from_plan.py <config>")


if __name__ == "__main__":
    main()
