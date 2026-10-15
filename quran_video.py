import os
import json
import random
import urllib.request
import urllib.error
import subprocess
import glob
import shutil

from PIL import Image, ImageDraw, ImageFont

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
TRANSLATION_KEY = "kurdish_salahuddin"
HEADERS = {"User-Agent": "QuranNightlyVideo/1.1"}

# =========================
# NETWORK / COMMAND HELPERS
# =========================
def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url, filename):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=90) as response:
        with open(filename, "wb") as output:
            shutil.copyfileobj(response, output)
    if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
        raise RuntimeError("دەنگ بە تەواوی دابەزێنراو نییە: " + filename)


def run_command(command):
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        raise RuntimeError("FFmpeg error:\n" + result.stderr[-5000:])

# =========================
# FONT AND RIGHT-TO-LEFT TEXT
# =========================
def find_font():
    # Noto Sans Arabic generally has broader Arabic-script coverage, including
    # Kurdish letters. Fall back to Noto Naskh and DejaVu if needed.
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    patterns = [
        "/usr/share/fonts/**/*Noto*Naskh*Arabic*.ttf",
        "/usr/share/fonts/**/*Noto*Sans*Arabic*.ttf",
        "/usr/share/fonts/**/*DejaVuSans.ttf",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
    raise RuntimeError(
        "فۆنتی عەرەبی نەدۆزرایەوە؛ لە workflow ـدا fonts-noto-core دابمەزرێنە."
    )


FONT_PATH = find_font()
LATIN_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
if not os.path.isfile(LATIN_FONT_PATH):
    LATIN_FONT_PATH = FONT_PATH


def make_font(size):
    # RAQM performs Arabic shaping and right-to-left layout inside Pillow.
    # This avoids shaping/reordering the same text twice.
    try:
        return ImageFont.truetype(FONT_PATH, size, layout_engine=ImageFont.Layout.RAQM)
    except (AttributeError, ValueError):
        return ImageFont.truetype(FONT_PATH, size)


def make_latin_font(size):
    return ImageFont.truetype(LATIN_FONT_PATH, size)


def shape_rtl(text):
    """Keep original Unicode text; Pillow/RAQM shapes and orders it when drawn."""
    return str(text)


def wrap_rtl_text(text, font, max_width):
    words = str(text).split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        try:
            candidate_width = font.getlength(candidate, direction="rtl", language="ar")
        except (TypeError, ValueError):
            candidate_width = font.getlength(candidate)
        if candidate_width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_rtl_block(draw, text, top, max_width, font_size, fill,
                   max_lines=7, line_gap=14):
    selected_font = None
    selected_lines = None
    for size in range(font_size, 22, -2):
        font = make_font(size)
        lines = wrap_rtl_text(text, font, max_width)
        if len(lines) <= max_lines:
            selected_font = font
            selected_lines = lines
            break
    if selected_font is None:
        selected_font = make_font(22)
        selected_lines = wrap_rtl_text(text, selected_font, max_width)

    y = top
    for line in selected_lines:
        displayed = shape_rtl(line)
        try:
            box = draw.textbbox(
                (0, 0), displayed, font=selected_font,
                direction="rtl", language="ar"
            )
        except (TypeError, ValueError):
            box = draw.textbbox((0, 0), displayed, font=selected_font)
        line_height = box[3] - box[1]
        try:
            draw.text(
                (WIDTH // 2, y), displayed, font=selected_font,
                fill=fill, anchor="ma", direction="rtl", language="ar"
            )
        except (TypeError, ValueError):
            # Fallback for Pillow builds without RAQM support.
            draw.text((WIDTH // 2, y), displayed, font=selected_font,
                      fill=fill, anchor="ma")
        y += line_height + line_gap
    return y

# =========================
# SELECT 10 RANDOM CONSECUTIVE VERSES
# =========================
def get_random_verses():
    print("Loading Quran surah list...")
    response = get_json(QURAN_API + "/surah")
    if response.get("code") != 200:
        raise RuntimeError("نەتوانرا لیستی سووڕەتەکان وەربگیرێت.")

    possible = [s for s in response["data"]
                if int(s["numberOfAyahs"]) >= AYAH_COUNT]
    if not possible:
        raise RuntimeError("هیچ سووڕەتێک بۆ ١٠ ئایەتی بەردەوام نەدۆزرایەوە.")

    surah = random.choice(possible)
    surah_number = int(surah["number"])
    total_ayahs = int(surah["numberOfAyahs"])
    start_ayah = random.randint(1, total_ayahs - AYAH_COUNT + 1)
    end_ayah = start_ayah + AYAH_COUNT - 1
    print("Selected:", surah["name"], "verses", start_ayah, "-", end_ayah)

    surah_response = get_json(QURAN_API + "/surah/" + str(surah_number))
    if surah_response.get("code") != 200:
        raise RuntimeError("نەتوانرا دەقی سووڕەتەکە وەربگیرێت.")
    all_ayahs = surah_response["data"]["ayahs"]

    translation_url = (
        QURANENC_API + "/translation/sura/" + TRANSLATION_KEY + "/" + str(surah_number)
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
        if isinstance(translated_items, dict):
            translated_items = translated_items.get("result", [])
    else:
        translated_items = []

    if not isinstance(translated_items, list) or not translated_items:
        raise RuntimeError(
            "وەرگێڕانی کوردی نەگەڕایەوە؛ ناوی وەرگێڕان یان وەڵامی QuranEnc بپشکنە."
        )

    translation_map = {}
    for item in translated_items:
        if not isinstance(item, dict):
            continue
        try:
            verse_number = int(item.get("aya", item.get("aya_number")))
            translation = str(
                item.get("translation", item.get("text", item.get("translation_text", "")))
            ).strip()
            if translation:
                translation_map[verse_number] = translation
        except (TypeError, ValueError):
            continue

    selected = []
    for ayah in all_ayahs:
        verse_number = int(ayah["numberInSurah"])
        if start_ayah <= verse_number <= end_ayah:
            translation = translation_map.get(verse_number, "")
            if not translation:
                raise RuntimeError(
                    "واتای کوردی بۆ ئایەتی " + str(verse_number) +
                    " نەدۆزرایەوە؛ بۆ پاراستنی دروستی، ڤیدیۆکە بڵاو ناکرێتەوە."
                )
            selected.append({
                "surah_number": surah_number,
                "surah_name": surah["name"],
                "ayah_number": verse_number,
                "global_number": int(ayah["number"]),
                "arabic": ayah["text"],
                "translation": translation,
            })

    if len(selected) != AYAH_COUNT:
        raise RuntimeError("ژمارەی ئایەتەکان ١٠ نییە؛ ڤیدیۆکە دروست ناکرێت.")
    return selected

# =========================
# CREATE AN IMAGE FOR EACH VERSE
# =========================
def create_ayah_image(ayah, index, image_path):
    image = Image.new("RGB", (WIDTH, HEIGHT), (12, 35, 42))
    pixels = image.load()
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        color = (int(9 + 8 * ratio), int(35 + 20 * ratio), int(43 + 17 * ratio))
        for x in range(WIDTH):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((25, 25, WIDTH - 25, HEIGHT - 25), radius=28,
                           outline=(197, 166, 94), width=3)
    draw.rounded_rectangle((42, 42, WIDTH - 42, HEIGHT - 42), radius=22,
                           outline=(93, 137, 133), width=1)

    draw.text((WIDTH // 2, 105), shape_rtl(ayah["surah_name"]),
              font=make_font(30), fill=(236, 213, 155), anchor="mm", direction="rtl", language="ar")
    # Keep Kurdish and numeric text separate so bidirectional reordering
    # cannot scramble the verse number and the fraction.
    draw.text((WIDTH // 2, 150),
              "ئایەت: " + str(ayah["ayah_number"]),
              font=make_font(23), fill=(224, 233, 229), anchor="mm",
              direction="rtl", language="ar")
    draw.text((WIDTH // 2, 183),
              str(index) + " / " + str(AYAH_COUNT),
              font=make_latin_font(20), fill=(224, 233, 229), anchor="mm")

    draw.rounded_rectangle((65, 220, WIDTH - 65, 610), radius=24,
                           fill=(20, 52, 57), outline=(126, 153, 122), width=2)
    draw.text((WIDTH // 2, 255), shape_rtl("بِسْمِ اللَّهِ"),
              font=make_font(25), fill=(197, 166, 94), anchor="mm", direction="rtl", language="ar")
    draw_rtl_block(draw, ayah["arabic"], 315, WIDTH - 155, 46,
                   (255, 250, 231), max_lines=7, line_gap=15)

    draw.rounded_rectangle((65, 650, WIDTH - 65, 1000), radius=24,
                           fill=(17, 45, 51), outline=(126, 153, 122), width=2)
    draw.text((WIDTH // 2, 690), shape_rtl("واتای کوردی"),
              font=make_font(30), fill=(236, 213, 155), anchor="mm", direction="rtl", language="ar")
    draw_rtl_block(draw, ayah["translation"], 750, WIDTH - 155, 34,
                   (245, 247, 241), max_lines=7, line_gap=14)

    draw.line((100, 1050, WIDTH - 100, 1050), fill=(126, 153, 122), width=2)
    # Keep Latin and Kurdish source credit separate; mixing both scripts
    # in one line can produce confusing visual ordering.
    draw.text((WIDTH // 2, 1078), "QuranEnc.com",
              font=make_latin_font(19), fill=(218, 220, 204), anchor="mm")
    draw.text((WIDTH // 2, 1110), shape_rtl("سەرچاوەی وەرگێڕان: واتای کوردی"),
              font=make_font(18), fill=(218, 220, 204), anchor="mm",
              direction="rtl", language="ar")
    draw.text((WIDTH // 2, 1160), shape_rtl("قورئانی پیرۆز"),
              font=make_font(25), fill=(236, 213, 155), anchor="mm",
              direction="rtl", language="ar")
    image.save(image_path, quality=95)

# =========================
# DOWNLOAD RECITATION AUDIO
# =========================
def download_ayah_audio(ayah, audio_path):
    response = get_json(QURAN_API + "/ayah/" + str(ayah["global_number"]) + "/ar.alafasy")
    if response.get("code") != 200:
        raise RuntimeError("نەتوانرا دەنگی ئایەتی " + str(ayah["ayah_number"]) + " وەربگیرێت.")
    audio_url = response.get("data", {}).get("audio")
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
        print("Preparing ayah", index, "of", AYAH_COUNT)
        image_path = os.path.join(WORK_DIR, "ayah_" + str(index) + ".png")
        audio_path = os.path.join(WORK_DIR, "ayah_" + str(index) + ".mp3")
        clip_path = os.path.join(WORK_DIR, "clip_" + str(index) + ".mp4")
        create_ayah_image(ayah, index, image_path)
        download_ayah_audio(ayah, audio_path)
        run_command([
            "ffmpeg", "-y", "-loop", "1", "-framerate", "24", "-i", image_path,
            "-i", audio_path, "-vf", "scale=720:1280,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
            "-tune", "stillimage", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", clip_path
        ])
        clip_files.append(clip_path)

    concat_path = os.path.join(WORK_DIR, "clips.txt")
    with open(concat_path, "w", encoding="utf-8") as file:
        for clip_path in clip_files:
            safe_path = os.path.abspath(clip_path).replace("'", "'\\''")
            file.write("file '" + safe_path + "'\n")

    run_command([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", OUTPUT_VIDEO
    ])
    if not os.path.exists(OUTPUT_VIDEO) or os.path.getsize(OUTPUT_VIDEO) < 10000:
        raise RuntimeError("ڤیدیۆکە دروست نەبوو یان قەبارەکەی زۆر بچووکە.")

    with open(os.path.join(WORK_DIR, "selected_ayahs.json"), "w", encoding="utf-8") as file:
        json.dump(selected_ayahs, file, ensure_ascii=False, indent=2)
    return selected_ayahs

# =========================
# SEND VIDEO TO TELEGRAM
# =========================
def send_video(ayahs):
    caption = (
        "📖 " + ayahs[0]["surah_name"] + "\n"
        + "ئایەتەکان: " + str(ayahs[0]["ayah_number"]) + "–" + str(ayahs[-1]["ayah_number"]) + "\n"
        + "🤍 ١٠ ئایەت لەگەڵ دەنگ و واتای کوردی\n"
        + "سەرچاوەی وەرگێڕانی واتا: QuranEnc.com\nhttps://quranenc.com"
    )
    boundary = "----QuranVideoBoundary7MA4YWxkTrZu0gW"

    def field_part(name, value):
        return ("--" + boundary + "\r\n").encode("utf-8") + (
            'Content-Disposition: form-data; name="' + name + '"\r\n\r\n'
        ).encode("utf-8") + value.encode("utf-8") + b"\r\n"

    body = field_part("chat_id", CHANNEL)
    body += field_part("caption", caption)
    body += field_part("supports_streaming", "true")
    body += ("--" + boundary + "\r\n").encode("utf-8")
    body += b'Content-Disposition: form-data; name="video"; filename="quran_nightly.mp4"\r\n'
    body += b"Content-Type: video/mp4\r\n\r\n"
    with open(OUTPUT_VIDEO, "rb") as file:
        body += file.read()
    body += ("\r\n--" + boundary + "--\r\n").encode("utf-8")

    request = urllib.request.Request(
        "https://api.telegram.org/bot" + TOKEN + "/sendVideo",
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "User-Agent": "QuranNightlyVideo/1.1"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError("Telegram video upload failed: " + details)
    if not result.get("ok"):
        raise RuntimeError("Telegram نەیتوانی ڤیدیۆکە بنێرێت: " + str(result))
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
