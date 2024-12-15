from PIL import Image, ImageDraw, ImageFont
import os


# Maximum desired file size in bytes (900KB)
MAX_IMAGE_SIZE = 900 * 1024

def convert_image_to_jpg(logger,png_path):
    if png_path and os.path.exists(png_path):
        jpg_path = png_path.replace(".png", ".jpg")
        try:
            with Image.open(png_path) as img:
                rgb_img = img.convert('RGB')
                rgb_img.save(jpg_path, 'JPEG', quality=90)
            logger.info(f"Converted image {png_path} to {jpg_path}")
            return jpg_path
        except Exception as e:
            logger.error(f"Failed to convert image {png_path} to JPEG: {e}")
            return None
    return None


def ensure_image_size_under_limit(logger,jpg_path, max_size=MAX_IMAGE_SIZE):
    if not jpg_path or not os.path.exists(jpg_path):
        return None

    while True:
        size_bytes = os.path.getsize(jpg_path)
        if size_bytes <= max_size:
            break
        # Need to resize
        try:
            with Image.open(jpg_path) as img:
                width, height = img.size
                new_width = int(width * 0.9)
                new_height = int(height * 0.9)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                img.save(jpg_path, 'JPEG', quality=90)
            logger.info(f"Resized image to {new_width}x{new_height} due to size {size_bytes} > {max_size}")
        except Exception as e:
            logger.error(f"Error resizing image: {e}")
            return None

    return jpg_path



def convert_image_to_jpg(logger,png_path):
    if png_path and os.path.exists(png_path):
        jpg_path = png_path.replace(".png", ".jpg")
        try:
            with Image.open(png_path) as img:
                rgb_img = img.convert('RGB')
                rgb_img.save(jpg_path, 'JPEG', quality=90)
            logger.info(f"Converted image {png_path} to {jpg_path}")
            return jpg_path
        except Exception as e:
            logger.error(f"Failed to convert image {png_path} to JPEG: {e}")
            return None
    return None

def generate_text(results):
    cal = results.get("calibration", {})
    ra = cal.get("ra", 0.0)
    dec = cal.get("dec", 0.0)
    pixscale = cal.get("pixscale", 0.0)

    ra_str = f"{ra:.2f}"
    dec_str = f"{dec:.2f}"
    pix_str = f"{pixscale:.2f}"

    objects_in_field = results.get("objects_in_field", {}).get("objects_in_field", [])
    objects_text = ", ".join(objects_in_field) if objects_in_field else "No objects found"

    reply_text = f"Astrometry:\nRA: {ra_str} °\nDec: {dec_str} °\nResolution: {pix_str} arcsec/pix\nObjects: {objects_text}"
    reply_alt_text=reply_text
    max_length = 300
    if len(reply_text) > max_length:
        truncated_text = reply_text[:max_length - len("\nTRUNCATED - See ALT-TEXT") - 1]
        reply_text = truncated_text.rstrip() + "\nTRUNCATED - See ALT-TEXT"

    max_length = 2000
    if len(reply_text) > max_length:
        truncated_alt_text = reply_text[:max_length - len("\nTRUNCATED") - 1]
        reply_alt_text = truncated_alt_text.rstrip() + "\nTRUNCATED"
    return reply_text,reply_alt_text


def create_table_image(logger,results, max_size=MAX_IMAGE_SIZE):
    cal = results.get("calibration", {})
    ra = cal.get("ra", 0.0)
    dec = cal.get("dec", 0.0)
    pixscale = cal.get("pixscale", 0.0)

    objects_in_field = results.get("objects_in_field", {}).get("objects_in_field", [])
    max_objects = 25
    displayed_objects = objects_in_field[:max_objects]
    truncated = (len(objects_in_field) > max_objects)
    if truncated:
        displayed_objects.append("... (truncated)")

    lines = []
    lines.append("Astrometry Results")
    lines.append(f"RA: {ra:.2f}°   Dec: {dec:.2f}°   Resolution: {pixscale:.2f}\"/pix")
    lines.append("")
    lines.append("Objects in Field:")
    for obj in displayed_objects:
        lines.append(f" - {obj}")
    if truncated:
        lines.append(f"Total Objects: {len(objects_in_field)}")

    font_path = "arial.ttf"
    if not os.path.exists(font_path):
        font = ImageFont.load_default()
    else:
        font = ImageFont.truetype(font_path, 24)

    draw_dummy = ImageDraw.Draw(Image.new("RGB", (1,1)))
    max_width = 0
    total_height = 0
    for line in lines:
        bbox = draw_dummy.textbbox((0,0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w > max_width:
            max_width = w
        total_height += h + 10

    padding = 50
    img_width = max_width + padding*2
    img_height = total_height + padding*2

    img = Image.new("RGB", (img_width, img_height), "white")
    draw = ImageDraw.Draw(img)

    y = padding
    for line in lines:
        bbox = draw.textbbox((0,0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        
        # Center the title line
        if line == "Astrometry Results":
            x = (img_width - w)//2
        else:
            x = padding

        draw.text((x, y), line, fill="black", font=font)
        y += h + 10

    table_png = "results/table_data.png"
    img.save(table_png, "PNG")

    table_jpg = convert_image_to_jpg(logger,table_png)
    if table_jpg:
        table_jpg = ensure_image_size_under_limit(logger,table_jpg, MAX_IMAGE_SIZE)
    return table_jpg