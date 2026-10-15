import os
import json
import random
import sqlite3
import urllib.request
import urllib.error
import subprocess
import glob
import shutil
import unicodedata

from PIL import Image, ImageDraw, ImageFont


# =========================
# CONFIGURATION
# =========================

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]

WIDTH = 720
HEIGHT = 1280
AYAH_COUNT = 10

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

WORK_DIR = os.path.join(BASE_DIR, "quran_video_work")
OUTPUT_VIDEO = os.path.join(BASE_DIR, "quran_nightly.mp4")

# Your own Quran font, uploaded beside quran_video.py
QURAN_FONT_PATH = os.path.join(BASE_DIR, "quran.ttf")

DB_URL = (
    "https://raw.githubusercontent.com/"
    "sahanddler/home_dialog/main/quran.db"
)
DB_PATH = os.path.join(BASE_DIR, "quran.db")

QURAN_API = "https://api.alquran.cloud/v1"

HEADERS = {
    "User-Agent": "QuranNightlyVideo/4.0"
}


# =========================
# NETWORK HELPERS
# =========================

def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


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


def download_file(url, filename):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=120) as response:
        with open(filename, "wb") as output:
            shutil.copyfileobj(response, output)

    if not os.path.isfile(filename) or os.path.getsize(filename) < 1000:
        raise RuntimeError(
            "فایل بە تەواوی دابەزێنراو نییە: " + filename
        )


# =========================
# DOWNLOAD SQLITE DATABASE
# =========================

def ensure_database():
    if os.path.isfile(DB_PATH) and os.path.getsize(DB_PATH) > 1000:
        with open(DB_PATH, "rb") as file:
            if file.read(16) == b"SQLite format 3\x00":
                print("Using existing Quran database.")
                return

    print("Downloading Quran database...")

    temporary_path = DB_PATH + ".download"

    try:
        download_file(DB_URL, temporary_path)

        with open(temporary_path, "rb") as file:
            signature = file.read(16)

        if signature != b"SQLite format 3\x00":
            raise RuntimeError(
                "فایلی دابەزێنراو Database ـی SQLite نییە. "
                "بەستەری quran.db بپشکنە."
            )

        os.replace(temporary_path, DB_PATH)

    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)

    print("Quran database downloaded successfully.")


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
        "فۆنتی بنەڕەتی بۆ دەقی کوردی نەدۆزرایەوە؛ "
        "fonts-noto-core لە workflow دابمەزرێنە."
    )


# System font: for Kurdish tafsir and Kurdish labels
SYSTEM_FONT_PATH = find_font()


def load_font(path, size):
    try:
        return ImageFont.truetype(
            path,
            size,
            layout_engine=ImageFont.Layout.RAQM
        )
    except (AttributeError, ValueError):
        return ImageFont.truetype(path, size)


def make_font(size):
    """System font for Kurdish text and general labels."""
    return load_font(SYSTEM_FONT_PATH, size)


def make_quran_font(size):
    """Your own quran.ttf font for Arabic Quran text."""
    if not os.path.isfile(QURAN_FONT_PATH):
        raise RuntimeError(
            "فایلی quran.ttf نەدۆزرایەوە. "
            "دڵنیابە لەوەی لە هەمان فۆڵدەری "
            "quran_video.py ـدایە."
        )

    try:
        return load_font(QURAN_FONT_PATH, size)
    except Exception as error:
        raise RuntimeError(
            "نەتوانرا فۆنتی quran.ttf بخوێندرێتەوە. "
            "دڵنیابە فایلەکە فۆنتێکی دروستی TTF ـە.\n"
            + str(error)
        )


def shape_rtl(text):
    return str(text)


# =========================
# TEXT WRAPPING
# =========================

def measure_text_width(font, text):
    try:
        return font.getlength(
            text,
            direction="rtl",
            language="ar"
        )
    except (TypeError, ValueError):
        return font.getlength(text)


def wrap_rtl_text(text, font, max_width):
    words = str(text).split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        candidate = current + " " + word

        if measure_text_width(font, candidate) <= max_width:
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

    total_height = sum(heights)

    if len(heights) > 1:
        total_height += (len(heights) - 1) * line_gap

    return total_height, heights


# =========================
# AUTO-FIT RTL TEXT
# =========================

def draw_rtl_block(
    draw,
    text,
    top,
    max_width,
    font_size,
    fill,
    max_lines=40,
    line_gap=5,
    max_height=None,
    use_quran_font=False
):
    text = str(text).strip()

    if not text:
        return top

    font_loader = make_quran_font if use_quran_font else make_font

    selected_font = None
    selected_lines = None
    selected_heights = None
    selected_gap = line_gap

    for size in range(font_size, 7, -1):
        font = font_loader(size)

        lines = wrap_rtl_text(text, font, max_width)

        if len(lines) > max_lines:
            continue

        # Reduce the line gap for smaller fonts.
        current_gap = min(line_gap, max(1, size // 8))

        total_height, heights = get_text_height(
            draw,
            lines,
            font,
            current_gap
        )

        if max_height is not None and total_height > max_height:
            continue

        selected_font = font
        selected_lines = lines
        selected_heights = heights
        selected_gap = current_gap
        break

    if selected_lines is None:
        raise RuntimeError(
            "دەقەکە زۆر درێژە بۆ چوارچێوەی وێنەکە. "
            "بۆ پاراستنی تەواوی دەق، ڤیدیۆکە دروست نەکرا."
        )

    total_height = (
        sum(selected_heights)
        + max(0, len(selected_lines) - 1) * selected_gap
    )

    y = top

    if max_height is not None:
        y += max(0, (max_height - total_height) // 2)

    for line, line_height in zip(selected_lines, selected_heights):
        try:
            draw.text(
                (WIDTH // 2, y),
                shape_rtl(line),
                font=selected_font,
                fill=fill,
                anchor="mt",
                direction="rtl",
                language="ar"
            )
        except (TypeError, ValueError):
            draw.text(
                (WIDTH // 2, y),
                shape_rtl(line),
                font=selected_font,
                fill=fill,
                anchor="mt"
            )

        y += line_height + selected_gap

    return y


# =========================
# ARABIC NAME NORMALIZATION
# =========================

def normalize_arabic_name(value):
    value = str(value or "").strip()

    value = unicodedata.normalize("NFD", value)

    cleaned = []

    for char in value:
        category = unicodedata.category(char)

        if category == "Mn":
            continue

        if char == "\u0640":
            continue

        if char.isspace():
            continue

        cleaned.append(char)

    result = "".join(cleaned)

    if result.startswith("سورة"):
        result = result[len("سورة"):]

    return result


# =========================
# DATABASE SCHEMA DETECTION
# =========================

def quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def find_database_table(connection):
    tables = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()

    required_columns = {
        "text",
        "asan",
        "sura_name_ar",
        "ayah"
    }

    possible_tables = []

    for table_row in tables:
        table_name = table_row[0]

        columns_info = connection.execute(
            "PRAGMA table_info("
            + quote_identifier(table_name)
            + ")"
        ).fetchall()

        columns = [row[1] for row in columns_info]

        column_lookup = {
            column.lower(): column
            for column in columns
        }

        if required_columns.issubset(set(column_lookup.keys())):
            possible_tables.append((table_name, column_lookup))

    if not possible_tables:
        raise RuntimeError(
            "خشتەی Database نەدۆزرایەوە کە "
            "خانەکانی text، asan، sura_name_ar، ayah "
            "لەخۆ بگرێت."
        )

    best_table = None
    best_lookup = None
    best_count = -1

    for table_name, column_lookup in possible_tables:
        count = connection.execute(
            "SELECT COUNT(*) FROM " + quote_identifier(table_name)
        ).fetchone()[0]

        if count > best_count:
            best_table = table_name
            best_lookup = column_lookup
            best_count = count

    print("Database table:", best_table, "| rows:", best_count)

    return best_table, best_lookup


# =========================
# GET QURAN DATA FROM SQLITE
# =========================

def get_random_verses():
    ensure_database()

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        table_name, column_lookup = find_database_table(connection)

        query = "SELECT * FROM " + quote_identifier(table_name)

        try:
            rows = connection.execute(query + " ORDER BY rowid").fetchall()
        except sqlite3.OperationalError:
            rows = connection.execute(query).fetchall()

        if not rows:
            raise RuntimeError(
                "Database ـەکە هیچ ئایەتێکی تێدا نییە."
            )

        text_column = column_lookup["text"]
        tafsir_column = column_lookup["asan"]
        name_column = column_lookup["sura_name_ar"]
        ayah_column = column_lookup["ayah"]

        surah_number_aliases = [
            "sura", "sora", "surah",
            "sura_id", "sora_id", "surah_id",
            "sura_no", "sora_no", "surah_no",
            "sura_number", "sora_number", "surah_number",
            "sura_num", "sora_num"
        ]

        surah_number_column = None

        for alias in surah_number_aliases:
            if alias in column_lookup:
                surah_number_column = column_lookup[alias]
                break

        surah_response = get_json(QURAN_API + "/surah")

        if surah_response.get("code") != 200:
            raise RuntimeError(
                "نەتوانرا زانیاری سووڕەتەکان وەربگیرێت."
            )

        api_surahs = surah_response["data"]

        api_name_to_number = {}

        for surah in api_surahs:
            normalized_name = normalize_arabic_name(surah["name"])
            api_name_to_number[normalized_name] = int(surah["number"])

        numeric_surah_values = []

        if surah_number_column:
            for row in rows:
                try:
                    numeric_surah_values.append(
                        int(row[surah_number_column])
                    )
                except (TypeError, ValueError):
                    pass

        zero_based_surah_numbers = (
            bool(numeric_surah_values)
            and min(numeric_surah_values) == 0
            and max(numeric_surah_values) <= 113
        )

        name_order = []

        for row in rows:
            name = str(row[name_column] or "").strip()

            if name and name not in name_order:
                name_order.append(name)

        ordered_name_to_number = {}

        for index, name in enumerate(name_order, start=1):
            if index <= 114:
                ordered_name_to_number[name] = index

        api_surah_counts = {
            int(surah["number"]): int(surah["numberOfAyahs"])
            for surah in api_surahs
        }

        global_start = {}
        running_total = 0

        for surah_number in range(1, 115):
            global_start[surah_number] = running_total
            running_total += api_surah_counts.get(surah_number, 0)

        grouped = {}

        for row in rows:
            try:
                ayah_number = int(row[ayah_column])
            except (TypeError, ValueError):
                continue

            if ayah_number < 1:
                continue

            surah_name = str(row[name_column] or "").strip()

            if not surah_name:
                continue

            surah_number = None

            if surah_number_column:
                try:
                    raw_number = int(row[surah_number_column])

                    if zero_based_surah_numbers:
                        raw_number += 1

                    if 1 <= raw_number <= 114:
                        surah_number = raw_number

                except (TypeError, ValueError):
                    pass

            if surah_number is None:
                normalized_name = normalize_arabic_name(surah_name)
                surah_number = api_name_to_number.get(normalized_name)

            if surah_number is None:
                surah_number = ordered_name_to_number.get(surah_name)

            if not surah_number:
                continue

            arabic_text = str(row[text_column] or "").strip()
            tafsir_text = str(row[tafsir_column] or "").strip()

            if not arabic_text or not tafsir_text:
                continue

            item = {
                "surah_number": surah_number,
                "surah_name": surah_name,
                "ayah_number": ayah_number,
                "global_number": global_start[surah_number] + ayah_number,
                "arabic": arabic_text,
                "tafsir": tafsir_text
            }

            grouped.setdefault(surah_number, []).append(item)

        candidates = []

        for surah_number, ayahs in grouped.items():
            ayahs.sort(key=lambda item: item["ayah_number"])

            for start in range(0, len(ayahs) - AYAH_COUNT + 1):
                window = ayahs[start:start + AYAH_COUNT]

                expected_numbers = list(
                    range(
                        window[0]["ayah_number"],
                        window[0]["ayah_number"] + AYAH_COUNT
                    )
                )

                actual_numbers = [
                    item["ayah_number"] for item in window
                ]

                if actual_numbers != expected_numbers:
                    continue

                if not all(item["arabic"] and item["tafsir"] for item in window):
                    continue

                candidates.append(window)

        if not candidates:
            raise RuntimeError(
                "هیچ کۆمەڵەیەکی ١٠ ئایەتی بەردەوام "
                "لەگەڵ تەفسیری ئاسان لە Database نەدۆزرایەوە."
            )

        selected = random.choice(candidates)

        print(
            "Selected surah:",
            selected[0]["surah_name"],
            "| ayahs:",
            selected[0]["ayah_number"],
            "-",
            selected[-1]["ayah_number"]
        )

        return selected

    finally:
        connection.close()


# =========================
# CREATE VERSE IMAGE
# =========================

# =========================
# PREMIUM ISLAMIC VIDEO DESIGN
# Replace your old create_ayah_image() with this
# =========================

def create_ayah_image(ayah, index, image_path):

    # Premium Islamic color palette
    BG_TOP = (7, 29, 32)
    BG_BOTTOM = (17, 55, 49)
    GOLD = (220, 184, 106)
    GOLD_LIGHT = (246, 222, 165)
    WHITE = (250, 248, 237)
    MUTED = (190, 211, 198)
    PANEL = (13, 42, 43)
    PANEL_LIGHT = (19, 50, 49)
    BORDER = (72, 112, 91)

    image = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    draw = ImageDraw.Draw(image)

    # -------------------------
    # 1. Elegant gradient
    # -------------------------
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)

        color = tuple(
            int(BG_TOP[i] * (1 - ratio) + BG_BOTTOM[i] * ratio)
            for i in range(3)
        )

        draw.line((0, y, WIDTH, y), fill=color)

    # Subtle decorative circles
    for radius in (100, 145, 190):
        draw.ellipse(
            (-radius // 2, 120 - radius // 2,
             radius // 2, 120 + radius // 2),
            outline=(24, 66, 57),
            width=2
        )

        draw.ellipse(
            (WIDTH - radius // 2, 120 - radius // 2,
             WIDTH + radius // 2, 120 + radius // 2),
            outline=(24, 66, 57),
            width=2
        )

    # -------------------------
    # 2. Double outer frame
    # -------------------------
    draw.rounded_rectangle(
        (22, 22, WIDTH - 22, HEIGHT - 22),
        radius=30,
        outline=GOLD,
        width=3
    )

    draw.rounded_rectangle(
        (36, 36, WIDTH - 36, HEIGHT - 36),
        radius=25,
        outline=BORDER,
        width=1
    )

    # -------------------------
    # 3. Decorative corner diamonds
    # -------------------------
    def draw_diamond(cx, cy, size, color):
        points = [
            (cx, cy - size),
            (cx + size, cy),
            (cx, cy + size),
            (cx - size, cy)
        ]

        draw.polygon(points, outline=color)
        draw.ellipse(
            (cx - 2, cy - 2, cx + 2, cy + 2),
            fill=color
        )

    for x in (57, WIDTH - 57):
        for y in (57, HEIGHT - 57):
            draw_diamond(x, y, 12, GOLD)

    # -------------------------
    # 4. Small brand header
    # -------------------------
    draw.rounded_rectangle(
        (170, 58, WIDTH - 170, 112),
        radius=24,
        fill=(22, 61, 52),
        outline=GOLD,
        width=1
    )

    draw.text(
        (WIDTH // 2, 85),
        shape_rtl("ڕێگای ئیسلام"),
        font=make_font(24),
        fill=GOLD_LIGHT,
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Small ornamental divider
    draw.line(
        (185, 132, 300, 132),
        fill=BORDER,
        width=2
    )

    draw.line(
        (420, 132, 535, 132),
        fill=BORDER,
        width=2
    )

    draw_diamond(WIDTH // 2, 132, 7, GOLD)

    # -------------------------
    # 5. Surah name
    # -------------------------
    draw.text(
        (WIDTH // 2, 165),
        shape_rtl(ayah["surah_name"]),
        font=make_quran_font(30),
        fill=GOLD_LIGHT,
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Ayah counter badge
    counter_text = (
        "ئایەتی "
        + str(ayah["ayah_number"])
        + "  |  "
        + str(index).zfill(2)
        + " / "
        + str(AYAH_COUNT)
    )

    draw.rounded_rectangle(
        (225, 190, 495, 228),
        radius=18,
        fill=(24, 60, 52),
        outline=BORDER,
        width=1
    )

    draw.text(
        (WIDTH // 2, 209),
        shape_rtl(counter_text),
        font=make_font(18),
        fill=MUTED,
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # -------------------------
    # 6. Quran verse panel
    # -------------------------
    draw.rounded_rectangle(
        (49, 246, WIDTH - 49, 605),
        radius=25,
        fill=PANEL,
        outline=GOLD,
        width=2
    )

    draw.rounded_rectangle(
        (59, 256, WIDTH - 59, 595),
        radius=20,
        outline=BORDER,
        width=1
    )

    # Decorative top ornament
    draw_diamond(WIDTH // 2, 270, 7, GOLD)

    # Quran text uses your quran.ttf font
    draw_rtl_block(
        draw,
        ayah["arabic"],
        290,
        WIDTH - 125,
        39,
        WHITE,
        max_lines=25,
        line_gap=7,
        max_height=285,
        use_quran_font=True
    )

    # -------------------------
    # 7. Tafsir panel
    # -------------------------
    draw.rounded_rectangle(
        (49, 625, WIDTH - 49, 1080),
        radius=25,
        fill=PANEL_LIGHT,
        outline=BORDER,
        width=2
    )

    # Tafsir heading background
    draw.rounded_rectangle(
        (155, 645, WIDTH - 155, 700),
        radius=20,
        fill=(29, 69, 55),
        outline=(126, 148, 94),
        width=1
    )

    draw.text(
        (WIDTH // 2, 673),
        shape_rtl("تەفسیری ئاسان"),
        font=make_font(27),
        fill=GOLD_LIGHT,
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Separator below heading
    draw.line(
        (105, 716, WIDTH - 105, 716),
        fill=BORDER,
        width=2
    )

    draw_diamond(WIDTH // 2, 716, 5, GOLD)

    # Kurdish tafsir uses the system font
    draw_rtl_block(
        draw,
        ayah["tafsir"],
        738,
        WIDTH - 125,
        28,
        WHITE,
        max_lines=45,
        line_gap=6,
        max_height=320,
        use_quran_font=False
    )

    # -------------------------
    # 8. Elegant footer
    # -------------------------
    draw.line(
        (105, 1105, WIDTH - 105, 1105),
        fill=GOLD,
        width=2
    )

    draw_diamond(WIDTH // 2, 1105, 6, GOLD)

    draw.text(
        (WIDTH // 2, 1145),
        shape_rtl("قورئانی پیرۆز"),
        font=make_quran_font(27),
        fill=GOLD_LIGHT,
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    draw.text(
        (WIDTH // 2, 1183),
        shape_rtl("هەر ڕۆژە و ئایەتێک"),
        font=make_font(20),
        fill=MUTED,
        anchor="mm",
        direction="rtl",
        language="ar"
    )

    # Bottom decorative line
    draw.line(
        (240, 1215, 480, 1215),
        fill=BORDER,
        width=2
    )

    draw_diamond(WIDTH // 2, 1215, 5, GOLD)

    # Save the final image
    image.save(image_path, quality=95)

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

    for index, ayah in enumerate(selected_ayahs, start=1):
        print("Preparing ayah", index, "of", AYAH_COUNT)

        image_path = os.path.join(
            WORK_DIR, "ayah_" + str(index) + ".png"
        )

        audio_path = os.path.join(
            WORK_DIR, "ayah_" + str(index) + ".mp3"
        )

        clip_path = os.path.join(
            WORK_DIR, "clip_" + str(index) + ".mp4"
        )

        create_ayah_image(ayah, index, image_path)
        download_ayah_audio(ayah, audio_path)

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

    concat_path = os.path.join(WORK_DIR, "clips.txt")

    with open(concat_path, "w", encoding="utf-8") as file:
        for clip_path in clip_files:
            safe_path = os.path.abspath(clip_path).replace("\\", "/")
            file.write("file '" + safe_path + "'\n")

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

    if not os.path.exists(OUTPUT_VIDEO) or os.path.getsize(OUTPUT_VIDEO) < 10000:
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
        + "تەفسیری ئاسان - ڕێگای ئیسلام"
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

    body += ("--" + boundary + "\r\n").encode("utf-8")
    body += (
        b'Content-Disposition: form-data; name="video"; '
        b'filename="quran_nightly.mp4"\r\n'
    )
    body += b"Content-Type: video/mp4\r\n\r\n"

    with open(OUTPUT_VIDEO, "rb") as file:
        body += file.read()

    body += ("\r\n--" + boundary + "--\r\n").encode("utf-8")

    request = urllib.request.Request(
        "https://api.telegram.org/bot" + TOKEN + "/sendVideo",
        data=body,
        headers={
            "Content-Type": (
                "multipart/form-data; boundary=" + boundary
            ),
            "User-Agent": "QuranNightlyVideo/4.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "Telegram video upload failed: " + details
        )

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram نەیتوانی ڤیدیۆکە بنێرێت: " + str(result)
        )

    print("Quran video sent successfully.")


# =========================
# MAIN
# =========================

def main():
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg دامەزراو نییە.")

    # Check your custom Quran font before starting
    if not os.path.isfile(QURAN_FONT_PATH):
        raise RuntimeError(
            "quran.ttf لە هەمان شوێنی quran_video.py نەدۆزرایەوە."
        )

    print("Starting Quran tafsir video...")

    selected_ayahs = create_video()

    print("Video created. Uploading to Telegram...")

    send_video(selected_ayahs)

    print("Finished successfully.")


if __name__ == "__main__":
    main()
