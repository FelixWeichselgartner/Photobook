import os
import json
import argparse
from datetime import datetime
import math

from fpdf import FPDF
from PIL import Image, ExifTags
from tqdm import tqdm
from pdf2image import convert_from_path

Image.MAX_IMAGE_PIXELS = None


def load_config(config_file: str) -> dict:
    """Load configuration from a JSON file."""
    with open(config_file, "r", encoding="utf8") as file:
        return json.load(file)


def get_exif_date_taken(file_path: str) -> datetime | None:
    """
    Extract a robust EXIF datetime in preferred order:
    DateTimeOriginal -> DateTimeDigitized -> DateTime
    """
    try:
        img = Image.open(file_path)
        exif = img._getexif()
        if not exif:
            return None

        # Build tag->value map with decoded names
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
    except Exception as e:
        print(f"Error reading EXIF data from {file_path}: {e}")

    return None


def get_image_sort_key(file_path: str):
    """Sort images by EXIF date, then modified date, then filename."""
    exif_date = get_exif_date_taken(file_path)
    if exif_date:
        return exif_date

    try:
        modified_date = os.path.getmtime(file_path)
        return datetime.fromtimestamp(modified_date)
    except Exception as e:
        print(f"Error getting modified date for {file_path}: {e}")

    return os.path.basename(file_path).lower()


def process_image(file_path: str, resized_folder: str, max_width_px: int, max_height_px: int) -> str:
    """
    Resize/rotate/upscale an image only if it has not been processed before.
    Keeps your original behavior: rotate portrait to landscape (so pages are landscape-ish images).
    """
    os.makedirs(resized_folder, exist_ok=True)
    resized_path = os.path.join(resized_folder, os.path.basename(file_path))

    if os.path.exists(resized_path):
        return resized_path

    try:
        img = Image.open(file_path)

        # Your original behavior: rotate if portrait
        if img.height > img.width:
            img = img.rotate(90, expand=True)

        img_width, img_height = img.size

        # Upscale if too small
        if img_width < max_width_px or img_height < max_height_px:
            scaling_factor = max(max_width_px / img_width, max_height_px / img_height)
            img = img.resize(
                (int(img_width * scaling_factor), int(img_height * scaling_factor)),
                Image.LANCZOS,
            )

        # Downscale to fit max
        img.thumbnail((max_width_px, max_height_px), Image.LANCZOS)

        img.save(resized_path, quality=85)
    except Exception as e:
        print(f"Error processing {file_path}: {e}")

    return resized_path


def process_image_for_pdf(image_path: str, max_width_mm: float, max_height_mm: float, allow_rotation: bool = True):
    """
    Resize an image for display in a PDF while maintaining aspect ratio.
    Optionally allow rotation for landscape images.

    Returns:
        (img_width_mm, img_height_mm, processed_image_path)
    """
    img = Image.open(image_path)
    dpi = 300  # scaling assumption

    if allow_rotation and img.width > img.height:
        img = img.rotate(-90, expand=True)

    img_width_mm = img.width * 25.4 / dpi
    img_height_mm = img.height * 25.4 / dpi

    scaling_factor = min(max_width_mm / img_width_mm, max_height_mm / img_height_mm)
    img_width_mm *= scaling_factor
    img_height_mm *= scaling_factor

    processed_image_path = f"{os.path.splitext(image_path)[0]}_processed.jpg"
    img.save(processed_image_path, "JPEG")

    return img_width_mm, img_height_mm, processed_image_path


class CustomPDF(FPDF):
    """Custom PDF class to add page numbering."""
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", size=10)
        self.cell(0, 10, f"- {self.page_no()} -", align="C")


def enhanced_title_page(pdf: CustomPDF, config: dict):
    """Create an enhanced title page."""
    pdf.add_page()
    pdf.set_font("Helvetica", size=36, style="B")
    pdf.cell(0, 30, config.get("title", "Photobook"), align="C", ln=True)
    pdf.ln(10)

    # Add thumbnail if provided
    thumb = config.get("thumbnail")
    if thumb and os.path.isfile(thumb):
        img_width_mm, img_height_mm, processed = process_image_for_pdf(thumb, 150, 150, allow_rotation=False)
        pdf.image(processed, x=(210 - img_width_mm) / 2, y=pdf.get_y(), w=img_width_mm, h=img_height_mm)
        pdf.ln(img_height_mm + 15)

    pdf.set_font("Helvetica", size=16)
    pdf.cell(0, 10, "Inhalte:", align="L", ln=True)
    for folder in config.get("input_folders", []):
        # folder structure: [folder_path, heading, thumbnail_relative]
        pdf.cell(0, 10, f"- {folder[1]}", align="L", ln=True)
    pdf.ln(10)


def chapter_page(pdf: CustomPDF, chapter: str, thumbnail: str | None):
    """Create a chapter page with a larger thumbnail."""
    pdf.add_page()
    pdf.ln(20)
    pdf.set_font("Helvetica", size=36, style="B")
    pdf.cell(0, 10, chapter, align="C", ln=True)
    pdf.ln(10)

    if thumbnail and os.path.isfile(thumbnail):
        img_width_mm, img_height_mm, processed = process_image_for_pdf(
            thumbnail, 175, 220, allow_rotation=False
        )
        pdf.image(processed, x=(210 - img_width_mm) / 2, y=pdf.get_y(), w=img_width_mm, h=img_height_mm)
        pdf.ln(img_height_mm + 15)


def compress(pdf_files: list[str], dpi: int, output_path: str):
    """
    Convert PDF pages to images, rotating landscape pages to portrait.
    """
    os.makedirs(output_path, exist_ok=True)

    for pdf_file in tqdm(pdf_files, desc="Converting PDFs to images"):
        keywords = ["DEL", "DTP"]
        if any(keyword in pdf_file for keyword in keywords):
            continue

        images = convert_from_path(pdf_file, dpi=math.ceil(dpi))

        for i, image in enumerate(images):
            if image.width > image.height:
                image = image.rotate(90, expand=True)

            base_name = os.path.splitext(os.path.basename(pdf_file))[0]
            output_filename = os.path.join(output_path, f"{base_name}_page_{i}.png")
            image.save(output_filename, "PNG")


def find_corresponding_gps_image(photo_path: str, gps_folder: str | None, mode: str = "stem_contains") -> str | None:
    """
    Find a GPS map image corresponding to photo_path in gps_folder.

    For your map exporter naming like:
      YYYYMMDD_HHMMSS__<photo_stem>__map_400x1200.png
    this finds it by token "__<stem>__map_".
    """
    if not gps_folder:
        return None
    if not os.path.isdir(gps_folder):
        return None

    stem = os.path.splitext(os.path.basename(photo_path))[0].lower()

    # collect candidates once per call (fast enough for typical sets; can be indexed if needed)
    for fn in os.listdir(gps_folder):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
            continue

        name_l = fn.lower()
        fp = os.path.join(gps_folder, fn)

        if mode == "exact":
            if os.path.splitext(fn)[0].lower() == stem:
                return fp

        # default: stem_contains
        token = f"__{stem}__map_"
        if token in name_l:
            return fp

    if mode != "exact":
        # fallback: contains stem anywhere
        for fn in os.listdir(gps_folder):
            if not fn.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            name_l = fn.lower()
            if stem in name_l:
                return os.path.join(gps_folder, fn)

    return None


def main():
    parser = argparse.ArgumentParser(description="Create a photobook from image folders (2 images per page) + optional GPS maps.")
    parser.add_argument("config", type=str, help="Path to the JSON configuration file.")
    args = parser.parse_args()

    config = load_config(args.config)

    output_folder = config["output_folder"]
    os.makedirs(output_folder, exist_ok=True)

    output_pdf = os.path.join(output_folder, "Photobook.pdf")
    resized_folder = os.path.join(output_folder, "Resized_Images")
    pdf_image_folder = os.path.join(output_folder, "PDF_Images")

    gps_folder = config.get("gps_image_folder")
    gps_match = config.get("gps_match", "stem_contains")

    pdf = CustomPDF("P", "mm", "A4")
    pdf.set_auto_page_break(auto=False)

    # Title page
    #enhanced_title_page(pdf, config)

    # Page layout: 2 rows × 2 columns per page
    # Each row: [photo | gps-map]
    margin_x = 10
    margin_y = 15
    gutter_x = 6
    gutter_y = 10

    usable_w = 210 - 2 * margin_x
    usable_h = 297 - 2 * margin_y

    left_col_w = 140
    right_col_w = usable_w - left_col_w - gutter_x

    row_h = (usable_h - gutter_y) / 2
    row_ys = [margin_y, margin_y + row_h + gutter_y]

    # Process chapters
    for folder_path, heading, thumb_rel in config["input_folders"]:
        chapter_thumb = os.path.join(folder_path, thumb_rel) if thumb_rel else None
        chapter_page(pdf, heading, chapter_thumb)

        image_files = [
            os.path.join(folder_path, file)
            for file in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, file)) and file.lower().endswith((".jpg", ".jpeg", ".png"))
        ]

        # Sort by EXIF date (fallback mtime), stable tie-break by filename
        sorted_images = sorted(image_files, key=lambda p: (get_image_sort_key(p), os.path.basename(p).lower()))

        for i in tqdm(range(0, len(sorted_images), 2), desc=f"Processing chapter '{heading}'", unit="page"):
            pdf.add_page()
            pair = sorted_images[i:i + 2]

            for row_idx, file_path in enumerate(pair):
                y_row = row_ys[row_idx]

                # LEFT: photo
                resized_path = process_image(file_path, resized_folder, 2480, 3508)
                img = Image.open(resized_path)
                img_w_mm = img.width * 25.4 / 300
                img_h_mm = img.height * 25.4 / 300

                scale_left = min(left_col_w / img_w_mm, row_h / img_h_mm)
                w_left = img_w_mm * scale_left
                h_left = img_h_mm * scale_left

                x_left = margin_x
                y_left = y_row + (row_h - h_left) / 2
                pdf.image(resized_path, x=x_left, y=y_left, w=w_left, h=h_left)

                # RIGHT: gps map (if exists)
                gps_path = find_corresponding_gps_image(file_path, gps_folder, mode=gps_match)
                if gps_path and os.path.isfile(gps_path):
                    gps_img = Image.open(gps_path)
                    gps_w_mm = gps_img.width * 25.4 / 300
                    gps_h_mm = gps_img.height * 25.4 / 300

                    # No rotation for GPS maps; they are pre-rendered (e.g., 400x1200)
                    scale_right = min(right_col_w / gps_w_mm, row_h / gps_h_mm)
                    w_right = gps_w_mm * scale_right
                    h_right = gps_h_mm * scale_right

                    x_right = margin_x + left_col_w + gutter_x
                    y_right = y_row + (row_h - h_right) / 2
                    pdf.image(gps_path, x=x_right, y=y_right, w=w_right, h=h_right)

    # Append additional PDFs as images (optional)
    if "append_pdfs" in config and config["append_pdfs"]:
        os.makedirs(pdf_image_folder, exist_ok=True)
        compress(config["append_pdfs"], dpi=600, output_path=pdf_image_folder)

        for pdf_file in tqdm(config["append_pdfs"], desc="Appending PDF images to photobook"):
            # This preserves your old behavior: append only page_0 image per PDF
            pdf.add_page()

            b, _ = os.path.splitext(pdf_file)
            p = os.path.join(pdf_image_folder, os.path.basename(b) + "_page_0.png")
            if not os.path.isfile(p):
                continue

            img = Image.open(p)
            img_width_mm = img.width * 25.4 / 300
            img_height_mm = img.height * 25.4 / 300

            max_width_mm = 180
            max_height_mm = 270
            scaling_factor = min(max_width_mm / img_width_mm, max_height_mm / img_height_mm)

            scaled_width_mm = img_width_mm * scaling_factor
            scaled_height_mm = img_height_mm * scaling_factor

            x_centered = (210 - scaled_width_mm) / 2
            y_centered = (297 - scaled_height_mm) / 2

            pdf.image(p, x=x_centered, y=y_centered, w=scaled_width_mm, h=scaled_height_mm)

    pdf.output(output_pdf)
    print(f"Final Photobook created and saved as PDF: {output_pdf}")


if __name__ == "__main__":
    main()
