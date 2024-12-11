import os
import json
import argparse
from datetime import datetime
from fpdf import FPDF
from PIL import Image, ExifTags
from tqdm import tqdm
from pdf2image import convert_from_path
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
import glob
import math


def load_config(config_file):
    """Load configuration from a JSON file."""
    with open(config_file, "r", encoding='utf8') as file:
        return json.load(file)


def get_exif_date_taken(file_path):
    """Extract the EXIF 'Date Taken' metadata."""
    try:
        img = Image.open(file_path)
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                decoded_tag = ExifTags.TAGS.get(tag)
                if decoded_tag == "DateTimeOriginal":
                    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        print(f"Error reading EXIF data from {file_path}: {e}")
    return None


def get_image_sort_key(file_path):
    """Sort images by EXIF date, modified date, or filename."""
    exif_date = get_exif_date_taken(file_path)
    if exif_date:
        return exif_date

    try:
        modified_date = os.path.getmtime(file_path)
        return datetime.fromtimestamp(modified_date)
    except Exception as e:
        print(f"Error getting modified date for {file_path}: {e}")

    return os.path.basename(file_path).lower()


def process_image(file_path, resized_folder, max_width, max_height):
    """Resize, rotate, and upscale an image."""
    img = Image.open(file_path)

    if img.height > img.width:
        img = img.rotate(90, expand=True)

    img_width, img_height = img.size

    if img_width < max_width or img_height < max_height:
        scaling_factor = max(max_width / img_width, max_height / img_height)
        img = img.resize(
            (int(img_width * scaling_factor), int(img_height * scaling_factor)), Image.LANCZOS
        )

    img.thumbnail((max_width, max_height), Image.LANCZOS)

    resized_path = os.path.join(resized_folder, os.path.basename(file_path))
    img.save(resized_path, quality=85)
    return resized_path


def enhanced_title_page(pdf, config):
    """Create an enhanced title page."""
    pdf.add_page()
    pdf.set_font("Arial", size=36, style="B")
    pdf.cell(0, 30, config["title"], align="C", ln=True)
    pdf.ln(10)

    # Add thumbnail if provided
    if "thumbnail" in config and os.path.isfile(config["thumbnail"]):
        img_width_mm, img_height_mm, _ = process_image_for_pdf(config["thumbnail"], 150, 150, allow_rotation=False)
        pdf.image(config["thumbnail"], x=(210 - img_width_mm) / 2, y=pdf.get_y(), w=img_width_mm, h=img_height_mm)
        pdf.ln(img_height_mm + 15)
    else:
        print(f'skipping title thumbnail {config["thumbnail"]}')

    pdf.set_font("Arial", size=16)
    pdf.cell(0, 10, "Inhalte:", align="L", ln=True)
    for folder in config["input_folders"]:
        pdf.cell(0, 10, f"- {folder[1]}", align="L", ln=True)
    pdf.ln(10)


def chapter_page(pdf, chapter, thumbnail):
    """Create a chapter page with a larger thumbnail."""
    pdf.add_page()
    pdf.ln(20)
    pdf.set_font("Arial", size=36, style="B")
    pdf.cell(0, 10, chapter, align="C", ln=True)
    pdf.ln(10)

    # Add chapter thumbnail
    if thumbnail and os.path.isfile(thumbnail):
        img_width_mm, img_height_mm, processed_thumbnail = process_image_for_pdf(
            thumbnail, 175, 220, allow_rotation=False
        )
        pdf.image(processed_thumbnail, x=(210 - img_width_mm) / 2, y=pdf.get_y(), w=img_width_mm, h=img_height_mm)
        pdf.ln(img_height_mm + 15)
    else:
        print(f'skipping chapter thumbnail {thumbnail}')


def process_image_for_pdf(image_path, max_width_mm, max_height_mm, allow_rotation=True):
    """
    Resize an image for display in a PDF while maintaining aspect ratio.
    Optionally allow rotation for landscape images.

    Parameters:
        image_path (str): Path to the image file.
        max_width_mm (float): Maximum width in millimeters.
        max_height_mm (float): Maximum height in millimeters.
        allow_rotation (bool): If True, rotate landscape images to portrait orientation.

    Returns:
        tuple: Scaled width and height in millimeters, and the processed image path.
    """
    img = Image.open(image_path)
    dpi = 300  # Default DPI for PDF scaling (pixels per inch)

    # Rotate the image only if rotation is allowed and the image is in landscape orientation
    if allow_rotation and img.width > img.height:
        img = img.rotate(-90, expand=True)

    # Convert dimensions from pixels to millimeters
    img_width_mm = img.width * 25.4 / dpi
    img_height_mm = img.height * 25.4 / dpi

    # Calculate the scaling factor to fit the image within the maximum dimensions
    scaling_factor = min(max_width_mm / img_width_mm, max_height_mm / img_height_mm)

    # Scale the image dimensions proportionally
    img_width_mm *= scaling_factor
    img_height_mm *= scaling_factor

    # Save the rotated or unmodified image to a temporary file
    processed_image_path = f"{os.path.splitext(image_path)[0]}_processed.jpg"
    img.save(processed_image_path, "JPEG")

    return img_width_mm, img_height_mm, processed_image_path


class CustomPDF(FPDF):
    """Custom PDF class to add page numbering."""
    def header(self):
        pass

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", size=10)
        self.cell(0, 10, f"- {self.page_no()} -", align="C")


def compress(pdf_files, dpi, output_path):
    """
    Convert PDF pages to images, rotating landscape pages to portrait.

    Parameters:
        pdf_files (list): List of PDF file paths to process.
        dpi (int): DPI for image conversion.
        output_path (str): Path to save the output images.
    """
    os.makedirs(output_path, exist_ok=True)

    for pdf_file in tqdm(pdf_files, desc="Converting PDFs to images"):
        keywords = ['DEL', 'DTP']
        if any(keyword in pdf_file for keyword in keywords):
            continue

        # Load the PDF and convert to images
        images = convert_from_path(pdf_file, dpi=math.ceil(dpi))
                
        for i, image in enumerate(images):
            # Rotate landscape images to portrait if needed
            if image.width > image.height:
                image = image.rotate(90, expand=True)

            # Save the image with an incremental name, include the original PDF name
            base_name = os.path.splitext(os.path.basename(pdf_file))[0]
            output_filename = os.path.join(output_path, f"{base_name}_page_{i}.png")
            image.save(output_filename, 'PNG')


def main():
    parser = argparse.ArgumentParser(description="Create a photobook from image folders.")
    parser.add_argument("config", type=str, help="Path to the JSON configuration file.")
    args = parser.parse_args()

    config = load_config(args.config)

    output_pdf = os.path.join(config["output_folder"], "Photobook.pdf")
    resized_folder = os.path.join(config["output_folder"], "Resized_Images")
    pdf_image_folder = os.path.join(config["output_folder"], "PDF_Images")

    os.makedirs(resized_folder, exist_ok=True)

    pdf = CustomPDF("P", "mm", "A4")
    pdf.set_auto_page_break(auto=False)

    # Enhanced title page
    enhanced_title_page(pdf, config)

    # Process chapters
    for folder_path, heading, thumbnail in config["input_folders"]:
        chapter_page(pdf, heading, os.path.join(folder_path, thumbnail))

        image_files = [
            os.path.join(folder_path, file)
            for file in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, file)) and file.lower().endswith(('.jpg', '.jpeg', '.png'))
        ]
        sorted_images = sorted(image_files, key=get_image_sort_key)

        for i in tqdm(range(0, len(sorted_images), 2), desc=f"Processing chapter '{heading}'", unit="page"):
            pdf.add_page()
            positions = [(15, 15), (15, 150)]
            for j, file_path in enumerate(sorted_images[i:i + 2]):
                resized_path = process_image(file_path, resized_folder, 2480, 3508)
                img = Image.open(resized_path)
                img_width_mm = img.width * 25.4 / 300
                img_height_mm = img.height * 25.4 / 300

                scaling_factor = min(180 / img_width_mm, 130 / img_height_mm)
                img_width_mm *= scaling_factor
                img_height_mm *= scaling_factor

                x, y = positions[j]
                pdf.image(resized_path, x=x, y=y, w=img_width_mm, h=img_height_mm)

    # Compress and append additional PDFs as images
    if "append_pdfs" in config:
        compress(config["append_pdfs"], dpi=600, output_path=pdf_image_folder)

        for image_file in tqdm(config['append_pdfs'], desc="Appending PDF images to photobook"):
            pdf.add_page()
            
            b, ext = os.path.splitext(image_file)
            p = os.path.join(pdf_image_folder, os.path.basename(b) + '_page_0.png')
            img = Image.open(p)

            # Convert image dimensions from pixels to millimeters (assuming 300 DPI)
            img_width_mm = img.width * 25.4 / 300
            img_height_mm = img.height * 25.4 / 300

            # Calculate scaling factor to fit image within the A4 page size (210mm x 297mm) with margins
            max_width_mm = 180  # Max width with margins
            max_height_mm = 270  # Max height with margins
            scaling_factor = min(max_width_mm / img_width_mm, max_height_mm / img_height_mm)

            # Scale dimensions proportionally
            scaled_width_mm = img_width_mm * scaling_factor
            scaled_height_mm = img_height_mm * scaling_factor

            # Center the image on the page
            x_centered = (210 - scaled_width_mm) / 2
            y_centered = (297 - scaled_height_mm) / 2

            # Add the image to the PDF
            pdf.image(p, x=x_centered, y=y_centered, w=scaled_width_mm, h=scaled_height_mm)

    pdf.output(output_pdf)
    print(f"Final Photobook created and saved as PDF: {output_pdf}")


if __name__ == '__main__':
    main()
