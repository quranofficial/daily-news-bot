
import os
import urllib.request
import subprocess
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

WIDTH, HEIGHT = 720, 1280
IMAGE_FILE = "quran_background.png"
AUDIO_FILE = "ikhlas.mp3"
OUTPUT_FILE = "quran_ikhlas.mp4"

AUDIO_URL = (
    "https://cdn.islamic.network/quran/audio-surah/"
    "128/ar.alafasy/112.mp3"
)

def arabic(text):
    return get_display(arabic_reshaper.reshape(text))

def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def draw_center(draw, text, y, font, fill):
    draw.text(
        (WIDTH // 2, y),
        arabic(text),
        font=font,
        fill=fill,
        anchor="mm",
        stroke_width=0,
    )

def make_image():
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = (
            int(7 + 8 * ratio),
            int(24 + 22 * ratio),
            int(39 + 32 * ratio),
        )
        for x in range(WIDTH):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)

    gold = (224, 190, 112)
    white = (250, 247, 235)
    green = (151, 208, 177)

    draw.rounded_rectangle(
        (28, 28, WIDTH - 28, HEIGHT - 28),
        radius=28,
        outline=gold,
        width=3,
    )

    draw_center(
        draw, "بە ناوی خوای بەخشندە و میهرەبان",
        105, get_font(27), gold
    )

    draw_center(
        draw, "سورەتی الإخلاص",
        205, get_font(46), gold
    )

    draw.line((100, 255, WIDTH - 100, 255), fill=gold, width=2)

    verses = [
        "قُلْ هُوَ اللَّهُ أَحَدٌ",
        "اللَّهُ الصَّمَدُ",
        "لَمْ يَلِدْ وَلَمْ يُولَدْ",
        "وَلَمْ يَكُن لَّهُ كُفُوًا أَحَدٌ",
    ]

    y = 335
    for verse in verses:
        draw_center(draw, verse, y, get_font(35), white)
        y += 88

    draw.line((100, 710, WIDTH - 100, 710), fill=gold, width=2)

    draw_center(
        draw, "واتای کوردی",
        765, get_font(34), green
    )

    translations = [
        "بڵێ: ئەو خوایە تاکە.",
        "خوا بێ‌پێویستە و هەموو پێویستیان پێیەتی.",
        "نەیبووە و نەزاوە.",
        "و هیچ کەس و شتێک هاوتای ئەو نییە.",
    ]

    y = 825
    for line in translations:
        draw_center(draw, line, y, get_font(23), white)
        y += 62

    draw_center(
        draw, "قورئانی پیرۆز",
        1130, get_font(28), gold
    )

    image.save(IMAGE_FILE)

def download_audio():
    request = urllib.request.Request(
        AUDIO_URL,
        headers={"User-Agent": "QuranVideoBot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        with open(AUDIO_FILE, "wb") as file:
            file.write(response.read())

    if os.path.getsize(AUDIO_FILE) < 10000:
        raise RuntimeError("فایلی دەنگ بە دروستی دابەزێنراوە نییە.")

def create_video():
    make_image()
    download_audio()

    subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "25",
        "-i", IMAGE_FILE,
        "-i", AUDIO_FILE,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        OUTPUT_FILE,
    ], check=True)

    print("ڤیدیۆ دروست کرا:", OUTPUT_FILE)

if __name__ == "__main__":
    create_video()
