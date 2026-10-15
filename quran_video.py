
import os
import json
import random
import urllib.request
import urllib.error
import subprocess
import glob
import shutil
import time

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display


# =========================
# CONFIGURATION
# =========================

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]

WIDTH = 720
HEIGHT = 1280
AYAH_COUNT = 10

WORK_DIR = "quran_video_work"
OUTPUT_VIDEO = "quran_nightly.mp4"

QURAN_API = "https://api.alquran.cloud/v1"
QURANENC_API = "https://quranenc.com/api/v1"

# QuranEnc Kurdish translation.
# The translation source is identified in the video caption.
TRANSLATION_KEY = "kurdish_salahuddin"

HEADERS = {
    "User-Agent": "QuranNightlyVideo/1.0"
}


# =========================
# NETWORK HELPERS
# =========================

def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read().decode("utf-8")

    return json.loads(raw)


def download_file(url, filename):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=90) as response:
        with open(filename, "wb") as output:
            shutil.copyfileobj(response, output)

    if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
        raise RuntimeError("فایلی بە تەواوی دابەزێنراوە نییە: " + filename)


def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg error:\n" + result.stderr[-5000:]
        )


# =========================
# ARABIC AND KURDISH FONTS
# =========================

def find_font():
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    patterns = [
        "/usr/share/fonts/**/*Noto*Naskh*Arabic*.ttf",
        "/usr/share/fonts/**/*Noto*Sans*Arabic*.ttf",
        "/usr/share/fonts/**/*DejaVuSans.ttf"
    ]

    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    raise RuntimeError(
        "فۆنتی عەرەبی نەدۆزرایەوە. "
        "پێویستە fonts-noto-core لە workflow دابمەزرێت."
    )


FONT_PATH = find_font()


def make_font(size):
    return ImageFont.truetype(FONT_PATH, size)


def shape_rtl(text):
    """
    Shape Arabic/Kurdish letters and prepare right-to-left display.
    """
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)


def wrap_rtl_text(text, font, max_width):
    """
    Wrap text by words while measuring the shaped display text.
    """
    words = str(text).split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        candidate = current + " " + word
        shown = shape_rtl(candidate)

        if font.getlength(shown) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def draw_rtl_block(draw, text, top, max_width, font_size,
                   fill, max_lines=7, line_gap=14):
    """
    Draw centered right-to-left text.
    Reduce font size when a verse is long.
    Return the bottom position of the block.
    """
    selected_font = None
    selected_lines = None

    for size in range(font_size, 23, -2):
        font = make_font(size)
        lines = wrap_rtl_text(text, font, max_width)

        if len(lines) <= max_lines:
            selected_font = font
            selected_lines = lines
            break

    if selected_font is None:
        selected_font = make_font(23)
        selected_lines = wrap_rtl_text(
            text, selected_font, max_width
        )

    y = top

    for line in selected_lines:
        displayed = shape_rtl(line)

        box = draw.textbbox(
            (0, 0),
            displayed,
            font=selected_font,
            direction="rtl"
        )

        line_height = box[3] - box[1]

        draw.text(
            (WIDTH // 2, y),
            displayed,
            font=selected_font,
            fill=fill,
            anchor="ma",
            direction="rtl",
            language="ar"
        )

        y += line_height + line_gap

    return y


# =========================
# SELECT 10 RANDOM VERSES
# =========================

def get_random_verses():
    print("Loading Quran surah list...")

    response = get_json(QURAN_API + "/surah")

    if response.get("code") != 200:
        raise RuntimeError("نەتوانرا لیستی سووڕەتەکان وەربگیرێت.")

    surahs = response["data"]

    # Ten consecutive verses must fit inside the selected surah.
    possible = [
        s for s in surahs
        if int(s["numberOfAyahs"]) >= AYAH_COUNT
    ]

    surah = random.choice(possible)
    surah_number = int(surah["number"])
    total_ayahs = int(surah["numberOfAyahs"])

    start_ayah = random.randint(
        1,
        total_ayahs - AYAH_COUNT + 1
    )

    end_ayah = start_ayah + AYAH_COUNT - 1

    print(
        "Selected surah:",
        surah_number,
        surah["name"],
        "verses:",
        start_ayah,
        "-",
        end_ayah
    )

    # Arabic Quran text, including each ayah's global number.
    surah_response = get_json(
        QURAN_API + "/surah/" + str(surah_number)
    )

    if surah_response.get("code") != 200:
        raise RuntimeError("نەتوانرا دەقی سووڕەتەکە وەربگیرێت.")

    all_ayahs = surah_response["data"]["ayahs"]

    # Fetch Kurdish translation for the selected surah.
    translation_url = (
        QURANENC_API
        + "/translation/sura/"
        + TRANSLATION_KEY
        + "/"
        + str(surah_number)
    )

    translation_response = get_json(translation_url)

    if isinstance(translation_response, list):
        translated_items = translation_response
    elif isinstance(translation_response, dict):
        translated_items = (
            translation_response.get("result")
            or translation_response.get("data")
            or translation_response.get("translations")
            or []
        )
    else:
        translated_items = []

    if not isinstance(translated_items, list) or not translated_items:
        raise RuntimeError(
            "وەرگێڕانی کوردی نەگەڕایەوە. "
            "پێویستە TRANSLATION_KEY لە QuranEnc بپشکنرێت."
        )

    translation_map = {}

    for item in translated_items:
        try:
            verse_number = int(item["aya"])
            verse_translation = str(item["translation"]).strip()
            translation_map[verse_number] = verse_translation
        except (KeyError, TypeError, ValueError):
            continue

    selected = []

    for ayah in all_ayahs:
        verse_number = int(ayah["numberInSurah"])

        if start_ayah <= verse_number <= end_ayah:
            translation = translation_map.get(verse_number, "")

            if not translation:
                raise RuntimeError(
                    "واتای کوردی بۆ ئایەتی "
                    + str(verse_number)
                    + " نەدۆزرایەوە. ڤیدیۆکە بڵاو ناکرێتەوە."
                )

            selected.append({
                "surah_number": surah_number,
                "surah_name": surah["name"],
                "ayah_number": verse_number,
                "global_number": int(ayah["number"]),
                "arabic": ayah["text"],
                "translation": translation
            })

    if len(selected) != AYAH_COUNT:
        raise RuntimeError(
            "ژمارەی ئایەتەکان ١٠ نییە؛ ڤیدیۆکە دروست ناکرێت."
        )

    return selected


# =========================
# CREATE AN IMAGE FOR EACH AYAH
# =========================

def create_ayah_image(ayah, index, image_path):
    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (12, 35, 42)
    )

    pixels = image.load()

    # Dark teal vertical gradient.
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(9 + 8 * ratio)
        g = int(35 + 20 * ratio)
        b = int(43 + 17 * ratio)

        for x in range(WIDTH):
            pixels[x, y] = (r, g, b)

    draw = ImageDraw.Draw(image)

    # Outer frame.
    draw.rounded_rectangle(
        (25, 25, WIDTH - 25, HEIGHT - 25),
        radius=28,
        outline=(197, 166, 94),
        width=3
    )

    draw.rounded_rectangle(
        (42, 42, WIDTH - 42, HEIGHT - 42),
        radius=22,
        outline=(93, 137, 133),
        width=1
    )

    title_font = make_font(30)
    small_font = make_font(23)

    # Surah title.
    draw.text(
        (WIDTH // 2, 105),
        shape_rtl(ayah["surah_name"]),
        font=title_font,
        fill=(236, 213, 155),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    draw.text(
        (WIDTH // 2, 155),
        "ئایەت "
        + str(ayah["ayah_number"])
        + "  |  "
        + str(index)
        + " / "
        + str(AYAH_COUNT),
        font=small_font,
        fill=(224, 233, 229),
        anchor="mm"
    )

    # Arabic text card.
    draw.rounded_rectangle(
        (65, 220, WIDTH - 65, 610),
        radius=24,
        fill=(20, 52, 57),
        outline=(126, 153, 122),
        width=2
    )

    draw.text(
        (WIDTH // 2, 255),
        shape_rtl("بِسْمِ اللَّهِ"),
        font=make_font(25),
        fill=(197, 166, 94),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    draw_rtl_block(
        draw=draw,
        text=ayah["arabic"],
        top=315,
        max_width=WIDTH - 155,
        font_size=46,
        fill=(255, 250, 231),
        max_lines=7,
        line_gap=15
    )

    # Kurdish meaning card.
    draw.rounded_rectangle(
        (65, 650, WIDTH - 65, 1000),
        radius=24,
        fill=(17, 45, 51),
        outline=(126, 153, 122),
        width=2
    )

    draw.text(
        (WIDTH // 2, 690),
        shape_rtl("واتای کوردی"),
        font=make_font(30),
        fill=(236, 213, 155),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    draw_rtl_block(
        draw=draw,
        text=ayah["translation"],
        top=750,
        max_width=WIDTH - 155,
        font_size=34,
        fill=(245, 247, 241),
        max_lines=7,
        line_gap=14
    )

    # Source credit.
    draw.line(
        (100, 1050, WIDTH - 100, 1050),
        fill=(126, 153, 122),
        width=2
    )

    draw.text(
        (WIDTH // 2, 1090),
        "QuranEnc.com - وەرگێڕانی واتا",
        font=make_font(20),
        fill=(218, 220, 204),
        anchor="mm"
    )

    draw.text(
        (WIDTH // 2, 1150),
        "قورئانی پیرۆز",
        font=make_font(25),
        fill=(236, 213, 155),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    image.save(image_path, quality=95)


# =========================
# DOWNLOAD RECITATION AUDIO
# =========================

def download_ayah_audio(ayah, audio_path):
    global_number = ayah["global_number"]

    response = get_json(
        QURAN_API
        + "/ayah/"
        + str(global_number)
        + "/ar.alafasy"
    )

    if response.get("code") != 200:
        raise RuntimeError(
            "نەتوانرا دەنگی ئایەتی "
            + str(ayah["ayah_number"])
            + " وەربگیرێت."
        )

    audio_url = response["data"].get("audio")

    if not audio_url:
        raise RuntimeError("بەستەری دەنگی ئایەت نەدۆزرایەوە.")

    download_file(audio_url, audio_path)


# =========================
# BUILD THE VIDEO
# =========================

def create_video():
    os.makedirs(WORK_DIR, exist_ok=True)

    selected_ayahs = get_random_verses()

    clip_files = []

    for index, ayah in enumerate(selected_ayahs, start=1):
        print(
            "Preparing ayah",
            index,
            "of",
            AYAH_COUNT
        )

        image_path = os.path.join(
            WORK_DIR,
            "ayah_" + str(index) + ".png"
        )

        audio_path = os.path.join(
            WORK_DIR,
            "ayah_" + str(index) + ".mp3"
        )

        clip_path = os.path.join(
            WORK_DIR,
            "clip_" + str(index) + ".mp4"
        )

        create_ayah_image(
            ayah,
            index,
            image_path
        )

        download_ayah_audio(
            ayah,
            audio_path
        )

        # Keep each verse on screen while its own recitation plays.
        run_command([
            "ffmpeg",
            "-y",
            "-loop", "1",
            "-framerate", "24",
            "-i", image_path,
            "-i", audio_path,
            "-vf", "scale=720:1280,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "27",
            "-tune", "stillimage",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            clip_path
        ])

        clip_files.append(clip_path)

    # Create a concat list for FFmpeg.
    concat_path = os.path.join(WORK_DIR, "clips.txt")

    with open(concat_path, "w", encoding="utf-8") as file:
        for clip_path in clip_files:
            safe_path = os.path.abspath(clip_path).replace("'", "'\\''")
            file.write("file '" + safe_path + "'\n")

    # Re-encode the joined clips to ensure consistent video/audio streams.
    run_command([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        OUTPUT_VIDEO
    ])

    if not os.path.exists(OUTPUT_VIDEO):
        raise RuntimeError("ڤیدیۆکە دروست نەبوو.")

    if os.path.getsize(OUTPUT_VIDEO) < 10000:
        raise RuntimeError("قەبارەی ڤیدیۆکە زۆر بچووکە.")

    # Save the selected verses for the Telegram caption.
    with open(
        os.path.join(WORK_DIR, "selected_ayahs.json"),
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            selected_ayahs,
            file,
            ensure_ascii=False,
            indent=2
        )

    return selected_ayahs


# =========================
# SEND VIDEO TO TELEGRAM
# =========================

def send_video(ayahs):
    surah_name = ayahs[0]["surah_name"]
    start = ayahs[0]["ayah_number"]
    end = ayahs[-1]["ayah_number"]

    caption = (
        "📖 " + surah_name + "\n"
        + "ئایەتەکان: " + str(start) + "–" + str(end) + "\n"
        + "🤍 ١٠ ئایەت لەگەڵ دەنگ و واتای کوردی\n"
        + "سەرچاوەی وەرگێڕانی واتا: QuranEnc.com\n"
        + "https://quranenc.com"
    )

    boundary = "----QuranVideoBoundary7MA4YWxkTrZu0gW"

    def field_part(name, value):
        return (
            ("--" + boundary + "\r\n").encode("utf-8")
            + (
                'Content-Disposition: form-data; name="'
                + name
                + '"\r\n\r\n'
            ).encode("utf-8")
            + value.encode("utf-8")
            + b"\r\n"
        )

    body = b""
    body += field_part("chat_id", CHANNEL)
    body += field_part("caption", caption)
    body += field_part("supports_streaming", "true")

    body += (
        ("--" + boundary + "\r\n").encode("utf-8")
        + (
            'Content-Disposition: form-data; name="video"; '
            'filename="quran_nightly.mp4"\r\n'
        ).encode("utf-8")
        + b"Content-Type: video/mp4\r\n\r\n"
    )

    with open(OUTPUT_VIDEO, "rb") as file:
        body += file.read()

    body += ("\r\n--" + boundary + "--\r\n").encode("utf-8")

    request = urllib.request.Request(
        "https://api.telegram.org/bot"
        + TOKEN
        + "/sendVideo",
        data=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "QuranNightlyVideo/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as error:
        details = error.read().decode(
            "utf-8",
            errors="replace"
        )
        raise RuntimeError(
            "Telegram video upload failed: " + details
        )

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram نەیتوانی ڤیدیۆکە بنێرێت: "
            + str(result)
        )

    print("Quran video sent successfully.")


# =========================
# MAIN
# =========================

def main():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg دامەزراو نییە.")

    print("Starting nightly Quran video...")

    selected_ayahs = create_video()

    print("Video created. Uploading to Telegram...")

    send_video(selected_ayahs)

    print("Finished successfully.")


if __name__ == "__main__":
    main()
