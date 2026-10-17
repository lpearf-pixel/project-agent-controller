"""Create the deterministic M4 single-update fixture for hwType 0x01."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 296
HEIGHT = 128
OUTPUT = Path("/tmp/oepl-m4-296x128.jpg")


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), outline=(0, 0, 0), width=4)
    draw.rectangle((8, 8, WIDTH - 9, 34), fill=(255, 0, 0))
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_large = ImageFont.truetype(font_path, 25)
    font_small = ImageFont.truetype(font_path, 18)
    draw.text((18, 10), "OEPL TEST", font=font_large, fill=(255, 255, 255))
    draw.text((18, 53), "296 x 128", font=font_small, fill=(0, 0, 0))
    draw.rectangle((190, 52, 276, 112), fill=(0, 0, 0))
    draw.rectangle((202, 64, 264, 100), fill=(255, 0, 0))
    image.save(OUTPUT, format="JPEG", quality=95, subsampling=0, optimize=False)


if __name__ == "__main__":
    main()
