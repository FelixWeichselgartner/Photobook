import os
import json
import argparse
from datetime import datetime
from typing import Optional, Dict, List

from fpdf import FPDF
from PIL import Image, ExifTags
from tqdm import tqdm

# --- PDF size control ---
PHOTO_TARGET_DPI = 200          # was effectively ~300
MAP_TARGET_DPI = 170            # maps can be lower
PHOTO_JPEG_QUALITY = 72         # 65–80 is typical; lower = smaller
MAP_JPEG_QUALITY = 70


Image.MAX_IMAGE_PIXELS = None
VALID_EXTS = (".jpg", ".jpeg", ".png")


# -------------------------------------------------
# Config + Sorting
# -------------------------------------------------
def load_config(config_file: str) -> dict:
    with open(config_file, "r", encoding="utf8") as f:
        return json.load(f)


def get_exif_date_taken(file_path: str) -> Optional[datetime]:
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


# -------------------------------------------------
# Rotation plan
# -------------------------------------------------
def parse_rotation_plan(plan_path: str) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    with open(plan_path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "|" not in s:
                continue
            path_part, deg_part = [x.strip() for x in s.split("|", 1)]
            deg = int(deg_part)
            if deg not in (0, 90, 180, 270):
                raise ValueError(f"Invalid rotation degree '{deg}' in line: {line}")
            mapping[path_part.replace("\\", "/")] = deg
    return mapping


# -------------------------------------------------
# GPS map lookup
# -------------------------------------------------
def find_corresponding_gps_image(
    photo_path: str,
    gps_folder: Optional[str],
    mode: str = "stem_contains",
) -> Optional[str]:
    if not gps_folder or not os.path.isdir(gps_folder):
        return None

    stem = os.path.splitext(os.path.basename(photo_path))[0].lower()

    for fn in os.listdir(gps_folder):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        name_l = fn.lower()
        fp = os.path.join(gps_folder, fn)

        if mode == "exact":
            if os.path.splitext(fn)[0].lower() == stem:
                return fp

        token = f"__{stem}__map_"
        if token in name_l:
            return fp

    if mode != "exact":
        for fn in os.listdir(gps_folder):
            if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            if stem in fn.lower():
                return os.path.join(gps_folder, fn)

    return None


# -------------------------------------------------
# Caching
# -------------------------------------------------


from PIL import ImageOps


from PIL import Image, ImageOps

from PIL import Image, ImageOps

def process_photo_to_cache(
    src_path: str,
    cache_folder: str,
    rotation_deg_cw: int,
):
    os.makedirs(cache_folder, exist_ok=True)

    base = os.path.splitext(os.path.basename(src_path))[0]
    out_path = os.path.join(
        cache_folder,
        f"{base}__rot{rotation_deg_cw}__q{PHOTO_JPEG_QUALITY}__dpi{PHOTO_TARGET_DPI}.jpg",
    )

    if os.path.exists(out_path):
        with Image.open(out_path) as im:
            return out_path, im.size[0], im.size[1]

    # A4 @ target dpi (portrait)
    max_w = int(round(8.27 * PHOTO_TARGET_DPI))   # 210mm / 25.4
    max_h = int(round(11.69 * PHOTO_TARGET_DPI))  # 297mm / 25.4

    with Image.open(src_path) as img:
        img = ImageOps.exif_transpose(img)

        if rotation_deg_cw % 360 != 0:
            img = img.rotate(-rotation_deg_cw, expand=True)

        if img.mode != "RGB":
            img = img.convert("RGB")

        # IMPORTANT: no upscaling, only downscale
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        w2, h2 = img.size

        img.save(
            out_path,
            "JPEG",
            quality=PHOTO_JPEG_QUALITY,
            optimize=True,
            progressive=True,
            subsampling=2,  # 4:2:0 chroma subsampling (smaller)
        )

    return out_path, w2, h2


def process_map_to_cache(
    map_path: str,
    cache_folder: str,
    rotate_deg_cw: int = 270,
):
    os.makedirs(cache_folder, exist_ok=True)

    base = os.path.splitext(os.path.basename(map_path))[0]
    out_path = os.path.join(
        cache_folder,
        f"{base}__mapcache_rot{rotate_deg_cw}__q{MAP_JPEG_QUALITY}__dpi{MAP_TARGET_DPI}.jpg",
    )

    if os.path.exists(out_path):
        with Image.open(out_path) as im:
            return out_path, im.size[0], im.size[1]

    # A4 width at target dpi, but short height (banner)
    max_w = int(round(8.27 * MAP_TARGET_DPI))
    max_h = int(round(2.2 * MAP_TARGET_DPI))  # banner height ~2.2 inches; tune as needed

    with Image.open(map_path) as img:
        img = ImageOps.exif_transpose(img)

        if rotate_deg_cw % 360 != 0:
            img = img.rotate(-rotate_deg_cw, expand=True)

        if img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((max_w, max_h), Image.LANCZOS)
        w2, h2 = img.size

        img.save(
            out_path,
            "JPEG",
            quality=MAP_JPEG_QUALITY,
            optimize=True,
            progressive=True,
            subsampling=2,
        )

    return out_path, w2, h2



def process_image_for_pdf(image_path: str, temp_folder: str, max_width_mm: float, max_height_mm: float):
    """
    Preps title/chapter thumbnails; writes into out/PDF_Temp (never next to source).
    """
    os.makedirs(temp_folder, exist_ok=True)
    img = Image.open(image_path)
    dpi = 300

    img_width_mm = img.width * 25.4 / dpi
    img_height_mm = img.height * 25.4 / dpi

    scale = min(max_width_mm / img_width_mm, max_height_mm / img_height_mm)
    img_width_mm *= scale
    img_height_mm *= scale

    processed = os.path.join(temp_folder, f"pdfprep__{os.path.basename(image_path)}")
    img.save(processed, "JPEG")

    return img_width_mm, img_height_mm, processed


# -------------------------------------------------
# PDF
# -------------------------------------------------
class CustomPDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", size=10)
        self.cell(0, 10, f"- {self.page_no()} -", align="C")


def enhanced_title_page(pdf: CustomPDF, config: dict, pdf_temp_folder: str):
    pdf.add_page()
    pdf.set_font("Helvetica", size=36, style="B")
    pdf.cell(0, 30, config.get("title", "Photobook"), align="C", ln=True)
    pdf.ln(10)

    thumb = config.get("thumbnail")
    if thumb and os.path.isfile(thumb):
        w_mm, h_mm, processed = process_image_for_pdf(thumb, pdf_temp_folder, 150, 150)
        pdf.image(processed, x=(210 - w_mm) / 2, y=pdf.get_y(), w=w_mm, h=h_mm)
        pdf.ln(h_mm + 15)

    pdf.set_font("Helvetica", size=16)
    pdf.cell(0, 10, "Inhalte:", align="L", ln=True)
    for folder in config.get("input_folders", []):
        pdf.cell(0, 10, f"- {folder[1]}", align="L", ln=True)
    pdf.ln(10)


def chapter_page(pdf: CustomPDF, chapter: str, thumbnail: Optional[str], pdf_temp_folder: str):
    pdf.add_page()
    pdf.ln(20)
    pdf.set_font("Helvetica", size=36, style="B")
    pdf.cell(0, 10, chapter, align="C", ln=True)
    pdf.ln(10)

    if thumbnail and os.path.isfile(thumbnail):
        w_mm, h_mm, processed = process_image_for_pdf(thumbnail, pdf_temp_folder, 175, 220)
        pdf.image(processed, x=(210 - w_mm) / 2, y=pdf.get_y(), w=w_mm, h=h_mm)
        pdf.ln(h_mm + 15)


# -------------------------------------------------
# Main
# -------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Build photobook PDF from rotation plan (map above photo).")
    parser.add_argument("config", type=str, help="Path to config JSON.")
    args = parser.parse_args()

    config = load_config(args.config)

    output_folder = config["output_folder"]
    os.makedirs(output_folder, exist_ok=True)

    plan_path = os.path.join(output_folder, "rotation_plan.txt")
    if not os.path.isfile(plan_path):
        raise FileNotFoundError(f"Rotation plan not found: {plan_path}")

    rotations = parse_rotation_plan(plan_path)

    gps_folder = config.get("gps_image_folder")
    gps_match = config.get("gps_match", "stem_contains")

    output_pdf = os.path.join(output_folder, "Photobook.pdf")
    resized_folder = os.path.join(output_folder, "Resized_Images")
    map_cache_folder = os.path.join(output_folder, "Map_Cache")
    pdf_temp_folder = os.path.join(output_folder, "PDF_Temp")

    pdf = CustomPDF("P", "mm", "A4")
    pdf.set_auto_page_break(auto=False)

    # Page geometry
    page_w = 210
    page_h = 297
    margin_x = 10
    margin_y = 12
    gutter_y = 6

    usable_w = page_w - 2 * margin_x
    usable_h = page_h - 2 * margin_y

    # Map area (top). Tune if you want bigger/smaller map strip.
    map_max_h = 60.0  # mm
    map_area_h = map_max_h

    # Photo area (below map)
    photo_area_h = usable_h - map_area_h - gutter_y
    if photo_area_h <= 50:
        raise RuntimeError("Layout too tight: increase page margins or reduce map_max_h.")

    #enhanced_title_page(pdf, config, pdf_temp_folder)

    for folder_path, heading, thumb_rel in config["input_folders"]:
        chapter_thumb = os.path.join(folder_path, thumb_rel) if thumb_rel else None
        chapter_page(pdf, heading, chapter_thumb, pdf_temp_folder)

        images = collect_images(folder_path)
        images_sorted = sorted(images, key=lambda p: (get_image_sort_key(p), os.path.basename(p).lower()))

        for src_path in tqdm(images_sorted, desc=f"Pages: {heading}", unit="page"):
            rel = src_path.replace("\\", "/")
            if rel not in rotations:
                raise KeyError(f"Missing rotation entry for: {rel}  (edit {plan_path})")

            deg = rotations[rel]

            # Cache rotated/resized photo
            photo_cached, pw_px, ph_px = process_photo_to_cache(src_path, resized_folder, deg)

            # Find + cache map
            gps_path = find_corresponding_gps_image(src_path, gps_folder, mode=gps_match)
            gps_cached = None
            mw_px = mh_px = None
            if gps_path and os.path.isfile(gps_path):
                gps_cached, mw_px, mh_px = process_map_to_cache(gps_path, map_cache_folder)

            pdf.add_page()

            # --- MAP (top, full width, bounded height) ---
            if gps_cached and mw_px and mh_px:
                map_w_mm = mw_px * 25.4 / 300
                map_h_mm = mh_px * 25.4 / 300

                scale_map = min(usable_w / map_w_mm, map_area_h / map_h_mm)
                w_map = map_w_mm * scale_map
                h_map = map_h_mm * scale_map

                x_map = margin_x + (usable_w - w_map) / 2
                y_map = margin_y + (map_area_h - h_map) / 2
                pdf.image(gps_cached, x=x_map, y=y_map, w=w_map, h=h_map)

            # --- PHOTO (below map, uses remaining height) ---
            photo_w_mm = pw_px * 25.4 / 300
            photo_h_mm = ph_px * 25.4 / 300

            scale_photo = min(usable_w / photo_w_mm, photo_area_h / photo_h_mm)
            w_photo = photo_w_mm * scale_photo
            h_photo = photo_h_mm * scale_photo

            x_photo = margin_x + (usable_w - w_photo) / 2
            y_photo = margin_y + map_area_h + gutter_y + (photo_area_h - h_photo) / 2
            pdf.image(photo_cached, x=x_photo, y=y_photo, w=w_photo, h=h_photo)

    pdf.output(output_pdf)
    print(f"Created: {output_pdf}")


if __name__ == "__main__":
    main()
