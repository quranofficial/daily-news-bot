# -*- coding: utf-8 -*-
"""Quran video rendering helpers used by the user bot (botyuser.py).

This module only renders ayah overlay images and provides fonts.
All channel-broadcast / nightly job code has been removed:
the bot only answers to the user who requested a video.
"""

import os
import json
import shutil
import threading
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont


# =========================
# CONFIGURATION
# =========================

WIDTH = 720
HEIGHT = 1280

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# All user-selectable fonts live in the fonts/ folder (fonts/quran1-3.ttf
# for the ayah, fonts/rabar1-3.ttf for the Kurdish text). The two paths
# below are only last-resort fallbacks.
FONT_DIR = os.path.join(BASE_DIR, "fonts")
QURAN_FONT_PATH = os.path.join(FONT_DIR, "quran1.ttf")
TAFSIR_FONT_PATH = os.path.join(FONT_DIR, "rabar1.ttf")

FONT_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "quranofficial/daily-news-bot/main/"
)

DB_URL = (
    "https://raw.githubusercontent.com/"
    "sahanddler/home_dialog/main/quran.db"
)
DB_PATH = os.path.join(BASE_DIR, "quran.db")

HEADERS = {
    "User-Agent": "QuranVideoBot/4.0"
}


# =========================
# USER-SELECTABLE FONTS (3 Quran fonts + 3 text styles)
# =========================

FONT_FILES = {
    "quran1": {"file": "fonts/quran1.ttf", "name": "قورئان ١"},
    "quran2": {"file": "fonts/quran2.ttf", "name": "قورئان ٢"},
    "quran3": {"file": "fonts/quran3.ttf", "name": "قورئان ٣"},
    "rabar1": {"file": "fonts/rabar1.ttf", "name": "نووسین ١"},
    "rabar2": {"file": "fonts/rabar2.ttf", "name": "نووسین ٢"},
    "rabar3": {"file": "fonts/rabar3.ttf", "name": "نووسین ٣"},
}

DEFAULT_AYAH_FONT = "quran1"
DEFAULT_TAFSIR_FONT = "rabar1"

# Font keys saved by older versions of the bot are mapped to the new set.
FONT_MIGRATION = {
    "quran": "quran1",
    "amiri": "quran2",
    "rabar": "rabar1",
}

# "custom" means: the user uploaded their own font file from their phone.
CUSTOM_FONT_KEY = "custom"


# =========================
# AYAH NUMBER LANGUAGE (English 123 / Kurdish ١٢٣)
# =========================

# "en" -> 123 (English digits), "ku" -> ١٢٣ (Kurdish/Arabic-Indic digits).
NUMBER_LANGS = ("en", "ku")

_EASTERN_DIGIT_MAP = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def format_number(value, lang="en"):
    """Render a number with English (123) or Kurdish (١٢٣) digits."""
    text = str(value)
    if lang == "ku":
        return text.translate(_EASTERN_DIGIT_MAP)
    return text


def normalize_font_key(key):
    """Map old keys / validate keys; returns a valid key, "custom" or None."""
    key = str(key or "").strip()
    key = FONT_MIGRATION.get(key, key)

    if key == CUSTOM_FONT_KEY:
        return CUSTOM_FONT_KEY

    return key if key in FONT_FILES else None

_FONT_LOCK = threading.Lock()


def font_label(key):
    """Kurdish display name for a font key."""
    info = FONT_FILES.get(str(key))
    return info["name"] if info else str(key)


def _download_font(path):
    """Download a missing font file from the bot repository (fonts/ folder)."""
    relative = os.path.relpath(path, BASE_DIR).replace(os.sep, "/")
    url = FONT_BASE_URL + relative
    temporary_path = path + ".part"

    try:
        request = urllib.request.Request(url, headers=HEADERS)

        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()

        if len(data) < 10000:
            raise RuntimeError(
                "فۆنتی دابەزێنراو زۆر بچووکە: " + url
            )

        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(temporary_path, "wb") as file:
            file.write(data)

        os.replace(temporary_path, path)

    except Exception as error:
        raise RuntimeError(
            "نەتوانرا فۆنتی «%s» وەربگیرێت. دڵنیابە فۆڵدەری fonts\n"
            "لەسەر سێرڤەرەکە هەیە و فۆنتەکان تێدایە.\n%s"
            % (relative, error)
        )

    finally:
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass


def ensure_font(key):
    """Return a usable local path for the font key, downloading if needed."""
    info = FONT_FILES.get(str(key))

    if not info:
        return None

    path = os.path.join(BASE_DIR, info["file"])

    if os.path.isfile(path) and os.path.getsize(path) > 10000:
        return path

    with _FONT_LOCK:
        if os.path.isfile(path) and os.path.getsize(path) > 10000:
            return path

        _download_font(path)

        return path


# =========================
# NETWORK HELPERS
# =========================

def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


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
    """Default font (rabar1) for labels; kept for compatibility."""
    return load_font(TAFSIR_FONT_PATH, size)


def make_ayah_font(size, font_key=None, custom_path=None):
    """Font for Arabic Quran verses; chosen by the user (3 + own font)."""
    key = normalize_font_key(font_key)

    if key == CUSTOM_FONT_KEY:
        if (
            custom_path
            and os.path.isfile(custom_path)
            and os.path.getsize(custom_path) > 1000
        ):
            try:
                return load_font(custom_path, size)
            except Exception:
                pass  # broken upload -> fall back to the default font
        key = DEFAULT_AYAH_FONT

    if not key:
        key = DEFAULT_AYAH_FONT

    try:
        path = ensure_font(key) or QURAN_FONT_PATH
    except Exception as error:
        raise RuntimeError(
            "نەتوانرا فۆنتی ئایەت ئامادە بکرێت.\n" + str(error)
        )

    try:
        return load_font(path, size)
    except Exception as error:
        raise RuntimeError(
            "نەتوانرا فۆنتی ئایەت بخوێندرێتەوە: "
            + str(path) + "\n" + str(error)
        )


def make_tafsir_font(size, font_key=None, custom_path=None):
    """Font for tafsir and footer text; chosen by the user (3 + own font)."""
    key = normalize_font_key(font_key)

    if key == CUSTOM_FONT_KEY:
        if (
            custom_path
            and os.path.isfile(custom_path)
            and os.path.getsize(custom_path) > 1000
        ):
            try:
                return load_font(custom_path, size)
            except Exception:
                pass  # broken upload -> fall back to the default font
        key = DEFAULT_TAFSIR_FONT

    if not key:
        key = DEFAULT_TAFSIR_FONT

    try:
        path = ensure_font(key) or TAFSIR_FONT_PATH
    except Exception as error:
        raise RuntimeError(
            "نەتوانرا فۆنتی تەفسیر ئامادە بکرێت.\n" + str(error)
        )

    try:
        return load_font(path, size)
    except Exception as error:
        raise RuntimeError(
            "نەتوانرا فۆنتی تەفسیر بخوێندرێتەوە: "
            + str(path) + "\n" + str(error)
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
# AUTO-FIT RTL TEXT (plan + paint)
#
# The text is first "planned" (font size, wrapped lines, positions) and
# then painted. This makes it possible to paint every layer TWICE:
# once as a soft black silhouette (the shadow) and once in the real
# color, so the shadow sits cleanly UNDER the text.
# =========================

def plan_rtl_block(
    text,
    max_width,
    font_size,
    max_lines=40,
    line_gap=5,
    max_height=None,
    use_quran_font=False,
    font_key=None,
    custom_font_path=None
):
    """Fit RTL text into the frame; returns a painting plan (or None if empty)."""
    text = str(text).strip()

    if not text:
        return None

    # Ayah text uses the user-selected ayah font.
    # Tafsir uses the user-selected tafsir font.
    if use_quran_font:
        def font_loader(size):
            return make_ayah_font(size, font_key, custom_font_path)
    else:
        def font_loader(size):
            return make_tafsir_font(size, font_key, custom_font_path)

    selected_font = None
    selected_lines = None
    selected_heights = None
    selected_gap = line_gap

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    for size in range(font_size, 7, -1):
        font = font_loader(size)

        lines = wrap_rtl_text(text, font, max_width)

        if len(lines) > max_lines:
            continue

        current_gap = min(line_gap, max(1, size // 8))

        total_height, heights = get_text_height(
            dummy_draw,
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

    y_offset = 0

    if max_height is not None:
        y_offset += max(0, (max_height - total_height) // 2)

    return {
        "font": selected_font,
        "lines": selected_lines,
        "heights": selected_heights,
        "gap": selected_gap,
        "y_offset": y_offset,
    }


def paint_rtl_block(draw, plan, fill, top, x_shift=0, y_shift=0):
    """Paint a planned text block, vertically centered in its band."""
    if not plan:
        return

    y = top + plan["y_offset"] + y_shift

    for line, line_height in zip(plan["lines"], plan["heights"]):
        try:
            draw.text(
                (WIDTH // 2 + x_shift, y),
                shape_rtl(line),
                font=plan["font"],
                fill=fill,
                anchor="mt",
                direction="rtl",
                language="ar"
            )
        except (TypeError, ValueError):
            draw.text(
                (WIDTH // 2 + x_shift, y),
                shape_rtl(line),
                font=plan["font"],
                fill=fill,
                anchor="mt"
            )

        y += line_height + plan["gap"]


# =========================
# SOFT TEXT SHADOW
# =========================

# A gentle blurred shadow under every text element keeps the text
# readable and elegant on any background (bright or dark).
SHADOW_COLOR = (0, 0, 0, 110)
SHADOW_OFFSET = (2, 3)
SHADOW_BLUR = 2.4


def _safe_text(draw, xy, text, font, fill, anchor="mm"):
    try:
        draw.text(
            xy, text,
            font=font,
            fill=fill,
            anchor=anchor,
            direction="rtl",
            language="ar"
        )
    except (TypeError, ValueError):
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _paint_ops(ops, draw, shadow_pass):
    """Paint prepared text elements; in shadow_pass paint the silhouette."""
    for op in ops:
        fill = SHADOW_COLOR if shadow_pass else op["fill"]

        if op["kind"] == "text":
            xy = op["xy"]

            if shadow_pass:
                xy = (xy[0] + SHADOW_OFFSET[0], xy[1] + SHADOW_OFFSET[1])

            _safe_text(draw, xy, op["text"], op["font"], fill, op.get("anchor", "mm"))

        else:
            if not op.get("plan"):
                continue

            if shadow_pass:
                paint_rtl_block(
                    draw, op["plan"], fill, op["top"],
                    SHADOW_OFFSET[0], SHADOW_OFFSET[1]
                )
            else:
                paint_rtl_block(draw, op["plan"], fill, op["top"])


# =========================
# TWO TRANSPARENT TEXT LAYERS (ayah layer + tafsir layer)
#
# The ayah and the tafsir live on separate transparent PNGs, so the
# video renderer can animate them independently (staggered entrance).
# =========================
def render_overlay_parts(ayah, include_tafsir=True, settings=None):
    """Render the text as two RGBA layers; returns (ayah_img, tafsir_img|None).

    ayah layer:   top surah info + basmala + ayah text
                  (plus footer / bottom info when tafsir is hidden)
    tafsir layer: tafsir text + footer + bottom surah info
    """
    settings = settings or {}
    ayah_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    tafsir_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ayah_shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    tafsir_shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ayah_draw = ImageDraw.Draw(ayah_img)
    tafsir_draw = ImageDraw.Draw(tafsir_img)
    ayah_shadow_draw = ImageDraw.Draw(ayah_shadow)
    tafsir_shadow_draw = ImageDraw.Draw(tafsir_shadow)

    ayah_color = tuple(settings.get("ayah_color", (255, 255, 255)))
    tafsir_color = tuple(settings.get("tafsir_color", (255, 210, 90)))
    other_color = tuple(settings.get("other_color", (255, 255, 255)))
    ayah_size = int(settings.get("ayah_size", 44))
    tafsir_size = int(settings.get("tafsir_size", 30))
    other_size = int(settings.get("other_size", 24))
    ayah_y = int(settings.get("ayah_y", 250))
    tafsir_y = int(settings.get("tafsir_y", 680))
    other_y = int(settings.get("other_y", 115))
    footer_size = max(12, int(settings.get("footer_size", 34)))
    ayah_font_key_raw = str(settings.get("ayah_font") or DEFAULT_AYAH_FONT)
    tafsir_font_key_raw = str(settings.get("tafsir_font") or DEFAULT_TAFSIR_FONT)
    ayah_custom = settings.get("ayah_font_custom") if ayah_font_key_raw == CUSTOM_FONT_KEY else None
    tafsir_custom = settings.get("tafsir_font_custom") if tafsir_font_key_raw == CUSTOM_FONT_KEY else None
    ayah_font_key = normalize_font_key(ayah_font_key_raw) or DEFAULT_AYAH_FONT
    tafsir_font_key = normalize_font_key(tafsir_font_key_raw) or DEFAULT_TAFSIR_FONT

    # Surah name + ayah number: ONE on/off switch and ONE position choice.
    show_info = bool(settings.get("show_surah_info", True))
    info_pos = str(settings.get("surah_info_pos", "bottom") or "bottom").lower()

    # Ayah-number digit language: English (123) or Kurdish (١٢٣).
    number_lang = str(settings.get("number_lang", "en") or "en").lower()
    if number_lang not in NUMBER_LANGS:
        number_lang = "en"

    if info_pos not in ("top", "bottom"):
        info_pos = "bottom"

    ops_ayah = []
    ops_tafsir = []

    if show_info and info_pos == "top":
        ops_ayah.append({
            "kind": "text",
            "xy": (WIDTH // 2, other_y),
            "text": shape_rtl(str(ayah.get("surah_name", ""))),
            "font": make_tafsir_font(other_size, tafsir_font_key, tafsir_custom),
            "fill": other_color,
            "anchor": "mm",
        })
        if settings.get("show_ayah_number", True):
            ops_ayah.append({
                "kind": "text",
                "xy": (WIDTH // 2, other_y + other_size + 12),
                "text": shape_rtl("ئایەتی " + format_number(ayah.get("ayah_number", ""), number_lang)),
                "font": make_tafsir_font(max(12, other_size - 3), tafsir_font_key, tafsir_custom),
                "fill": other_color,
                "anchor": "mm",
            })

    surah_number = int(ayah.get("surah_number", 0) or 0)
    ayah_number = int(ayah.get("ayah_number", 0) or 0)
    show_basmala = ayah_number == 1 and surah_number not in (1, 9)
    verse_top = ayah_y
    verse_height = int(settings.get("ayah_height", 340))

    if show_basmala:
        ops_ayah.append({
            "kind": "text",
            "xy": (WIDTH // 2, ayah_y),
            "text": shape_rtl("بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"),
            "font": make_ayah_font(ayah_size, ayah_font_key, ayah_custom),
            "fill": ayah_color,
            "anchor": "mm",
        })
        verse_top = ayah_y + max(48, ayah_size + 10)
        verse_height = max(100, verse_height - max(48, ayah_size + 10))

    ops_ayah.append({
        "kind": "block",
        "top": verse_top,
        "plan": plan_rtl_block(
            ayah.get("arabic", ""), WIDTH - 80, ayah_size,
            max_lines=35, line_gap=8, max_height=verse_height,
            use_quran_font=True, font_key=ayah_font_key, custom_font_path=ayah_custom,
        ),
        "fill": ayah_color,
    })

    if include_tafsir:
        # Keep the tafsir band clear of the bottom info block: cap the
        # band so long tafsir text NEVER reaches the footer / surah
        # name area (they used to touch and sometimes overlap).
        tafsir_band = min(
            int(settings.get("tafsir_height", 400)),
            1080 - tafsir_y,
        )
        ops_tafsir.append({
            "kind": "block",
            "top": tafsir_y,
            "plan": plan_rtl_block(
                ayah.get("tafsir", ""), WIDTH - 90, tafsir_size,
                max_lines=50, line_gap=5, max_height=max(120, tafsir_band),
                use_quran_font=False, font_key=tafsir_font_key, custom_font_path=tafsir_custom,
            ),
            "fill": tafsir_color,
        })

    # The footer text can now be hidden/shown by the user (toggle_footer),
    # but the surah name + ayah number stay available in both states.
    show_footer = bool(settings.get("show_footer", True))
    if include_tafsir:
        footer_y = int(settings.get("footer_y", 1150))
    else:
        footer_y = int(settings.get("footer_y", 1045))

    # When tafsir is hidden the footer + bottom info join the ayah layer,
    # so the whole video still animates as one beautiful unit.
    label_ops = ops_tafsir if include_tafsir else ops_ayah

    if show_footer:
        label_ops.append({
            "kind": "text",
            "xy": (WIDTH // 2, footer_y),
            "text": shape_rtl(str(settings.get("footer_text", "قورئانی پیرۆز - ڕێگای ئیسلام"))),
            "font": make_tafsir_font(footer_size, tafsir_font_key, tafsir_custom),
            "fill": other_color,
            "anchor": "mm",
        })
        surah_offset = 44
    else:
        # No footer text: move the surah info up into the freed space.
        surah_offset = 10

    if show_info and info_pos == "bottom":
        info_size = max(12, int(settings.get("other_size", 24)))
        surah_y = footer_y + surah_offset
        ayah_y_footer = surah_y + 32
        # The database name already includes "سورەتی" (e.g. سورەتی الفاتحة),
        # so no extra prefix is added here.
        label_ops.append({
            "kind": "text",
            "xy": (WIDTH // 2, surah_y),
            "text": shape_rtl(str(ayah.get("surah_name", ""))),
            "font": make_tafsir_font(info_size, tafsir_font_key, tafsir_custom),
            "fill": other_color,
            "anchor": "mm",
        })
        label_ops.append({
            "kind": "text",
            "xy": (WIDTH // 2, ayah_y_footer),
            "text": shape_rtl("ژمارەی ئایەت: " + format_number(ayah.get("ayah_number", ""), number_lang)),
            "font": make_tafsir_font(max(12, info_size - 2), tafsir_font_key, tafsir_custom),
            "fill": other_color,
            "anchor": "mm",
        })

    # 1) paint the soft shadow silhouettes, blur them and composite them
    #    UNDER the text, then 2) paint the real text on top.
    _paint_ops(ops_ayah, ayah_shadow_draw, shadow_pass=True)
    ayah_img.alpha_composite(ayah_shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)))
    _paint_ops(ops_ayah, ayah_draw, shadow_pass=False)

    if include_tafsir:
        _paint_ops(ops_tafsir, tafsir_shadow_draw, shadow_pass=True)
        tafsir_img.alpha_composite(tafsir_shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)))
        _paint_ops(ops_tafsir, tafsir_draw, shadow_pass=False)

    return ayah_img, (tafsir_img if include_tafsir else None)


def create_overlay_parts(ayah, ayah_path, tafsir_path, include_tafsir=True, settings=None):
    """Save the two transparent text layers as PNG files for the video renderer."""
    ayah_img, tafsir_img = render_overlay_parts(ayah, include_tafsir, settings)
    ayah_img.save(ayah_path)

    if tafsir_path:
        if tafsir_img is None:
            Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)).save(tafsir_path)
        else:
            tafsir_img.save(tafsir_path)


def create_text_overlay(ayah, index, image_path, include_tafsir=True, settings=None):
    """Single combined transparent overlay (used for the live preview)."""
    ayah_img, tafsir_img = render_overlay_parts(ayah, include_tafsir, settings)

    if tafsir_img is not None:
        ayah_img.alpha_composite(tafsir_img)

    ayah_img.save(image_path)


# =========================
# CREATE VERSE IMAGE
# =========================

def create_ayah_image(ayah, index, image_path, include_tafsir=True, settings=None):
    """Always renders the transparent text overlay (old design system removed)."""
    create_text_overlay(
        ayah, index, image_path,
        include_tafsir=include_tafsir,
        settings=settings or {}
    )
