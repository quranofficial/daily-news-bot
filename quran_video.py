
# -*- coding: utf-8 -*-
"""Quran video image renderer with five visual designs."""

import os
import re
import logging
import textwrap
import unicodedata

from PIL import Image, ImageDraw, ImageFont


# =========================
# CONFIGURATION
# =========================

WIDTH = 720
HEIGHT = 1280

BACKGROUND_DIR = os.path.dirname(os.path.abspath(__file__))

QURAN_FONT_PATH = os.path.join(BACKGROUND_DIR, "quran.ttf")
TAFSIR_FONT_PATH = os.path.join(BACKGROUND_DIR, "rabartafsir.ttf")

# فۆنتی جێگرەوە، ئەگەر فۆنتە سەرەکییەکان نەدۆزرێنەوە
FALLBACK_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("quran_video")


# =========================
# FIVE DESIGN PALETTES
# =========================

DESIGNS = {
    1: {
        "name": "سەوز و زێڕین",
        "background": (8, 48, 39),
        "panel": (13, 65, 51),
        "primary": (225, 190, 105),
        "secondary": (244, 225, 169),
        "text": (255, 249, 231),
        "muted": (198, 216, 195),
        "border": (190, 154, 77),
    },
    2: {
        "name": "ڕەش و زێڕین",
        "background": (15, 15, 18),
        "panel": (29, 28, 29),
        "primary": (226, 184, 83),
        "secondary": (250, 220, 145),
        "text": (255, 250, 235),
        "muted": (202, 195, 177),
        "border": (163, 125, 43),
    },
    3: {
        "name": "شینی ئاسمانی",
        "background": (9, 30, 58),
        "panel": (15, 47, 80),
        "primary": (91, 205, 239),
        "secondary": (171, 231, 250),
        "text": (245, 251, 255),
        "muted": (180, 209, 228),
        "border": (65, 155, 204),
    },
    4: {
        "name": "کرێمی و کلاسیک",
        "background": (242, 230, 203),
        "panel": (250, 244, 226),
        "primary": (129, 91, 49),
        "secondary": (161, 119, 67),
        "text": (60, 45, 31),
        "muted": (117, 94, 67),
        "border": (190, 164, 121),
    },
    5: {
        "name": "مۆر و لاڤەندەر",
        "background": (36, 22, 59),
        "panel": (54, 35, 83),
        "primary": (205, 164, 255),
        "secondary": (230, 207, 255),
        "text": (252, 246, 255),
        "muted": (209, 190, 226),
        "border": (155, 112, 202),
    },
}


# =========================
# FONT HELPERS
# =========================

def _find_font_path(preferred_path):
    if preferred_path and os.path.isfile(preferred_path):
        return preferred_path

    for candidate in FALLBACK_FONT_PATHS:
        if os.path.isfile(candidate):
            return candidate

    return None


def _load_font(preferred_path, size):
    path = _find_font_path(preferred_path)

    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception as error:
            log.warning("Could not load font %s: %s", path, error)

    return ImageFont.load_default()


def _quran_font(size):
    return _load_font(QURAN_FONT_PATH, size)


def _tafsir_font(size):
    return _load_font(TAFSIR_FONT_PATH, size)


def _text_width(draw, text, font):
    try:
        return draw.textbbox((0, 0), text, font=font)[2]
    except Exception:
        return len(text) * getattr(font, "size", 20)


def _text_height(draw, text, font):
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[3] - box[1]
    except Exception:
        return getattr(font, "size", 20)


# =========================
# TEXT HELPERS
# =========================

def _clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\r", " ").replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()

    return value


def _wrap_text(draw, text, font, max_width):
    """
    دابەشکردنی دەق بەپێی پانی پیکسڵ، بۆ ئەوەی نووسینەکان
    لە دەرەوەی چوارچێوە نەچن.
    """

    text = _clean_text(text)

    if not text:
        return []

    words = text.split(" ")
    lines = []
    current = ""

    for word in words:
        candidate = word if not current else current + " " + word

        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)

            if _text_width(draw, word, font) <= max_width:
                current = word
            else:
                # وشەی زۆر درێژ بە پارچە دابەش دەکرێت
                part = ""

                for character in word:
                    test = part + character

                    if _text_width(draw, test, font) <= max_width:
                        part = test
                    else:
                        if part:
                            lines.append(part)
                        part = character

                current = part

    if current:
        lines.append(current)

    return lines


def _draw_centered_text(
    draw,
    text,
    y,
    font,
    fill,
    max_width=None,
    x_center=WIDTH // 2
):
    text = _clean_text(text)

    if not text:
        return y

    if max_width is None:
        max_width = WIDTH - 80

    lines = _wrap_text(draw, text, font, max_width)

    for line in lines:
        width = _text_width(draw, line, font)
        height = _text_height(draw, line, font)

        x = x_center - width // 2

        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            direction="rtl"
        )

        y += height + 10

    return y


def _draw_centered_multiline(
    draw,
    text,
    y,
    font,
    fill,
    max_width,
    line_gap=12
):
    text = _clean_text(text)

    if not text:
        return y

    lines = _wrap_text(draw, text, font, max_width)

    for line in lines:
        width = _text_width(draw, line, font)
        height = _text_height(draw, line, font)

        draw.text(
            ((WIDTH - width) // 2, y),
            line,
            font=font,
            fill=fill,
            direction="rtl"
        )

        y += height + line_gap

    return y


# =========================
# DESIGN HELPERS
# =========================

def _get_design(design_id=1):
    try:
        design_id = int(design_id)
    except (TypeError, ValueError):
        design_id = 1

    return DESIGNS.get(design_id, DESIGNS[1])


def _draw_background(image, draw, palette):
    image.paste(palette["background"], (0, 0, WIDTH, HEIGHT))

    # چوارچێوەی دەرەوە
    draw.rounded_rectangle(
        (22, 22, WIDTH - 22, HEIGHT - 22),
        radius=28,
        outline=palette["border"],
        width=3
    )

    # چوارچێوەی ناوەوە
    draw.rounded_rectangle(
        (35, 35, WIDTH - 35, HEIGHT - 35),
        radius=23,
        outline=palette["primary"],
        width=1
    )


def _draw_header(draw, palette):
    title_font = _tafsir_font(34)
    subtitle_font = _tafsir_font(22)

    _draw_centered_text(
        draw,
        "قورئانی پیرۆز",
        65,
        title_font,
        palette["primary"],
        max_width=WIDTH - 100
    )

    _draw_centered_text(
        draw,
        "ڕێگای ئیسلام",
        112,
        subtitle_font,
        palette["muted"],
        max_width=WIDTH - 100
    )

    draw.line(
        (115, 160, WIDTH - 115, 160),
        fill=palette["border"],
        width=2
    )


def _draw_footer(draw, palette):
    # ناونیشانی خوارەوە بە قەبارەی گەورەتر
    footer_font = _tafsir_font(29)

    draw.line(
        (100, HEIGHT - 108, WIDTH - 100, HEIGHT - 108),
        fill=palette["border"],
        width=2
    )

    _draw_centered_text(
        draw,
        "قورئانی پیرۆز - ڕێگای ئیسلام",
        HEIGHT - 88,
        footer_font,
        palette["primary"],
        max_width=WIDTH - 100
    )


# =========================
# AYAH IMAGE LAYOUT
# =========================

def _draw_ayah_number(draw, number, y, palette):
    number_font = _tafsir_font(24)
    label = f"﴿ {number} ﴾"

    _draw_centered_text(
        draw,
        label,
        y,
        number_font,
        palette["secondary"],
        max_width=WIDTH - 100
    )


def _draw_tafsir_panel(
    draw,
    tafsir,
    top,
    bottom,
    palette
):
    """
    تەفسیر لە ناو پانێڵێکی ڕێکخراودا دەنووسێت.
    فۆنتی تەفسیر گەورەتر کراوە.
    """

    left = 62
    right = WIDTH - 62

    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=22,
        fill=palette["panel"],
        outline=palette["border"],
        width=2
    )

    heading_font = _tafsir_font(31)
    body_font = _tafsir_font(34)

    heading = "تەفسیری ئایەت"

    heading_height = _text_height(
        draw,
        heading,
        heading_font
    )

    heading_width = _text_width(
        draw,
        heading,
        heading_font
    )

    draw.text(
        ((WIDTH - heading_width) // 2, top + 22),
        heading,
        font=heading_font,
        fill=palette["primary"],
        direction="rtl"
    )

    line_y = top + heading_height + 42

    draw.line(
        (left + 45, line_y, right - 45, line_y),
        fill=palette["border"],
        width=1
    )

    tafsir = _clean_text(tafsir)

    if not tafsir:
        tafsir = "تەفسیر بۆ ئەم ئایەتە بەردەست نییە."

    text_top = line_y + 22
    max_width = right - left - 48
    lines = _wrap_text(
        draw,
        tafsir,
        body_font,
        max_width
    )

    available_height = bottom - text_top - 20

    # ژمارەی هێڵەکان بەپێی بەرزی بەردەست سنووردار دەکرێت
    line_height = _text_height(
        draw,
        "ئ",
        body_font
    ) + 14

    max_lines = max(1, available_height // line_height)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

        if lines:
            last = lines[-1]

            while last and _text_width(
                draw,
                last + "...",
                body_font
            ) > max_width:
                last = last[:-1]

            lines[-1] = last.rstrip() + "..."

    y = text_top

    for line in lines:
        width = _text_width(draw, line, body_font)
        height = _text_height(draw, line, body_font)

        draw.text(
            ((WIDTH - width) // 2, y),
            line,
            font=body_font,
            fill=palette["text"],
            direction="rtl"
        )

        y += height + 14


def _draw_ayah_panel(
    draw,
    ayah_text,
    top,
    bottom,
    palette
):
    left = 54
    right = WIDTH - 54

    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=24,
        fill=palette["panel"],
        outline=palette["border"],
        width=2
    )

    quran_font = _quran_font(43)
    max_width = right - left - 48

    ayah_text = _clean_text(ayah_text)

    lines = _wrap_text(
        draw,
        ayah_text,
        quran_font,
        max_width
    )

    available_height = bottom - top - 45

    line_height = _text_height(
        draw,
        "الله",
        quran_font
    ) + 17

    max_lines = max(1, available_height // line_height)

    if len(lines) > max_lines:
        # ئەگەر دەقەکە زۆر درێژ بێت، قەبارەی فۆنت کەم دەکەین
        quran_font = _quran_font(35)

        lines = _wrap_text(
            draw,
            ayah_text,
            quran_font,
            max_width
        )

        line_height = _text_height(
            draw,
            "الله",
            quran_font
        ) + 13

    total_height = len(lines) * line_height
    y = top + max(20, (bottom - top - total_height) // 2)

    for line in lines:
        width = _text_width(draw, line, quran_font)
        height = _text_height(draw, line, quran_font)

        draw.text(
            ((WIDTH - width) // 2, y),
            line,
            font=quran_font,
            fill=palette["text"],
            direction="rtl"
        )

        y += height + 13


# =========================
# CREATE AYAH IMAGE
# =========================

def create_ayah_image(
    ayah,
    index,
    output_path,
    design_id=1
):
    """
    وێنەی ئایەتێک دروست دەکات.

    ayah:
        دەتوانێت dict بێت و ستوونەکانی text, asan, ayah, sura_name_ar
        لەخۆ بگرێت.

    index:
        ژمارەی ڕیزبەندی ئایەت لە ڤیدیۆکەدا.

    output_path:
        شوێنی پاشەکەوتکردنی PNG.

    design_id:
        1 سەوز/زێڕین
        2 ڕەش/زێڕین
        3 شینی ئاسمانی
        4 کرێمی/کلاسیک
        5 مۆر/لاڤەندەر
    """

    palette = _get_design(design_id)

    os.makedirs(
        os.path.dirname(os.path.abspath(output_path)),
        exist_ok=True
    )

    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        palette["background"]
    )

    draw = ImageDraw.Draw(image)

    _draw_background(image, draw, palette)
    _draw_header(draw, palette)

    ayah_text = _clean_text(
        ayah.get("text", "")
    )

    tafsir = _clean_text(
        ayah.get("asan", "")
    )

    ayah_number = ayah.get("ayah", index + 1)

    surah_name = _clean_text(
        ayah.get("sura_name_ar", "")
    )

    # ناوی سورەت
    if surah_name:
        surah_font = _tafsir_font(24)

        _draw_centered_text(
            draw,
            surah_name,
            180,
            surah_font,
            palette["secondary"],
            max_width=WIDTH - 100
        )

        ayah_top = 230
    else:
        ayah_top = 205

    ayah_bottom = 620

    _draw_ayah_panel(
        draw,
        ayah_text,
        ayah_top,
        ayah_bottom,
        palette
    )

    _draw_ayah_number(
        draw,
        ayah_number,
        640,
        palette
    )

    tafsir_top = 700
    tafsir_bottom = HEIGHT - 145

    _draw_tafsir_panel(
        draw,
        tafsir,
        tafsir_top,
        tafsir_bottom,
        palette
    )

    _draw_footer(draw, palette)

    image.save(
        output_path,
        format="PNG",
        optimize=True
    )

    return output_path


# =========================
# OPTIONAL PREVIEW / TEST
# =========================

def create_preview(
    output_path,
    design_id=1,
    ayah_text="بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
    tafsir_text="بە ناوی خوای بەخشندە و میهرەبان.",
    ayah_number=1,
    surah_name="سورة الفاتحة"
):
    """
    دروستکردنی وێنەیەکی تاقیکردنەوە بۆ هەر یەکێک لە ٥ دیزاینەکە.
    ئەم فەنکشنە بۆ تاقیکردنەوەی دیزاینەکەیە، نەک بۆ دروستکردنی
    ڤیدیۆی سەرەکی.
    """

    sample_ayah = {
        "text": ayah_text,
        "asan": tafsir_text,
        "ayah": ayah_number,
        "sura_name_ar": surah_name
    }

    return create_ayah_image(
        sample_ayah,
        0,
        output_path,
        design_id=design_id
    )


def create_all_design_previews(output_dir):
    """
    ٥ وێنەی جیاواز دروست دەکات بۆ بینینی دیزاینەکان.
    """

    os.makedirs(output_dir, exist_ok=True)

    results = []

    for design_id in range(1, 6):
        output_path = os.path.join(
            output_dir,
            f"design_{design_id}.png"
        )

        create_preview(
            output_path,
            design_id=design_id
        )

        results.append(output_path)

    return results


# =========================
# MODULE EXPORTS
# =========================

__all__ = [
    "DESIGNS",
    "create_ayah_image",
    "create_preview",
    "create_all_design_previews",
]
