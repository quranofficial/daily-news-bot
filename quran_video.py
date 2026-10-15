
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

HEADERS = {"User-Agent": "QuranNightlyVideo/3.0"}


# =========================
# NETWORK HELPERS
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
        raise RuntimeError(
            "دەنگ بە تەواوی دابەزێنراو نییە: " + filename
        )


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
# FONT HELPERS
# =========================

def find_font():
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    patterns = [
        "/usr/share/fonts/**/*NotoSansArabic*.ttf",
        "/usr/share/fonts/**/*NotoNaskhArabic*.ttf",
        "/usr/share/fonts/**/*DejaVuSans.ttf",
    ]

    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    raise RuntimeError(
        "فۆنتی عەرەبی نەدۆزرایەوە؛ "
        "fonts-noto-core لە workflow دابمەزرێنە."
    )


FONT_PATH = find_font()

LATIN_FONT_PATH = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)

if not os.path.isfile(LATIN_FONT_PATH):
    LATIN_FONT_PATH = FONT_PATH


def make_font(size):
    try:
        return ImageFont.truetype(
            FONT_PATH,
            size,
            layout_engine=ImageFont.Layout.RAQM
        )
    except (AttributeError, ValueError):
        return ImageFont.truetype(FONT_PATH, size)


def make_latin_font(size):
    return ImageFont.truetype(LATIN_FONT_PATH, size)


def shape_rtl(text):
    return str(text)


# =========================
# TEXT WRAPPING
# =========================

def wrap_rtl_text(text, font, max_width):
    words = str(text).split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        candidate = current + " " + word

        try:
            width = font.getlength(
                candidate,
                direction="rtl",
                language="ar"
            )
        except (TypeError, ValueError):
            width = font.getlength(candidate)

        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def get_text_height(draw, lines, font, line_gap):
    heights = []

    for line in lines:
        try:
            box = draw.textbbox(
                (0, 0),
                line,
                font=font,
                direction="rtl",
                language="ar"
            )
        except (TypeError, ValueError):
            box = draw.textbbox((0, 0), line, font=font)

        heights.append(max(1, box[3] - box[1]))

    total = sum(heights)

    if len(heights) > 1:
        total += (len(heights) - 1) * line_gap

    return total, heights


# =========================
# SMART RTL TEXT DRAWING
# Automatically reduces font size to fit
# =========================

def draw_rtl_block(
    draw,
    text,
    top,
    max_width,
    font_size,
    fill,
    max_lines=20,
    line_gap=6,
    max_height=None
):
    text = str(text).strip()

    if not text:
        return top

    selected_font = None
    selected_lines = None
    selected_heights = None

    # Try larger fonts first, then progressively smaller fonts.
    for size in range(font_size, 9, -1):
        font = make_font(size)

        lines = wrap_rtl_text(
            text,
            font,
            max_width
        )

        if len(lines) > max_lines:
            continue

        total_height, heights = get_text_height(
            draw,
            lines,
            font,
            line_gap
        )

        if (
            max_height is not None
            and total_height > max_height
        ):
            continue

        selected_font = font
        selected_lines = lines
        selected_heights = heights
        break

    # Last fallback for unusually long text.
    if selected_lines is None:
        selected_font = make_font(10)

        selected_lines = wrap_rtl_text(
            text,
            selected_font,
            max_width
        )

        _, selected_heights = get_text_height(
            draw,
            selected_lines,
            selected_font,
            2
        )

        line_gap = 2

    total_height = (
        sum(selected_heights)
        + max(0, len(selected_lines) - 1) * line_gap
    )

    # Center the text vertically in its allocated area.
    y = top

    if max_height is not None:
        y = top + max(0, (max_height - total_height) // 2)

    for line, line_height in zip(
        selected_lines,
        selected_heights
    ):
        try:
            draw.text(
                (WIDTH // 2, y),
                line,
                font=selected_font,
                fill=fill,
                anchor="ma",
                direction="rtl",
                language="ar"
            )
        except (TypeError, ValueError):
            draw.text(
                (WIDTH // 2, y),
                line,
                font=selected_font,
                fill=fill,
                anchor="ma"
            )

        y += line_height + line_gap

    return y


# =========================
# GET 10 RANDOM CONSECUTIVE VERSES
# =========================

def get_random_verses():
    print("Loading Quran surah list...")

    response = get_json(QURAN_API + "/surah")

    if response.get("code") != 200:
        raise RuntimeError(
            "نەتوانرا لیستی سووڕەتەکان وەربگیرێت."
        )

    possible = [
        surah for surah in response["data"]
        if int(surah["numberOfAyahs"]) >= AYAH_COUNT
    ]

    if not possible:
        raise RuntimeError(
            "هیچ سووڕەتێک بۆ ١٠ ئایەتی بەردەوام نەدۆزرایەوە."
        )

    surah = random.choice(possible)

    surah_number = int(surah["number"])
    total_ayahs = int(surah["numberOfAyahs"])

    start_ayah = random.randint(
        1,
        total_ayahs - AYAH_COUNT + 1
    )

    end_ayah = start_ayah + AYAH_COUNT - 1

    print(
        "Selected:",
        surah["name"],
        "verses",
        start_ayah,
        "-",
        end_ayah
    )

    surah_response = get_json(
        QURAN_API + "/surah/" + str(surah_number)
    )

    if surah_response.get("code") != 200:
        raise RuntimeError(
            "نەتوانرا دەقی سووڕەتەکە وەربگیرێت."
        )

    all_ayahs = surah_response["data"]["ayahs"]

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

        if isinstance(translated_items, dict):
            translated_items = (
                translated_items.get("result", [])
            )

    else:
        translated_items = []

    if not isinstance(translated_items, list) or not translated_items:
        raise RuntimeError(
            "وەرگێڕانی کوردی بەردەست نییە؛ "
            "وەڵامی QuranEnc بپشکنە."
        )

    translation_map = {}

    for item in translated_items:
        if not isinstance(item, dict):
            continue

        try:
            verse_number = int(
                item.get(
                    "aya",
                    item.get("aya_number")
                )
            )

            translation = str(
                item.get(
                    "translation",
                    item.get(
                        "text",
                        item.get("translation_text", "")
                    )
                )
            ).strip()

            if translation:
                translation_map[verse_number] = translation

        except (TypeError, ValueError):
            continue

    selected = []

    for ayah in all_ayahs:
        verse_number = int(ayah["numberInSurah"])

        if start_ayah <= verse_number <= end_ayah:
            translation = translation_map.get(
                verse_number,
                ""
            )

            if not translation:
                raise RuntimeError(
                    "واتای ئایەتی "
                    + str(verse_number)
                    + " نەدۆزرایەوە؛ "
                    + "بۆ پاراستنی دروستی، ڤیدیۆکە بڵاو ناکرێتەوە."
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
        raise RuntimeError(
            "ژمارەی ئایەتەکان ١٠ نییە؛ ڤیدیۆکە دروست ناکرێت."
        )

    return selected


# =========================
# CREATE VERSE IMAGE
# Larger frames and adaptive font sizes
# =========================

def create_ayah_image(ayah, index, image_path):
    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (12, 35, 42)
    )

    pixels = image.load()

    for y in range(HEIGHT):
        ratio = y / HEIGHT

        color = (
            int(9 + 8 * ratio),
            int(35 + 20 * ratio),
            int(43 + 17 * ratio)
        )

        for x in range(WIDTH):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)

    # Outer frame
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

    # Surah name
    draw.text(
        (WIDTH // 2, 100),
        shape_rtl(ayah["surah_name"]),
        font=make_font(29),
        fill=(236, 213, 155),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Only the verse number
    draw.text(
        (WIDTH // 2, 145),
        shape_rtl("ئایەتی " + str(ayah["ayah_number"])),
        font=make_font(23),
        fill=(224, 233, 229),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Expanded Arabic verse frame
    draw.rounded_rectangle(
        (45, 180, WIDTH - 45, 635),
        radius=24,
        fill=(20, 52, 57),
        outline=(126, 153, 122),
        width=2
    )

    # Arabic text automatically shrinks when necessary
    draw_rtl_block(
        draw,
        ayah["arabic"],
        200,
        WIDTH - 120,
        39,
        (255, 250, 231),
        max_lines=20,
        line_gap=6,
        max_height=410
    )

    # Expanded Kurdish meaning frame
    draw.rounded_rectangle(
        (45, 650, WIDTH - 45, 1085),
        radius=24,
        fill=(17, 45, 51),
        outline=(126, 153, 122),
        width=2
    )

    draw.text(
        (WIDTH // 2, 690),
        shape_rtl("واتای کوردی"),
        font=make_font(27),
        fill=(236, 213, 155),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Kurdish translation: adaptive font and spacing
    draw_rtl_block(
        draw,
        ayah["translation"],
        720,
        WIDTH - 120,
        30,
        (245, 247, 241),
        max_lines=20,
        line_gap=6,
        max_height=345
    )

    # Decorative bottom line only
    draw.line(
        (100, 1110, WIDTH - 100, 1110),
        fill=(126, 153, 122),
        width=2
    )

    draw.text(
        (WIDTH // 2, 1160),
        shape_rtl("قورئانی پیرۆز"),
        font=make_font(24),
        fill=(236, 213, 155),
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Removed:
    # - Separate Bismillah heading
    # - 1 / 10 counter
    # - QuranEnc.com
    # - Translation source footer

    image.save(image_path)


# =========================
# DOWNLOAD RECITATION AUDIO
# =========================

def download_ayah_audio(ayah, audio_path):
    url = (
        QURAN_API
        + "/ayah/"
        + str(ayah["global_number"])
        + "/ar.alafasy"
    )

    response = get_json(url)

    if response.get("code") != 200:
        raise RuntimeError(
            "نەتوانرا دەنگی ئایەتی "
            + str(ayah["ayah_number"])
            + " وەربگیرێت."
        )

    audio_url = response.get("data", {}).get("audio")

    if not audio_url:
        raise RuntimeError(
            "بەستەری دەنگی ئایەت نەدۆزرایەوە."
        )

    download_file(audio_url, audio_path)


# =========================
# BUILD VIDEO
# =========================

def create_video():
    os.makedirs(WORK_DIR, exist_ok=True)

    selected_ayahs = get_random_verses()
    clip_files = []

    for index, ayah in enumerate(
        selected_ayahs,
        start=1
    ):
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

        run_command([
            "ffmpeg", "-y",
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

    concat_path = os.path.join(
        WORK_DIR,
        "clips.txt"
    )

    with open(
        concat_path,
        "w",
        encoding="utf-8"
    ) as file:
        for clip_path in clip_files:
            safe_path = os.path.abspath(
                clip_path
            ).replace("'", "'\\''")

            file.write(
                "file '" + safe_path + "'\n"
            )

    run_command([
        "ffmpeg", "-y",
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

    if (
        not os.path.exists(OUTPUT_VIDEO)
        or os.path.getsize(OUTPUT_VIDEO) < 10000
    ):
        raise RuntimeError(
            "ڤیدیۆکە دروست نەبوو یان قەبارەکەی زۆر بچووکە."
        )

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
    caption = (
        "📖 " + ayahs[0]["surah_name"] + "\n"
        + "ئایەتەکان: "
        + str(ayahs[0]["ayah_number"])
        + "–"
        + str(ayahs[-1]["ayah_number"])
        + "\n"
        + "🤍 ١٠ ئایەت لەگەڵ دەنگ و واتای کوردی"
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

    body = field_part("chat_id", CHANNEL)
    body += field_part("caption", caption)
    body += field_part("supports_streaming", "true")

    body += (
        "--" + boundary + "\r\n"
    ).encode("utf-8")

    body += (
        b'Content-Disposition: form-data; name="video"; '
        b'filename="quran_nightly.mp4"\r\n'
    )

    body += b"Content-Type: video/mp4\r\n\r\n"

    with open(OUTPUT_VIDEO, "rb") as file:
        body += file.read()

    body += (
        "\r\n--" + boundary + "--\r\n"
    ).encode("utf-8")

    request = urllib.request.Request(
        "https://api.telegram.org/bot"
        + TOKEN
        + "/sendVideo",
        data=body,
        headers={
            "Content-Type": (
                "multipart/form-data; boundary=" + boundary
            ),
            "User-Agent": "QuranNightlyVideo/3.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=180
        ) as response:
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
        raise RuntimeError(
            "FFmpeg دامەزراو نییە."
        )

    print("Starting Quran video...")

    selected_ayahs = create_video()

    print("Video created. Uploading to Telegram...")

    send_video(selected_ayahs)

    print("Finished successfully.")


if __name__ == "__main__":
    main()
