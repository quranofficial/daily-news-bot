
import os
import json
import urllib.request
import subprocess

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

# ==============================
# ڕێکخستن
# ==============================

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]

WIDTH, HEIGHT = 720, 1280

IMAGE_FILE = "quran_background.png"
AUDIO_FILE = "ikhlas.mp3"
OUTPUT_FILE = "quran_ikhlas.mp4"

AUDIO_URL = (
    "https://cdn.islamic.network/quran/audio-surah/"
    "128/ar.alafasy/112.mp3"
)

# ==============================
# نووسینی عەرەبی
# ==============================

def arabic(text):
    return get_display(arabic_reshaper.reshape(text))


def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/noto/"
        "NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans.ttf",
    ]

    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    raise RuntimeError(
        "فۆنتی عەرەبی نەدۆزرایەوە."
    )


def draw_center(draw, text, y, font, fill):
    draw.text(
        (WIDTH // 2, y),
        arabic(text),
        font=font,
        fill=fill,
        anchor="mm",
    )


# ==============================
# دروستکردنی وێنەی ڤیدیۆ
# ==============================

def make_image():
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)

    # پشتەوەی شینی تاریک
    for y in range(HEIGHT):
        ratio = y / HEIGHT

        color = (
            int(7 + 8 * ratio),
            int(24 + 22 * ratio),
            int(39 + 32 * ratio),
        )

        draw.line(
            (0, y, WIDTH, y),
            fill=color,
        )

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
        draw,
        "بە ناوی خوای بەخشندە و میهرەبان",
        105,
        get_font(27),
        gold,
    )

    draw_center(
        draw,
        "سورەتی الإخلاص",
        205,
        get_font(46),
        gold,
    )

    draw.line(
        (100, 255, WIDTH - 100, 255),
        fill=gold,
        width=2,
    )

    verses = [
        "قُلْ هُوَ اللَّهُ أَحَدٌ",
        "اللَّهُ الصَّمَدُ",
        "لَمْ يَلِدْ وَلَمْ يُولَدْ",
        "وَلَمْ يَكُن لَّهُ كُفُوًا أَحَدٌ",
    ]

    y = 335

    for verse in verses:
        draw_center(
            draw,
            verse,
            y,
            get_font(35),
            white,
        )
        y += 88

    draw.line(
        (100, 710, WIDTH - 100, 710),
        fill=gold,
        width=2,
    )

    draw_center(
        draw,
        "واتای کوردی",
        765,
        get_font(34),
        green,
    )

    translations = [
        "بڵێ: ئەو خوایە تاکە.",
        "خوا بێ‌پێویستە و هەموو پێویستیان پێیەتی.",
        "نەیبووە و نەزاوە.",
        "و هیچ کەس و شتێک هاوتای ئەو نییە.",
    ]

    y = 825

    for line in translations:
        draw_center(
            draw,
            line,
            y,
            get_font(23),
            white,
        )
        y += 62

    draw_center(
        draw,
        "قورئانی پیرۆز",
        1130,
        get_font(28),
        gold,
    )

    image.save(IMAGE_FILE)

    print("وێنەی ڤیدیۆ دروست کرا.")


# ==============================
# دابەزاندنی دەنگی قورئان
# ==============================

def download_audio():
    request = urllib.request.Request(
        AUDIO_URL,
        headers={
            "User-Agent": "QuranVideoBot/1.0"
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=90,
    ) as response:

        audio_data = response.read()

    if len(audio_data) < 10000:
        raise RuntimeError(
            "فایلی دەنگ بە دروستی دابەزێنراوە نییە."
        )

    with open(AUDIO_FILE, "wb") as file:
        file.write(audio_data)

    print("دەنگی قورئان دابەزێنرا.")


# ==============================
# دروستکردنی MP4
# ==============================

def create_video():
    make_image()
    download_audio()

    subprocess.run(
        [
            "ffmpeg",
            "-y",
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
        ],
        check=True,
    )

    if not os.path.exists(OUTPUT_FILE):
        raise RuntimeError(
            "فایلی ڤیدیۆ دروست نەکرا."
        )

    if os.path.getsize(OUTPUT_FILE) < 10000:
        raise RuntimeError(
            "قەبارەی ڤیدیۆکە نائاساییە."
        )

    print("ڤیدیۆ ئامادەیە:", OUTPUT_FILE)


# ==============================
# ناردنی ڤیدیۆ بۆ Telegram
# ==============================

def send_video():
    boundary = "----QuranVideoUploadBoundary"

    parts = []

    def add_field(name, value):
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; '
                f'name="{name}"\r\n'
                "\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )

    add_field("chat_id", CHANNEL)

    add_field(
        "caption",
        "📖 سووڕەتی الإخلاص\n"
        "🤍 لەگەڵ نووسینی ئایەتەکان و واتای کوردی",
    )

    add_field("supports_streaming", "true")

    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; '
            f'name="video"; filename="{OUTPUT_FILE}"\r\n'
            "Content-Type: video/mp4\r\n"
            "\r\n"
        ).encode("utf-8")
    )

    with open(OUTPUT_FILE, "rb") as file:
        parts.append(file.read())

    parts.append(
        f"\r\n--{boundary}--\r\n".encode("utf-8")
    )

    payload = b"".join(parts)

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendVideo",
        data=payload,
        headers={
            "Content-Type": (
                f"multipart/form-data; boundary={boundary}"
            )
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=180,
    ) as response:

        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram ناردنی ڤیدیۆی ڕەت کردەوە."
        )

    print("ڤیدیۆ بە سەرکەوتوویی نێردرا.")


# ==============================
# دەستپێکردن
# ==============================

if __name__ == "__main__":
    create_video()
    send_video()
