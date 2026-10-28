# -*- coding: utf-8 -*-
"""Telegram Quran video bot with reciter selection and timing databases."""

import os
import sys
import json
import time
import queue
import base64
import random
import hashlib
import shutil
import sqlite3
import threading
import subprocess
import traceback
import urllib.request
import urllib.parse
import urllib.error

from PIL import Image, ImageFont, ImageOps

import quran_video as qv


# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
TG = "https://api.telegram.org/bot" + BOT_TOKEN + "/"
QURAN_API = "https://api.alquran.cloud/v1"

# No fixed maximum ayah count per video.
MAX_AYAHS = None

POLL_TIMEOUT = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "quran_user_jobs")
USER_FONTS_DIR = os.path.join(JOBS_DIR, "user_fonts")
TIMINGS_DIR = os.path.join(BASE_DIR, "reciter_timings")
BACKGROUND_DIR = BASE_DIR
BACKGROUND_BASE_URL = "https://raw.githubusercontent.com/quranofficial/daily-news-bot/main/"
RECITERS_JSON_PATH = os.path.join(BASE_DIR, "reciters.json")
LOG_PATH = os.path.join(BASE_DIR, "bot_debug.log")
USER_PREFS_PATH = os.path.join(BASE_DIR, "user_preferences.json")
REQUIRED_CHANNEL = "@Regayislam_1"

TIMINGS_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "quranofficial/daily-news-bot/main/"
)

SESSIONS = {}
_SURAH_CACHE = None


# =========================
# TEXT ANIMATIONS (4 entrance styles for the ayah + tafsir text)
# =========================

# fade  : soft alpha fade-in (ayah first, tafsir follows shortly after)
# rise  : text gently rises up from below while fading in
# zoom  : the ayah settles down from a slight zoom while fading in
# drop  : text descends softly from above while fading in
# (The old "slide / خاڵێوە لە ڕاستەوە" animation was REMOVED on request;
#  users who had it saved fall back to "fade" automatically.)
TEXT_ANIMS = ("fade", "rise", "zoom", "drop")

TEXT_ANIM_LABELS = {
    "fade": "فەیدی نەرم ✨",
    "rise": "سەرکەوتن ⬆️",
    "zoom": "زووم 🔍",
    "drop": "دابەزین لە سەرەوە ⬇️",
}

# Short Kurdish descriptions shown in the animation-picker message.
TEXT_ANIM_DESCRIPTIONS = {
    "fade": "نووسینەکە بە نەرمی و بەرەبەرە دەردەکەوێت",
    "rise": "نووسینەکە لە خوارەوەوە بەرز دەبێتەوە",
    "zoom": "نووسینی ئایەت بە نەرمی دەگەڕێتەوە شوێنی خۆی",
    "drop": "نووسینەکە لە سەرەوەوە بە نەرمی دادەبەزێت",
}


# =========================
# ANIMATION SPEED (3 levels, chosen by the user)
# =========================

# Every fade/motion duration of the entrance animation is multiplied by
# this factor: fast = snappy, medium = default, slow = relaxed.
TEXT_ANIM_SPEEDS = ("slow", "medium", "fast")

TEXT_ANIM_SPEED_LABELS = {
    "slow": "سەبر 🐢",
    "medium": "ناوەند ⏱️",
    "fast": "خێرا ⚡",
}

TEXT_ANIM_SPEED_FACTOR = {
    "slow": 1.7,
    "medium": 1.0,
    "fast": 0.6,
}


def text_anim_label(key):
    return TEXT_ANIM_LABELS.get(str(key), TEXT_ANIM_LABELS["fade"])


def text_anim_speed_label(key):
    return TEXT_ANIM_SPEED_LABELS.get(str(key), TEXT_ANIM_SPEED_LABELS["medium"])


# =========================
# VIDEO SIZE PRESETS (Facebook / TikTok / Instagram / Telegram / Gallery)
# =========================

# The user picks one of these BEFORE the video is built. Every preset
# keeps the full 9:16 frame (no crop -> no text is ever lost):
#   Facebook / TikTok / Instagram / Gallery -> 1080x1920
#   Telegram  -> 720x1280 (light file, sends fast, never hits 413)
#
# budget_mb is the intermediate-file bitrate budget; segments are
# rendered at 720x1280 first (fast) and 1080p presets get one final
# Lanczos upscale pass.
VIDEO_SIZE_PRESETS = {
    "facebook":  {"label": "Facebook",  "width": 1080, "height": 1920, "crf": 22, "budget_mb": 180},
    "tiktok":    {"label": "TikTok",    "width": 1080, "height": 1920, "crf": 21, "budget_mb": 180},
    "instagram": {"label": "Instagram", "width": 1080, "height": 1920, "crf": 21, "budget_mb": 180},
    "telegram":  {"label": "Telegram",  "width": 720,  "height": 1280, "crf": 20, "budget_mb": 45},
    "gallery":   {"label": "Gallery",   "width": 1080, "height": 1920, "crf": 18, "budget_mb": 180},
}
DEFAULT_SIZE_PRESET = "telegram"

# Telegram bots cannot upload more than 50MB per HTTP request (HTTP 413
# Request Entity Too Large). Everything stays safely below that limit.
TELEGRAM_SEND_LIMIT_MB = 48


# =========================
# CONCURRENCY (multiple users at the same time)
# =========================

# How many videos can be built simultaneously. Each render is now
# ~3x faster (single-decode filter graph), so 3 concurrent jobs keep
# the 4-core Actions runner busy without thrashing; extra requests
# wait in a queue and every user still gets an answer immediately.
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "3"))
JOB_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)

# chat_ids that currently have a running video job.
ACTIVE_JOBS = set()
_ACTIVE_JOBS_LOCK = threading.Lock()

# Guards the user_preferences JSON file across threads.
PREFS_LOCK = threading.Lock()


def load_user_preferences():
    with PREFS_LOCK:
        try:
            with open(USER_PREFS_PATH, "r", encoding="utf-8") as file:
                data = json.load(file)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def save_user_preferences(data):
    with PREFS_LOCK:
        temp_path = USER_PREFS_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, USER_PREFS_PATH)


def default_text_settings():
    return {
        "plain_overlay": True,
        "ayah_color": [255, 255, 255],
        "tafsir_color": [255, 210, 90],
        "other_color": [255, 255, 255],
        "ayah_size": 44,
        "tafsir_size": 30,
        "other_size": 24,
        "ayah_y": 250,
        "tafsir_y": 680,
        "other_y": 115,
        # Entrance animation of the ayah + tafsir text (see TEXT_ANIMS).
        "text_anim": "fade",
        # Speed of the entrance animation: slow / medium / fast.
        "text_anim_speed": "medium",
        # ONE switch for the surah name + ONE position choice (top/bottom).
        "show_surah_info": True,
        "surah_info_pos": "bottom",
        "footer_text": "قورئانی پیرۆز - ڕێگای ئیسلام",
        "footer_size": 34,
        "ayah_font": qv.DEFAULT_AYAH_FONT,
        "tafsir_font": qv.DEFAULT_TAFSIR_FONT,
        # Language of the ayah number: "en" (123) or "ku" (١٢٣).
        "number_lang": "en",
        # Show/hide the footer text line (toggle button in the settings menu).
        "show_footer": True,
        # Background darkening percentage 0..100 (set from the settings menu).
        "background_darkness": 0,
    }


def user_preferences(chat_id):
    all_prefs = load_user_preferences()
    return all_prefs.setdefault(str(chat_id), {})


def saved_text_settings(chat_id):
    prefs = user_preferences(chat_id)
    settings = default_text_settings()
    settings.update(prefs.get("text_settings", {}))
    # Keep the transparent text-only overlay so the selected background remains visible.
    settings["plain_overlay"] = True

    # --- Migration: the old two surah-name toggles became ONE switch + position ---
    # (defaults already contain the new keys, so the OLD keys are the signal)
    if "show_header" in settings or "show_footer_info" in settings:
        header = bool(settings.get("show_header", False))
        footer = bool(settings.get("show_footer_info", True))
        settings["show_surah_info"] = header or footer
        settings["surah_info_pos"] = "top" if header else "bottom"
    settings.pop("show_header", None)
    settings.pop("show_footer_info", None)

    position = str(settings.get("surah_info_pos", "bottom")).lower()
    settings["surah_info_pos"] = position if position in ("top", "bottom") else "bottom"
    settings["show_surah_info"] = bool(settings.get("show_surah_info", True))

    # --- Migration: font keys of old versions -> the new fonts/ set ---
    for slot, fallback in (
        ("ayah_font", qv.DEFAULT_AYAH_FONT),
        ("tafsir_font", qv.DEFAULT_TAFSIR_FONT),
    ):
        key = qv.normalize_font_key(settings.get(slot))
        custom_path = settings.get(slot + "_custom")

        if key == qv.CUSTOM_FONT_KEY and custom_path and os.path.isfile(custom_path):
            continue  # uploaded font still exists on the server

        settings[slot] = key if key and key != qv.CUSTOM_FONT_KEY else fallback
        settings.pop(slot + "_custom", None)

    # --- Validate the ayah-number language, animation and background darkness ---
    number_lang = str(settings.get("number_lang", "en") or "en").lower()
    settings["number_lang"] = number_lang if number_lang in qv.NUMBER_LANGS else "en"

    # Text animation: fade / rise / zoom / drop (4 types; old "slide"
    # was removed and falls back to "fade").
    anim = str(settings.get("text_anim", "fade") or "fade").lower()
    if anim == "slide":
        anim = "fade"
    settings["text_anim"] = anim if anim in TEXT_ANIMS else "fade"

    # Animation speed: slow / medium / fast.
    speed = str(settings.get("text_anim_speed", "medium") or "medium").lower()
    settings["text_anim_speed"] = speed if speed in TEXT_ANIM_SPEEDS else "medium"

    try:
        darkness = max(0, min(100, int(settings.get("background_darkness", 0) or 0)))
    except (TypeError, ValueError):
        darkness = 0
    settings["background_darkness"] = darkness

    for key in ("ayah_color", "tafsir_color", "other_color"):
        value = settings.get(key)
        if isinstance(value, list):
            settings[key] = tuple(value)
    return settings


def save_text_settings(chat_id, settings):
    all_prefs = load_user_preferences()
    key = str(chat_id)
    prefs = all_prefs.setdefault(key, {})
    serializable = dict(settings)
    for color_key in ("ayah_color", "tafsir_color", "other_color"):
        value = serializable.get(color_key)
        if isinstance(value, tuple):
            serializable[color_key] = list(value)
    prefs["text_settings"] = serializable
    save_user_preferences(all_prefs)


_MEMBERSHIP_CACHE = {}
_MEMBERSHIP_LOCK = threading.Lock()
_MEMBERSHIP_TTL = 600.0  # seconds a membership answer stays valid


def is_channel_member(user_id):
    """Telegram can reliably check membership when the bot is an admin in the channel.

    Results are cached for 10 minutes per user, so even when MANY users
    press buttons at the same moment (10+ messages per second) the bot
    never floods api.telegram.org with getChatMember calls.
    """
    now = time.time()

    with _MEMBERSHIP_LOCK:
        cached = _MEMBERSHIP_CACHE.get(user_id)

        if cached and now - cached[0] < _MEMBERSHIP_TTL:
            return cached[1]

    try:
        member = telegram("getChatMember", {
            "chat_id": REQUIRED_CHANNEL,
            "user_id": user_id,
        })
        status = member.get("status", "")
        result = status in ("creator", "administrator", "member") or (
            status == "restricted" and bool(member.get("is_member"))
        )
    except Exception as exc:
        log_error("CHANNEL MEMBERSHIP CHECK FAILED | %r" % exc)
        # Network hiccups must not lock members out: trust the last
        # positive answer for one more cache cycle.
        with _MEMBERSHIP_LOCK:
            cached = _MEMBERSHIP_CACHE.get(user_id)
        if cached and cached[1]:
            _MEMBERSHIP_CACHE[user_id] = (now, True)
            return True
        return False

    with _MEMBERSHIP_LOCK:
        _MEMBERSHIP_CACHE[user_id] = (now, result)

    return result


def keyboard_join_channel():
    return {"inline_keyboard": [
        [{"text": "📢 چوونە ناو کەناڵ", "url": "https://t.me/Regayislam_1"}],
        [{"text": "✅ پشکنین", "callback_data": "join:check"}],
    ]}


def keyboard_start():
    """The big green start button so users never have to type /start."""
    return {"inline_keyboard": [
        [{"text": "🚀 دەستپێکردن", "callback_data": "start:go"}],
        [{"text": "ℹ️ ڕێنمایی", "callback_data": "help:show"}],
    ]}


def send_help(chat_id):
    """Kurdish guide message, reachable via /help and the ℹ️ button."""
    send_message(
        chat_id,
        "ℹ️ ڕێنمایی بۆتەکە\n\n"
        "🚀 دەستپێکردن — دەستپێکردنی دروستکردنی ڤیدیۆ\n"
        "✖️ /cancel — هەڵوەشاندنەوەی داواکاری\n\n"
        "هەنگاوەکان:\n"
        "١. قاری هەڵبژێرە 🎙️\n"
        "٢. سوورەت دیاری بکە 📖\n"
        "٣. ژمارەی ئایەتی دەستپێک و کۆتایی بنووسە 🔢\n"
        "٤. پاشبنەما و تەفسیر هەڵبژێرە 🖼️\n"
        "٥. ڕەنگ، فۆنت و ئەنیمەیشن ڕێک بخە ✨\n"
        "٦. قەبارەی ڤیدیۆ هەڵبژێرە (Facebook, TikTok, Instagram, Telegram, گەلەری) 📏\n\n"
        "✨ ئەنیمەیشن (٤ جۆر): فەیدی نەرم، سەرکەوتن، زووم، دابەزین لە سەرەوە\n"
        "⏱️ خێرایی ئەنیمەیشن (٣ جۆر): سەبر، ناوەند، خێرا\n\n"
        "💡 لە مینیووی ڕێکخستندا دوگمەی «🎲 دیزاینی هەڕەمەکی» هەیە "
        "کە بە یەک داگرتن دیزاینێکی جوانی نوێ بۆت دروست دەکات.\n\n"
        "📩 بۆ هەر ڕەخنە و پێشنیارێک ئەتوانن نامە بنێرن: @ismahil_312",
        keyboard_start(),
    )


def require_channel_membership(chat_id, user_id=None):
    user_id = chat_id if user_id is None else user_id
    if is_channel_member(user_id):
        return True
    send_message(
        chat_id,
        "🔒 بۆ بەکارهێنانی بۆتەکە، سەرەتا دەبێت ببیتە ئەندامی کەناڵەکەمان.\n\n"
        "١. بچۆ ناو کەناڵەکە.\n"
        "٢. پاشان دوگمەی «پشکنین» دابگرە.",
        keyboard_join_channel(),
    )
    return False


# =========================
# LOGGING
# =========================

def log_error(message):
    line = (
        time.strftime("%Y-%m-%d %H:%M:%S")
        + " | "
        + str(message)
    )

    print(line, file=sys.stderr, flush=True)

    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception as exc:
        print(
            "Could not write log file: " + repr(exc),
            file=sys.stderr,
            flush=True,
        )


def log_exception(context, exc):
    log_error(
        context + " | " + repr(exc) + "\n"
        + traceback.format_exc()
    )


# =========================
# URL HELPERS
# =========================

def normalize_github_url(url):
    url = str(url).strip()

    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")

        if host == "github.com" and len(parts) >= 5:
            owner, repo = parts[0], parts[1]

            if parts[2] in ("raw", "blob"):
                branch = parts[3]
                file_path = "/".join(parts[4:])

                return (
                    "https://raw.githubusercontent.com/"
                    + owner + "/"
                    + repo + "/"
                    + branch + "/"
                    + file_path
                )

        return url

    except Exception as exc:
        log_error(
            "URL normalization failed | "
            + repr(exc)
            + " | URL="
            + url
        )
        return url


# =========================
# LOAD RECITERS FROM JSON
# =========================

def load_reciters():
    if not os.path.isfile(RECITERS_JSON_PATH):
        raise FileNotFoundError(
            "فایلی reciters.json لە فۆڵدەری بۆتەکە نەدۆزرایەوە: "
            + RECITERS_JSON_PATH
        )

    try:
        with open(
            RECITERS_JSON_PATH, "r", encoding="utf-8"
        ) as f:
            data = json.load(f)

    except json.JSONDecodeError as exc:
        raise ValueError(
            "فایلی reciters.json بە شێوەیەکی دروست JSON نییە: "
            + str(exc)
        )

    if not isinstance(data, list):
        raise ValueError(
            "ناوەڕۆکی reciters.json دەبێت لیستێک بێت."
        )

    reciters = {}

    for item in data:
        if not isinstance(item, dict):
            continue

        enabled = str(item.get("doxx", "")).strip().lower()

        if enabled != "true":
            continue

        reciter_id = str(item.get("id", "")).strip()
        name = str(item.get("name_ar", "")).strip()
        url = str(item.get("url", "")).strip()

        if not reciter_id or not name or not url:
            log_error(
                "Skipping incomplete reciter entry: "
                + repr(item)
            )
            continue

        if not url.startswith(("http://", "https://")):
            log_error(
                "Skipping reciter with invalid URL: "
                + reciter_id
            )
            continue

        url = normalize_github_url(url)

        reciters[reciter_id] = {
            "name": name,
            "base": url.rstrip("/") + "/",
        }

    if not reciters:
        raise ValueError(
            "هیچ قارییەکی دروست بە doxx=true "
            "لە reciters.json نەدۆزرایەوە."
        )

    log_error(
        "Loaded %d reciters from reciters.json."
        % len(reciters)
    )

    return reciters


RECITERS = load_reciters()


# =========================
# TELEGRAM API
# =========================

class TelegramConflict(RuntimeError):
    """Raised when getUpdates returns HTTP 409.

    Telegram only allows ONE poller per bot token. When a newer bot
    instance starts polling (the 24/7 handover), the old instance's
    getUpdates call fails with 409 - that is the signal for the old
    instance to finish its in-flight video jobs and exit gracefully.
    """


def telegram(method, data=None, files=None, timeout=120):
    data = data or {}
    url = TG + method

    if files:
        boundary = "----QuranBotBoundary7MA4YWxkTrZu0gW"
        body = bytearray()

        for key, value in data.items():
            body.extend(("--" + boundary + "\r\n").encode())
            body.extend(
                (
                    'Content-Disposition: form-data; name="%s"\r\n\r\n'
                    % key
                ).encode()
            )
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        for key, (filename, content, mime) in files.items():
            body.extend(("--" + boundary + "\r\n").encode())
            body.extend(
                (
                    'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (key, filename)
                ).encode()
            )
            body.extend(
                ("Content-Type: %s\r\n\r\n" % mime).encode()
            )
            body.extend(content)
            body.extend(b"\r\n")

        body.extend(("--" + boundary + "--\r\n").encode())

        req = urllib.request.Request(
            url,
            data=bytes(body),
            headers={
                "Content-Type": "multipart/form-data; boundary=" + boundary,
            },
        )

    else:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=encoded)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as exc:
        log_exception("Telegram API error: " + method, exc)

        if exc.code == 409:
            raise TelegramConflict(
                "409 Conflict: another bot instance is polling"
            )

        raise RuntimeError(
            "Telegram API error: " + str(exc)
        )

    except Exception as exc:
        log_exception("Telegram API error: " + method, exc)
        raise RuntimeError(
            "Telegram API error: " + str(exc)
        )

    if not result.get("ok"):
        raise RuntimeError(
            result.get("description", "Telegram API request failed")
        )

    return result.get("result")


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup, ensure_ascii=False
        )

    return telegram("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup, ensure_ascii=False
        )

    return telegram("editMessageText", data)


def answer_callback(callback_id, text=""):
    try:
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
            },
        )
    except Exception as exc:
        log_error("answerCallbackQuery failed: " + repr(exc))


def send_video(chat_id, path, caption):
    log_error(
        "Sending video | chat_id=%s | path=%s | size=%s"
        % (
            chat_id,
            path,
            os.path.getsize(path) if os.path.isfile(path) else "missing",
        )
    )

    with open(path, "rb") as f:
        telegram(
            "sendVideo",
            {
                "chat_id": chat_id,
                "caption": caption,
                "supports_streaming": "true",
            },
            {
                "video": (
                    os.path.basename(path),
                    f.read(),
                    "video/mp4",
                )
            },
            timeout=300,
        )


def send_photo(chat_id, path, caption):
    with open(path, "rb") as f:
        telegram(
            "sendPhoto",
            {"chat_id": chat_id, "caption": caption},
            {"photo": (os.path.basename(path), f.read(), "image/png")},
            timeout=180,
        )


def send_background_previews(chat_id, kind):
    """Send previews only on first visit, unless the user explicitly asks again."""
    if kind not in ("image", "video"):
        kind = "image"
    ext = "mp4" if kind == "video" else "png"
    for number in range(1, 11):
        local_path = os.path.join(BACKGROUND_DIR, "%d.%s" % (number, ext))
        if not os.path.isfile(local_path):
            try:
                download_file(BACKGROUND_BASE_URL + "%d.%s" % (number, ext), local_path, min_size=1000)
            except Exception as exc:
                log_error("BACKGROUND PREVIEW UNAVAILABLE | %s | %r" % (local_path, exc))
                send_message(chat_id, "⚠️ فایل %d.%s لە GitHub نییە. دڵنیابە ناوەکە و شوێنی فایلەکە ڕاستە." % (number, ext))
                continue
        try:
            if kind == "video":
                send_video(chat_id, local_path, "🎬 ڤیدیۆی پاشبنەما %d/10" % number)
            else:
                send_photo(chat_id, local_path, "🖼️ وێنەی پاشبنەما %d/10" % number)
        except Exception as exc:
            log_exception("BACKGROUND PREVIEW SEND FAILED", exc)
            send_message(chat_id, "نەتوانرا پێشبینینی فایل %d بنێردرێت." % number)
    all_prefs = load_user_preferences()
    prefs = all_prefs.setdefault(str(chat_id), {})
    prefs["previews_seen_" + kind] = True
    save_user_preferences(all_prefs)
    send_message(chat_id, "⬇️ ژمارەی پاشبنەما هەڵبژێرە. ئەگەر پێویست بوو دەتوانیت پێشبینینەکان دووبارە ببینیت:", keyboard_backgrounds(kind))


# =========================
# QURAN SURAH LIST
# =========================

def get_surahs():
    global _SURAH_CACHE

    if _SURAH_CACHE is None:
        log_error("Fetching surah list from Quran API.")

        data = qv.get_json(QURAN_API + "/surah")

        if not isinstance(data, dict) or data.get("code") != 200:
            raise RuntimeError(
                "نەتوانرا لیستی سوورەتەکان وەربگیرێت."
            )

        _SURAH_CACHE = data["data"]

        log_error(
            "Surah list loaded: %d surahs."
            % len(_SURAH_CACHE)
        )

    return _SURAH_CACHE


def keyboard_reciters():
    rows = []
    items = list(RECITERS.items())

    for i in range(0, len(items), 2):
        row = []

        for reciter_id, reciter in items[i:i + 2]:
            row.append({
                "text": reciter["name"],
                "callback_data": "reciter:" + reciter_id,
            })

        rows.append(row)

    return {"inline_keyboard": rows}


def keyboard_surahs(page=0):
    surahs = get_surahs()
    per_page = 12
    start = page * per_page
    chunk = surahs[start:start + per_page]
    rows = []

    for i in range(0, len(chunk), 2):
        row = []

        for s in chunk[i:i + 2]:
            row.append({
                "text": "%s. %s" % (s["number"], s["name"]),
                "callback_data": "surah:%d" % s["number"],
            })

        rows.append(row)

    nav = []

    if page > 0:
        nav.append({
            "text": "⬅️ پێشوو",
            "callback_data": "page:%d" % (page - 1),
        })

    if start + per_page < len(surahs):
        nav.append({
            "text": "دواتر ➡️",
            "callback_data": "page:%d" % (page + 1),
        })

    if nav:
        rows.append(nav)

    rows.append([
        {
            "text": "🎙️ گۆڕینی قاری",
            "callback_data": "change_reciter",
        }
    ])

    return {"inline_keyboard": rows}


# =========================
# DOWNLOAD FILES WITH RETRIES
# =========================

def download_file(url, output_path, min_size=100):
    urls = []
    normalized = normalize_github_url(url)
    urls.append(normalized)

    if url not in urls:
        urls.append(url)

    last_error = None

    os.makedirs(
        os.path.dirname(output_path) or ".",
        exist_ok=True,
    )

    for current_url in urls:
        for attempt in range(1, 6):
            temp_path = output_path + ".part"

            try:
                log_error(
                    "DOWNLOAD START | attempt=%d/5 | URL=%s"
                    % (attempt, current_url)
                )

                request = urllib.request.Request(
                    current_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 QuranVideoBot/1.0",
                        "Accept": "*/*",
                        "Connection": "close",
                    },
                )

                with urllib.request.urlopen(
                    request,
                    timeout=90,
                ) as response:
                    content = response.read()
                    status = response.getcode()
                    content_type = response.headers.get(
                        "Content-Type", ""
                    )

                if status != 200:
                    raise RuntimeError(
                        "HTTP status: %s" % status
                    )

                if len(content) < min_size:
                    raise RuntimeError(
                        "فایلە وەرگیراوەکە زۆر بچووکە: "
                        + str(len(content))
                        + " bytes"
                    )

                beginning = content[:500].lstrip().lower()

                if (
                    beginning.startswith(b"<!doctype html")
                    or beginning.startswith(b"<html")
                    or b"service temporarily unavailable" in beginning
                    or b"502 bad gateway" in beginning
                    or b"503 service unavailable" in beginning
                ):
                    raise RuntimeError(
                        "سێرڤەر HTML ـی ناردووە لەبری فایل."
                    )

                with open(temp_path, "wb") as f:
                    f.write(content)
                    f.flush()

                os.replace(temp_path, output_path)

                log_error(
                    "DOWNLOAD SUCCESS | URL=%s | bytes=%d | type=%s"
                    % (
                        current_url,
                        len(content),
                        content_type,
                    )
                )

                return output_path

            except urllib.error.HTTPError as exc:
                last_error = exc

                log_error(
                    "DOWNLOAD HTTP ERROR | attempt=%d/5 | "
                    "status=%s | URL=%s | reason=%s"
                    % (
                        attempt,
                        exc.code,
                        current_url,
                        exc.reason,
                    )
                )

            except Exception as exc:
                last_error = exc

                log_exception(
                    "DOWNLOAD FAILED | attempt=%d/5 | URL=%s"
                    % (attempt, current_url),
                    exc,
                )

            finally:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass

            if attempt < 5:
                wait_seconds = min(3 * attempt, 12)

                log_error(
                    "Retrying download in %d seconds."
                    % wait_seconds
                )

                time.sleep(wait_seconds)

    raise RuntimeError(
        "دابەزاندنی فایل سەرکەوتوو نەبوو.\n"
        "URL: " + str(url) + "\n"
        "هەڵەی کۆتایی: " + str(last_error) + "\n"
        "Log: " + LOG_PATH
    )


# =========================
# DOWNLOAD RECITER AUDIO
# =========================

def download_surah_audio(surah_number, reciter_id, output_path):
    reciter = RECITERS.get(str(reciter_id))

    if not reciter:
        raise ValueError("قاریی هەڵبژێردراو دروست نییە.")

    filename = "%03d.mp3" % int(surah_number)
    url = reciter["base"] + filename

    log_error(
        "AUDIO REQUEST | reciter_id=%s | reciter=%s | surah=%s | URL=%s"
        % (
            reciter_id,
            reciter["name"],
            surah_number,
            url,
        )
    )

    try:
        download_file(url, output_path, min_size=1000)

        proc = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                output_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8", errors="replace",
        )

        if proc.returncode != 0:
            log_error(
                "FFPROBE FAILED | URL=%s | stderr=%s"
                % (url, proc.stderr[-1000:])
            )

            raise RuntimeError(
                "فایلی دابەزێنراو MP3 ـێکی دروست نییە."
            )

        duration = float(proc.stdout.strip())

        if duration <= 0:
            raise RuntimeError("ماوەی دەنگەکە دروست نییە.")

        log_error(
            "AUDIO VERIFIED | reciter=%s | surah=%s | duration=%s"
            % (
                reciter["name"],
                surah_number,
                duration,
            )
        )

    except Exception as exc:
        log_exception("Audio download/verification failed", exc)

        raise RuntimeError(
            "نەتوانرا دەنگی سوورەتەکە وەربگیرێت.\n"
            "قاری: %s\n"
            "URL: %s\n"
            "هۆکار: %s"
            % (reciter["name"], url, exc)
        )


# =========================
# AYAH TIMING DATABASE
# =========================

def valid_timings_database(db_path):
    conn = None

    try:
        conn = sqlite3.connect(db_path)

        conn.execute(
            "SELECT sura, ayah, time FROM timings LIMIT 1"
        )

        return True

    except Exception as exc:
        log_error(
            "INVALID TIMINGS DATABASE | path=%s | error=%r"
            % (db_path, exc)
        )
        return False

    finally:
        if conn is not None:
            conn.close()


def download_timings_database(reciter_id):
    os.makedirs(TIMINGS_DIR, exist_ok=True)

    db_path = os.path.join(
        TIMINGS_DIR,
        str(reciter_id) + ".db",
    )

    if os.path.exists(db_path):
        if valid_timings_database(db_path):
            log_error(
                "Using cached timings database: " + db_path
            )
            return db_path

        try:
            os.remove(db_path)
        except OSError as exc:
            log_error(
                "Could not remove invalid timings DB: "
                + repr(exc)
            )

    url = TIMINGS_BASE_URL + str(reciter_id) + ".db"

    log_error(
        "TIMINGS DATABASE REQUEST | ID=%s | URL=%s"
        % (reciter_id, url)
    )

    try:
        download_file(url, db_path, min_size=100)

        if not valid_timings_database(db_path):
            raise RuntimeError(
                "داتابەیسی دابەزێنراو خشتەی timings ـی دروستی تێدا نییە."
            )

        log_error(
            "TIMINGS DATABASE READY | ID=%s | path=%s"
            % (reciter_id, db_path)
        )

        return db_path

    except Exception as exc:
        log_exception(
            "Timings database download failed for ID="
            + str(reciter_id),
            exc,
        )

        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass

        raise RuntimeError(
            "نەتوانرا داتابەیسی کاتی قاری وەربگیرێت.\n"
            "ID: %s\nURL: %s\nهۆکار: %s\nLog: %s"
            % (reciter_id, url, exc, LOG_PATH)
        )


def get_ayah_timings(reciter_id, surah_number):
    db_path = download_timings_database(reciter_id)

    conn = sqlite3.connect(db_path)

    try:
        rows = conn.execute(
            """
            SELECT sura, ayah, time
            FROM timings
            WHERE sura = ?
            ORDER BY ayah
            """,
            (int(surah_number),),
        ).fetchall()

    finally:
        conn.close()

    if not rows:
        raise RuntimeError(
            "هیچ زانیارییەکی timing بۆ ئەم سوورەتە "
            "لە داتابەیسدا نییە."
        )

    result = {}

    for sura, ayah, start_time in rows:
        result[int(ayah)] = float(start_time)

    largest = max(result.values())

    if largest >= 10000:
        result = {
            ayah: value / 1000.0
            for ayah, value in result.items()
        }

    return result


def get_audio_duration(audio_path):
    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8", errors="replace",
    )

    if proc.returncode != 0:
        log_error(
            "GET AUDIO DURATION FAILED | path=%s | stderr=%s"
            % (audio_path, proc.stderr[-1000:])
        )

        raise RuntimeError(
            "نەتوانرا ماوەی فایلە دەنگەکە بخوێندرێتەوە."
        )

    try:
        duration = float(proc.stdout.strip())

    except Exception:
        raise RuntimeError(
            "ماوەی فایلە دەنگەکە دروست نییە."
        )

    if duration <= 0:
        raise RuntimeError("ماوەی دەنگەکە صفرە.")

    return duration


# =========================
# READ AYAH TEXT FROM QURAN DB
# =========================

def find_surah_rows(surah_number, start_ayah, end_ayah):
    if end_ayah < start_ayah:
        raise ValueError(
            "ئایەتی کۆتایی دەبێت گەورەتر یان یەکسان "
            "بێت بە ئایەتی دەستپێک."
        )

    count = end_ayah - start_ayah + 1

    # No fixed maximum ayah count.
    surahs = get_surahs()

    surah = next(
        (
            s for s in surahs
            if int(s["number"]) == int(surah_number)
        ),
        None,
    )

    if not surah:
        raise ValueError("سوورەتەکە نەدۆزرایەوە.")

    if (
        start_ayah < 1
        or end_ayah > int(surah["numberOfAyahs"])
    ):
        raise ValueError(
            "ژمارەی ئایەت دەبێت لە 1 تا %d بێت."
            % int(surah["numberOfAyahs"])
        )

    qv.ensure_database()

    db_path = getattr(
        qv,
        "DB_PATH",
        os.path.join(BASE_DIR, "quran.db"),
    )

    if not os.path.exists(db_path):
        raise RuntimeError(
            "فایلی quran.db نەدۆزرایەوە: " + str(db_path)
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        table_row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND lower(name)=lower(?)",
            ("ayah_text",),
        ).fetchone()

        if not table_row:
            raise RuntimeError(
                "خشتەی ayah_text لە quran.db نەدۆزرایەوە."
            )

        table_name = table_row["name"]

        column_rows = conn.execute(
            'PRAGMA table_info("%s")'
            % table_name.replace('"', '""')
        ).fetchall()

        columns = {
            str(row["name"]).lower(): str(row["name"])
            for row in column_rows
        }

        required = (
            "suraid",
            "sura_name_ar",
            "text",
            "asan",
            "ayah",
        )

        missing = [
            column for column in required
            if column not in columns
        ]

        if missing:
            raise RuntimeError(
                "ستوونی پێویست نەدۆزرایەوە: "
                + ", ".join(missing)
            )

        def quoted(column):
            return '"' + column.replace('"', '""') + '"'

        sql = (
            "SELECT "
            + quoted(columns["sura_name_ar"]) + " AS sura_name, "
            + quoted(columns["ayah"]) + " AS ayah_number, "
            + quoted(columns["text"]) + " AS arabic_text, "
            + quoted(columns["asan"]) + " AS tafsir_text "
            + "FROM " + quoted(table_name) + " "
            + "WHERE CAST("
            + quoted(columns["suraid"])
            + " AS INTEGER)=? "
            + "AND CAST("
            + quoted(columns["ayah"])
            + " AS INTEGER) BETWEEN ? AND ? "
            + "ORDER BY CAST("
            + quoted(columns["ayah"])
            + " AS INTEGER)"
        )

        rows = conn.execute(
            sql,
            (
                int(surah_number),
                int(start_ayah),
                int(end_ayah),
            ),
        ).fetchall()

        if len(rows) != count:
            raise RuntimeError(
                "ئایەتە داواکراوەکان لە داتابەیسدا "
                "بە تەواوی نەدۆزرایەوە."
            )

        result = []

        for row in rows:
            arabic = str(row["arabic_text"] or "").strip()
            tafsir = str(row["tafsir_text"] or "").strip()

            if not arabic:
                raise RuntimeError(
                    "دەقی عەرەبی ئایەت بەتاڵە."
                )

            result.append({
                "surah_number": int(surah_number),
                "surah_name": str(
                    row["sura_name"] or surah["name"]
                ).strip(),
                "ayah_number": int(row["ayah_number"]),
                "arabic": arabic,
                "tafsir": (
                    tafsir
                    or "تەفسیری ئەم ئایەتە بەردەست نییە."
                ),
                "translation": (
                    tafsir
                    or "تەفسیری ئەم ئایەتە بەردەست نییە."
                ),
            })

        return result

    finally:
        conn.close()


# =========================
# VIDEO CREATION
# =========================

def run_ffmpeg(args):
    log_error(
        "FFMPEG START | " + " ".join(str(arg) for arg in args)
    )

    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8", errors="replace",
    )

    if proc.returncode != 0:
        log_error(
            "FFMPEG FAILED | returncode=%s | stderr=%s"
            % (proc.returncode, proc.stderr[-3000:])
        )

        raise RuntimeError(
            "FFmpeg: "
            + (proc.stderr[-1500:] or "هەڵەی نەناسراو")
        )


def get_media_duration(path):
    """Duration of an audio/video file in seconds (0.0 when unknown)."""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        return float(probe.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def _is_too_large_error(exc):
    """True when a Telegram send failed because the file was too big."""
    text = str(exc)
    return (
        "413" in text
        or "Too Large" in text
        or "too large" in text
        or "Request Entity" in text
        or "ENTITY" in text.upper()
    )


def shrink_video_for_send(path, target_mb=40):
    """Hard-cap a video file so a send can never fail with HTTP 413."""
    duration = get_media_duration(path)
    send_kbps = max(
        50,
        int((target_mb * 8 * 1024) / max(0.5, duration)) - 64,
    )

    if path.endswith(".mp4"):
        out = path[:-4] + "_send.mp4"
    else:
        out = path + "_send.mp4"

    run_ffmpeg([
        "ffmpeg", "-y", "-i", path,
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", "%dk" % send_kbps,
        "-maxrate", "%dk" % send_kbps,
        "-bufsize", "%dk" % (send_kbps * 2),
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        out,
    ])

    return out


def _segment_filter_graph(anim, include_tafsir, bg_chain, duration, first_segment=False, speed="medium"):
    """Build the -filter_complex string for one ayah segment.

    Input order: 0=background, 1=info layer (surah name + ayah number +
    footer text), 2=ayah layer, 3=tafsir layer, then the audio input.

    ANIMATION RULES (as requested by the user):
      - ayah layer   -> animated on EVERY ayah
      - tafsir layer -> animated on EVERY ayah
      - info layer   -> animated ONLY on the FIRST ayah segment; after
        that it is drawn instantly (no fade, no motion), so the footer
        text "قورئانی پیرۆز - ڕێگای ئیسلام" and the surah name stay
        perfectly still while the ayah changes.

    Four entrance animations for the text (ayah first, tafsir follows
    just a blink later). Every animation fully settles quickly, so the
    text feels quick and snappy:
      fade  - soft alpha fade-in for both layers
      rise  - both layers gently rise up while fading in
      zoom  - the ayah settles from a slight zoom while fading in
      drop  - both layers descend from above while fading in
    (The old "slide / خاڵێوە لە ڕاستەوە" animation was removed.)

    The SPEED preset (slow / medium / fast) scales every fade and
    motion duration, so the same animation can play relaxed or snappy.

    PERF: the overlay pictures are still images. Instead of "-loop 1"
    (which DECODES the PNG again for EVERY frame) each layer is decoded
    ONCE and the `loop` filter clones the frame in memory, with
    setpts=N/(24*TB) giving the clones a proper 24fps timeline. The
    static info layer on later segments is a plain single frame that
    the overlay filter repeats (eof_action=repeat). This measured ~3x
    faster rendering with pixel-identical output.
    """
    factor = TEXT_ANIM_SPEED_FACTOR.get(str(speed or "medium"), 1.0)

    def D(base):
        """Scale an animation duration by the chosen speed preset."""
        return max(0.08, base * factor)

    zoom_frames = int(duration * 24) + 30  # zoompan output frames (+1s buffer)
    parts = [bg_chain]

    # Decode the still PNG once, then clone its frame in memory at 24fps.
    clone = "loop=loop=-1:size=1:start=0,setpts=N/(24*TB),format=rgba"

    # On the first segment the info layer leads at t=0, the ayah starts
    # 0.15s later and the tafsir another 0.15s after that. On later
    # segments the info layer is already on screen, so the ayah starts
    # at t=0 again (exactly like before).
    ayah_delay = D(0.15) if first_segment else 0.0
    tafsir_delay = D(0.30) if first_segment else D(0.15)

    # --- info layer: animated once on the first segment, then static ---
    if first_segment:
        parts.append(
            "[1:v]%s,fade=t=in:st=0:d=%.2f:alpha=1[ovI];" % (clone, D(0.40))
        )
    else:
        # Static: one decoded frame, repeated for the whole segment.
        parts.append("[1:v]format=rgba[ovI];")

    info_merge = "[bg][ovI]overlay=x=0:y=0:eof_action=repeat[bg0];"

    if anim == "rise":
        parts.append(
            "[2:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovA];"
            % (clone, ayah_delay, D(0.35))
        )
        ayah_merge = (
            "[bg0][ovA]overlay=x=0"
            ":y='pow(1-min(max(t-%.2f,0)/%.2f,1),3)*80':eval=frame"
            % (ayah_delay, D(0.45))
        )
        tafsir_chain = (
            "[3:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovB];"
            % (clone, tafsir_delay, D(0.35))
        )
        tafsir_merge = (
            "[o1][ovB]overlay=x=0"
            ":y='pow(1-min(max(t-%.2f,0)/%.2f,1),3)*70'"
            ":eval=frame" % (tafsir_delay, D(0.45))
        )
    elif anim == "zoom":
        # zoompan itself generates the movement frames from ONE decoded
        # picture; only the settle speed follows the chosen preset.
        zoom_settle = max(2, int(round(14 * factor)))
        parts.append(
            "[2:v]format=rgba,"
            "zoompan=z='1.08-0.08*min(on/%d,1)'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            ":d=%d:s=720x1280:fps=24,format=rgba,"
            "fade=t=in:st=%.2f:d=%.2f:alpha=1[ovA];"
            % (zoom_settle, zoom_frames, ayah_delay, D(0.30))
        )
        ayah_merge = "[bg0][ovA]overlay=x=0:y=0"
        tafsir_chain = (
            "[3:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovB];"
            % (clone, tafsir_delay, D(0.40))
        )
        tafsir_merge = "[o1][ovB]overlay=x=0:y=0"
    elif anim == "drop":
        # Descend softly from above, like the mirror of "rise".
        parts.append(
            "[2:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovA];"
            % (clone, ayah_delay, D(0.35))
        )
        ayah_merge = (
            "[bg0][ovA]overlay=x=0"
            ":y='-pow(1-min(max(t-%.2f,0)/%.2f,1),3)*90':eval=frame"
            % (ayah_delay, D(0.45))
        )
        tafsir_chain = (
            "[3:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovB];"
            % (clone, tafsir_delay, D(0.35))
        )
        tafsir_merge = (
            "[o1][ovB]overlay=x=0"
            ":y='-pow(1-min(max(t-%.2f,0)/%.2f,1),3)*70'"
            ":eval=frame" % (tafsir_delay, D(0.45))
        )
    else:  # fade (default)
        parts.append(
            "[2:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovA];"
            % (clone, ayah_delay, D(0.40))
        )
        ayah_merge = "[bg0][ovA]overlay=x=0:y=0"
        tafsir_chain = (
            "[3:v]%s,fade=t=in:st=%.2f:d=%.2f:alpha=1[ovB];"
            % (clone, tafsir_delay, D(0.40))
        )
        tafsir_merge = "[o1][ovB]overlay=x=0:y=0"

    if include_tafsir:
        parts.append(tafsir_chain)
        parts.append(info_merge)
        parts.append(ayah_merge + "[o1];")
        parts.append(tafsir_merge + ":shortest=1[v]")
    else:
        parts.append(info_merge)
        parts.append(ayah_merge + ":shortest=1[v]")

    return "".join(parts)


def build_video(chat_id, ayahs, reciter_id, background_path=None, background_kind="image", background_darkness=0, include_tafsir=True, text_settings=None, progress=None, size_preset=None):
    stamp = str(int(time.time()))

    job_dir = os.path.join(
        JOBS_DIR,
        str(chat_id),
        stamp,
    )

    os.makedirs(job_dir, exist_ok=True)

    log_error(
        "VIDEO JOB START | chat_id=%s | reciter_id=%s | size=%s | job_dir=%s"
        % (chat_id, reciter_id, size_preset, job_dir)
    )

    def report(percent, label=""):
        if progress is None:
            return
        try:
            progress(int(percent), label)
        except Exception as exc:
            log_error("PROGRESS CALLBACK FAILED | %r" % exc)

    # Which of the 4 text animations should play.
    text_anim = str((text_settings or {}).get("text_anim", "fade") or "fade").lower()
    if text_anim not in TEXT_ANIMS:
        text_anim = "fade"

    # Which animation speed preset (slow / medium / fast) should play.
    text_anim_speed = str(
        (text_settings or {}).get("text_anim_speed", "medium") or "medium"
    ).lower()
    if text_anim_speed not in TEXT_ANIM_SPEEDS:
        text_anim_speed = "medium"

    # Which size preset (Facebook / TikTok / Instagram / Telegram / Gallery).
    preset = VIDEO_SIZE_PRESETS.get(
        str(size_preset or "").strip().lower(),
        VIDEO_SIZE_PRESETS[DEFAULT_SIZE_PRESET],
    )

    try:
        surah_number = int(ayahs[0]["surah_number"])

        full_audio_path = os.path.join(
            job_dir,
            "%03d.mp3" % surah_number,
        )

        report(3, "داگرتنی دەنگی قاری...")

        download_surah_audio(
            surah_number,
            reciter_id,
            full_audio_path,
        )

        report(9, "خوێندنەوەی کاتەکانی ئایەتەکان...")

        timings = get_ayah_timings(
            reciter_id,
            surah_number,
        )

        audio_duration = get_audio_duration(
            full_audio_path
        )

        # Compute every ayah time window first, so we know the total
        # duration and can keep the background video continuous.
        segments = []
        background_offset = 0.0

        for i, ayah in enumerate(ayahs, 1):
            ayah_number = int(ayah["ayah_number"])

            if ayah_number not in timings:
                raise RuntimeError(
                    "کاتی ئایەتی %d لە داتابەیسدا نییە."
                    % ayah_number
                )

            start_time = timings[ayah_number]

            if ayah_number + 1 in timings:
                end_time = timings[ayah_number + 1]
            else:
                end_time = audio_duration

            if end_time <= start_time:
                raise RuntimeError(
                    "کاتی timing بۆ ئایەتی %d دروست نییە."
                    % ayah_number
                )

            end_time = min(end_time, audio_duration)
            duration = end_time - start_time

            if (
                start_time < 0
                or start_time >= audio_duration
                or duration <= 0
            ):
                raise RuntimeError(
                    "کاتی ئایەتی %d لە سنووری دەنگەکەدا نییە."
                    % ayah_number
                )

            segments.append({
                "ayah": ayah,
                "index": i,
                "start": start_time,
                "duration": duration,
                "bg_offset": background_offset,
            })

            background_offset += duration

        total_duration = background_offset
        darkness_value = max(0, min(100, int(background_darkness)))
        brightness = -darkness_value / 100.0

        audio_kbps = 64

        # ---- Bitrate budget ----
        # For the Telegram preset (and any final send) bots can upload at
        # most 50MB per HTTP request (HTTP 413). Budget the bitrate so the
        # final file stays safely below that limit. Bigger presets (Gallery,
        # TikTok, ...) get a generous budget: their full-quality file may
        # exceed 50MB and is then auto-compressed into a sendable copy.
        budget_mb = float(preset.get("budget_mb", 45))
        budget_kbps = (budget_mb * 8 * 1024) / max(1.0, total_duration)
        video_max_kbps = max(80, int(budget_kbps) - audio_kbps)

        # ---- SPEED: pre-render the STATIC background ONCE ----
        # The scale/crop/eq/vignette chain used to run on EVERY frame of
        # EVERY segment; now it runs a single time and every segment
        # reuses the finished 720x1280 picture. This is the biggest
        # rendering speed-up for image and black backgrounds.
        background_full = None
        bg_static = None

        if background_kind == "video":
            if not background_path or not os.path.isfile(background_path):
                raise RuntimeError("Background ـی هەڵبژێردراو نەدۆزرایەوە.")

            report(14, "ئامادەکردنی پاشبنەمای ڤیدیۆیی...")

            background_full = os.path.join(job_dir, "background_full.mp4")

            run_ffmpeg([
                "ffmpeg", "-y",
                "-stream_loop", "-1",
                "-i", background_path,
                "-t", "%.3f" % total_duration,
                "-vf",
                "scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280,"
                "eq=brightness=%.2f:saturation=1.14:contrast=1.05,"
                "vignette=a=PI/5.5,format=yuv420p" % brightness,
                "-r", "24",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-an",
                background_full,
            ])
        else:
            report(14, "ئامادەکردنی پاشبنەما...")

            bg_static = os.path.join(job_dir, "bg_static.png")

            if background_kind == "black":
                Image.new(
                    "RGB", (qv.WIDTH, qv.HEIGHT), (0, 0, 0)
                ).save(bg_static)
            else:
                if not background_path or not os.path.isfile(background_path):
                    raise RuntimeError("Background ـی هەڵبژێردراو نەدۆزرایەوە.")

                # One single frame -> the whole look (scale, crop,
                # colour lift, vignette, darkness) is baked in once.
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", background_path,
                    "-vf",
                    "scale=720:1280:force_original_aspect_ratio=increase,"
                    "crop=720:1280,"
                    "eq=brightness=%.2f:saturation=1.14:contrast=1.05,"
                    "vignette=a=PI/5.5,format=rgb24" % brightness,
                    "-frames:v", "1",
                    bg_static,
                ])

        clips = []
        total_segments = max(1, len(segments))

        for segment in segments:
            i = segment["index"]
            ayah = segment["ayah"]
            duration = segment["duration"]

            report(
                20 + 60 * (i - 1) // total_segments,
                "دروستکردنی بەشی %d لە %d (ئایەتی %s)..."
                % (i, total_segments, ayah["ayah_number"]),
            )

            # The ayah, the tafsir and the info labels are rendered as
            # THREE transparent layers, so they can be animated
            # independently (info only on the first ayah).
            info_png = os.path.join(
                job_dir,
                "info_%03d.png" % i,
            )

            ayah_png = os.path.join(
                job_dir,
                "ayah_%03d.png" % i,
            )

            tafsir_png = os.path.join(
                job_dir,
                "tafsir_%03d.png" % i,
            )

            clip_path = os.path.join(
                job_dir,
                "clip_%03d.mp4" % i,
            )

            qv.create_overlay_parts(
                ayah,
                info_png,
                ayah_png,
                tafsir_png,
                include_tafsir=include_tafsir,
                settings=text_settings or {},
            )

            if background_kind == "video":
                # Video backgrounds continue the shared pre-rendered
                # track exactly where the previous ayah stopped
                # (no restart between ayahs).
                bg_input = [
                    "-ss", "%.3f" % segment["bg_offset"],
                    "-i", background_full,
                ]
                bg_chain = "[0:v]null[bg];"
            else:
                # Pre-rendered static picture: decoded ONCE, then the
                # loop filter clones the frame in memory at 24fps
                # (a "-loop 1" input would re-decode the PNG for every
                # single frame — that was the biggest slowdown).
                bg_input = ["-i", bg_static]
                bg_chain = (
                    "[0:v]loop=loop=-1:size=1:start=0,setpts=N/(24*TB),"
                    "format=rgba[bg];"
                )

            # Overlay inputs: 1=info, 2=ayah, 3=tafsir. Each overlay is
            # a STILL image decoded once per segment; the filter graph
            # clones its frames in memory (loop filter / eof repeat).
            # The zoom animation feeds zoompan a single frame (zoompan
            # generates the movement frames itself).
            overlay_inputs = ["-i", info_png, "-i", ayah_png]

            if include_tafsir:
                overlay_inputs += ["-i", tafsir_png]

            # bg(1 input) + info + ayah + tafsir(optional) -> audio index
            # inputs: 0=bg, 1=info, 2=ayah, 3=tafsir(optional), audio=next
            audio_index = 4 if include_tafsir else 3

            ffmpeg_args = ["ffmpeg", "-y"] + bg_input + overlay_inputs + [
                "-ss", "%.3f" % segment["start"],
                "-i", full_audio_path,
                "-filter_complex",
                _segment_filter_graph(
                    text_anim, include_tafsir, bg_chain, duration,
                    first_segment=(i == 1),
                    speed=text_anim_speed,
                ),
                "-map", "[v]", "-map", "%d:a:0" % audio_index,
                "-t", "%.3f" % duration,
                "-c:v", "libx264", "-preset", "superfast",
                "-crf", "20",
                "-maxrate", "%dk" % video_max_kbps,
                "-bufsize", "%dk" % (video_max_kbps * 2),
                "-c:a", "aac", "-b:a", "%dk" % audio_kbps, "-ac", "1",
                "-pix_fmt", "yuv420p", "-shortest",
                "-movflags", "+faststart", clip_path,
            ]
            run_ffmpeg(ffmpeg_args)

            clips.append(clip_path)

            report(20 + 60 * i // total_segments, "")

        report(84, "یەکخستنی بەشەکانی ڤیدیۆ...")

        list_path = os.path.join(
            job_dir,
            "clips.txt",
        )

        with open(
            list_path, "w", encoding="utf-8"
        ) as f:
            for clip in clips:
                safe_clip = clip.replace("'", "'\\''")
                f.write("file '%s'\n" % safe_clip)

        base_path = os.path.join(
            job_dir,
            "quran_video_base.mp4",
        )

        run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            base_path,
        ])

        if (
            not os.path.isfile(base_path)
            or os.path.getsize(base_path) == 0
        ):
            raise RuntimeError(
                "فایلی ڤیدیۆی کۆتایی دروست نەکرا."
            )

        # ---- Final quality pass for the chosen size ----
        # Segments are rendered fast at 720x1280; 1080p presets get ONE
        # Lanczos upscale + encode pass with the preset quality.
        # superfast keeps this pass quick while the preset CRF still
        # controls the final file size / quality.
        output = os.path.join(job_dir, "quran_video.mp4")

        if (preset["width"], preset["height"]) != (qv.WIDTH, qv.HEIGHT):
            report(86, "ئامادەکردنی کوالیتی %s..." % preset["label"])

            run_ffmpeg([
                "ffmpeg", "-y",
                "-i", base_path,
                "-vf", "scale=%d:%d:flags=lanczos" % (preset["width"], preset["height"]),
                "-c:v", "libx264", "-preset", "superfast",
                "-crf", str(preset["crf"]),
                "-c:a", "copy",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                output,
            ])
        else:
            os.replace(base_path, output)

        # ---- Last safety net: Telegram 50MB upload limit (HTTP 413) ----
        # If the final file is too big for a bot upload, build a smaller
        # sendable copy in a few quality steps; the note tells the user.
        send_note = None

        if os.path.getsize(output) > TELEGRAM_SEND_LIMIT_MB * 1024 * 1024:
            report(90, "بچووککردنەوەی ڤیدیۆ بۆ ناردن...")
            original_mb = os.path.getsize(output) / (1024.0 * 1024.0)
            shrunk = None

            for target_mb in (46, 40, 32, 24, 16):
                candidate = os.path.join(job_dir, "send_%dmb.mp4" % target_mb)
                send_kbps = max(
                    50,
                    int((target_mb * 8 * 1024) / max(1.0, total_duration)) - audio_kbps,
                )

                shrink_args = ["ffmpeg", "-y", "-i", output]

                if send_kbps < 1500 and preset["width"] > qv.WIDTH:
                    shrink_args += [
                        "-vf", "scale=%d:%d:flags=lanczos" % (qv.WIDTH, qv.HEIGHT),
                    ]

                shrink_args += [
                    "-c:v", "libx264", "-preset", "superfast",
                    "-b:v", "%dk" % send_kbps,
                    "-maxrate", "%dk" % send_kbps,
                    "-bufsize", "%dk" % (send_kbps * 2),
                    "-c:a", "copy",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    candidate,
                ]
                run_ffmpeg(shrink_args)
                shrunk = candidate

                if os.path.getsize(candidate) <= TELEGRAM_SEND_LIMIT_MB * 1024 * 1024:
                    break

            final_mb = os.path.getsize(shrunk) / (1024.0 * 1024.0)
            send_note = (
                "ℹ️ ڤیدیۆی ڕەسەن %.0fMB بوو و گەورەترە لە سنووری ناردنی تێلەگرام "
                "(50MB)، بۆیە وەشانی بچووککراوە (%.0fMB) نێردرا بۆ تۆ." % (original_mb, final_mb)
            )
            os.replace(shrunk, output)

        report(92, "ڤیدیۆکە ئامادەیە")

        log_error(
            "VIDEO JOB SUCCESS | output=%s | size=%s | preset=%s"
            % (output, os.path.getsize(output), preset["label"])
        )

        return output, job_dir, send_note

    except Exception as exc:
        log_exception("VIDEO JOB FAILED", exc)
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def keyboard_output_modes():
    return {"inline_keyboard": [
        [{"text": "🖼️ ١. پاشبنەمای وێنە", "callback_data": "mode:image"}],
        [{"text": "🎬 ٢. پاشبنەمای ڤیدیۆیی سروشت", "callback_data": "mode:video"}],
        [{"text": "⚫ ٣. پاشبنەمای ڕەشی تەواو", "callback_data": "mode:black"}],
    ]}


def keyboard_backgrounds(kind):
    if kind == "video":
        rows = []
        for number in range(1, 11):
            rows.append([{"text": "🎬 ڤیدیۆی سروشت %d" % number,
                          "callback_data": "background:video:%d" % number}])
        rows.append([{"text": "📱 هەڵبژاردن لە گەلەری مۆبایل", "callback_data": "gallery:video"}])
        rows.append([{"text": "👁️ دووبارە پیشاندانی ١٠ ڤیدیۆ", "callback_data": "previews:video"}])
        return {"inline_keyboard": rows}
    # Image backgrounds: 1.png..10.png in the repository.
    rows = []
    for number in range(1, 11):
        rows.append([{"text": "🖼️ پاشبنەمای %d" % number,
                      "callback_data": "background:image:%d" % number}])
    rows.append([{"text": "📱 هەڵبژاردن لە گەلەری مۆبایل", "callback_data": "gallery:image"}])
    rows.append([{"text": "👁️ دووبارە پیشاندانی ١٠ پاشبنەما", "callback_data": "previews:image"}])
    return {"inline_keyboard": rows}


# =========================
# RANDOM DESIGN (🎲 one-tap beautiful random theme)
# =========================

# Curated color themes: every theme pairs a soft near-white ayah color with
# a matching accent, so any random pick still looks professional.
RANDOM_THEMES = [
    {"name": "زەرداوی شاهانە", "ayah": (255, 255, 255), "tafsir": (255, 210, 90), "other": (255, 224, 150)},
    {"name": "سەوزی ئاسمانی", "ayah": (240, 255, 245), "tafsir": (130, 255, 170), "other": (190, 255, 215)},
    {"name": "شینی ئاسمان", "ayah": (245, 250, 255), "tafsir": (130, 200, 255), "other": (200, 225, 255)},
    {"name": "پەمەیی گوڵ", "ayah": (255, 245, 250), "tafsir": (255, 145, 190), "other": (255, 205, 225)},
    {"name": "مۆری شەو", "ayah": (250, 245, 255), "tafsir": (205, 160, 255), "other": (230, 205, 255)},
    {"name": "پرتەقاڵی ئاوارە", "ayah": (255, 250, 240), "tafsir": (255, 165, 80), "other": (255, 215, 170)},
    {"name": "فیرووزەیی", "ayah": (240, 255, 254), "tafsir": (100, 240, 230), "other": (185, 245, 240)},
    {"name": "زەیتوونی", "ayah": (250, 252, 240), "tafsir": (190, 220, 120), "other": (220, 235, 175)},
    {"name": "ئاڵتوونی", "ayah": (255, 235, 190), "tafsir": (255, 205, 110), "other": (255, 220, 160)},
    {"name": "سپیی پاک", "ayah": (255, 255, 255), "tafsir": (235, 235, 235), "other": (255, 255, 255)},
]

# Number of image backgrounds available in the repository (1.png..10.png).
BACKGROUND_COUNT = 10


def resolve_background(kind, number):
    """Local path of background number, downloading from GitHub if needed.

    Two kinds exist: "image" (1.png..10.png) and "video" (1.mp4..10.mp4)
    — 10 beautiful nature video backgrounds.
    """
    if kind not in ("image", "video") or number not in range(1, BACKGROUND_COUNT + 1):
        raise ValueError("پاشبنەماکە دروست نییە.")
    ext = "png" if kind == "image" else "mp4"
    local_path = os.path.join(BACKGROUND_DIR, "%d.%s" % (number, ext))
    if not os.path.isfile(local_path):
        url = BACKGROUND_BASE_URL + "%d.%s" % (number, ext)
        try:
            download_file(url, local_path, min_size=1000)
        except Exception as exc:
            raise RuntimeError(
                "فایلی %d.%s لە GitHub نەدۆزرایەوە. دڵنیابە فایلەکە لە ڕەگی repository ـەکەت بارکراوە. %s"
                % (number, ext, exc)
            )
    return local_path


def apply_random_design(chat_id, session):
    """Randomize colors, sizes, positions, fonts and the background.

    Every call produces a fresh combination; the curated themes keep the
    result beautiful no matter which one is picked.
    """
    theme = random.choice(RANDOM_THEMES)
    settings = session.setdefault("text_settings", default_text_settings())

    settings["ayah_color"] = theme["ayah"]
    settings["tafsir_color"] = theme["tafsir"]
    settings["other_color"] = theme["other"]
    settings["ayah_size"] = random.randint(38, 52)
    settings["tafsir_size"] = random.randint(24, 32)
    settings["other_size"] = random.randint(20, 27)
    settings["footer_size"] = random.randint(28, 38)
    settings["ayah_y"] = random.choice([190, 220, 250, 280, 310])
    settings["tafsir_y"] = random.choice([640, 670, 700, 730])
    settings["ayah_font"] = random.choice(["quran1", "quran2", "quran3"])
    settings["tafsir_font"] = random.choice(["rabar1", "rabar2", "rabar3"])
    settings["text_anim"] = random.choice(TEXT_ANIMS)
    settings["text_anim_speed"] = random.choice(TEXT_ANIM_SPEEDS)
    settings["background_darkness"] = random.randint(15, 45)

    # Pick a random background (image or nature video, 1..10) for the
    # user, so they do not have to choose anything at all.
    bg_note = ""
    number = random.randint(1, BACKGROUND_COUNT)
    kind = random.choice(("image", "video"))
    try:
        session["background_path"] = resolve_background(kind, number)
        session["background_kind"] = kind
        icon = "🖼️ وێنە" if kind == "image" else "🎬 ڤیدیۆی سروشت"
        bg_note = " • پاشبنەما: %s %d • ئەنیمەیشن: %s" % (
            icon,
            number,
            text_anim_label(settings.get("text_anim", "fade")),
        )
    except Exception as exc:
        log_error("RANDOM BACKGROUND FAILED | %r" % exc)
        bg_note = " • پاشبنەمای پێشوو هەڵگیرا"

    return theme["name"], bg_note


def keyboard_tafsir():
    return {"inline_keyboard": [
        [{"text": "📖 لەگەڵ تەفسیر", "callback_data": "tafsir:on"}],
        [{"text": "🕋 تەنها ئایەت، بێ تەفسیر", "callback_data": "tafsir:off"}],
    ]}


def keyboard_video_size(current=None):
    """Size/quality picker shown BEFORE the video is built.

    Each platform gets its ideal resolution; the last choice of the
    user is marked with a check mark (defaults to Telegram).
    """
    current = str(current or DEFAULT_SIZE_PRESET)

    def mark(key):
        return " ✔" if key == current else ""

    return {"inline_keyboard": [
        [{"text": "📘 Facebook — 1080×1920" + mark("facebook"), "callback_data": "size:facebook"}],
        [{"text": "🎵 TikTok — 1080×1920" + mark("tiktok"), "callback_data": "size:tiktok"}],
        [{"text": "📸 Instagram — 1080×1920" + mark("instagram"), "callback_data": "size:instagram"}],
        [{"text": "✈️ Telegram — 720×1280 (خێرا و سووک)" + mark("telegram"), "callback_data": "size:telegram"}],
        [{"text": "📱 گەلەری — 1080×1920 (باشترین کوالیتی)" + mark("gallery"), "callback_data": "size:gallery"}],
        [{"text": "↩️ گەڕانەوە بۆ ڕێکخستن", "callback_data": "settings:back"}],
    ]}



def color_label(value):
    colors = {
        (255, 255, 255): "سپی",
        (255, 210, 90): "زەرد",
        (130, 255, 170): "سەوز",
        (130, 200, 255): "شین",
        (255, 145, 190): "پەمەیی",
        (205, 160, 255): "مۆر",
        (255, 165, 80): "پرتەقاڵی",
        (100, 240, 230): "فیرووزەیی",
        (255, 90, 90): "سوور",
        (190, 220, 120): "زەیتوونی",
        (230, 230, 230): "خۆڵەمێشی ڕوون",
        (20, 20, 20): "ڕەش",
    }
    return colors.get(tuple(value) if isinstance(value, (list, tuple)) else value, "تایبەت")


# =========================
# FONT SELECTION (3 Quran fonts + 3 text styles + your own font)
# =========================

# Kurdish labels of the font options, exactly as the user sees them:
#   ayah_font  ->  فۆنتی:  قورئان ١ / قورئان ٢ / قورئان ٣ / خۆت فۆنت هەڵبژێرە
#   tafsir_font -> فۆنتی:  نووسین ١ / نووسین ٢ / نووسین ٣ / خۆت فۆنت هەڵبژێرە
FONT_OPTIONS = {
    "ayah_font": [
        ("quran1", "قورئان ١"),
        ("quran2", "قورئان ٢"),
        ("quran3", "قورئان ٣"),
    ],
    "tafsir_font": [
        ("rabar1", "نووسین ١"),
        ("rabar2", "نووسین ٢"),
        ("rabar3", "نووسین ٣"),
    ],
}

CUSTOM_FONT_BUTTON = "📤 خۆت فۆنت هەڵبژێرە"
CUSTOM_FONT_LABEL = "فۆنتی خۆم"


def font_value_label(setting, key):
    """Kurdish name of the currently selected font (for the settings row)."""
    default = "قورئان ١" if setting == "ayah_font" else "نووسین ١"

    if key == qv.CUSTOM_FONT_KEY:
        return CUSTOM_FONT_LABEL

    normalized = qv.normalize_font_key(key)
    if not normalized:
        return default

    name = qv.FONT_FILES[normalized]["name"]

    # Never show a Quran font name inside the tafsir slot (or vice versa).
    if setting == "ayah_font" and not name.startswith("قورئان"):
        return default
    if setting == "tafsir_font" and not name.startswith("نووسین"):
        return default

    return name


def keyboard_fonts(setting, current=None):
    """Keyboard: 3 ready-made fonts + 'choose your own font' (upload)."""
    rows = []

    for key, label in FONT_OPTIONS.get(setting, []):
        mark = " ✔" if current == key else ""
        rows.append([{
            "text": "🔤 " + label + mark,
            "callback_data": "setval:%s:%s" % (setting, key),
        }])

    mark = " ✔" if current == qv.CUSTOM_FONT_KEY else ""
    rows.append([{
        "text": CUSTOM_FONT_BUTTON + mark,
        "callback_data": "setval:%s:%s" % (setting, qv.CUSTOM_FONT_KEY),
    }])

    rows.append([{"text": "↩️ گەڕانەوە بۆ ڕێکخستن", "callback_data": "settings:back"}])
    return {"inline_keyboard": rows}


def validate_font_file(path, require_kurdish):
    """Check that an uploaded file really is a usable font.

    Returns the list of missing letters (may be empty), or None when the
    fontTools library is not installed and the check has to be skipped.
    """
    size = os.path.getsize(path)

    if size > 10 * 1024 * 1024:
        raise RuntimeError("قەبارەی فۆنت زۆر گەورەیە (سنوور: 10MB).")

    try:
        ImageFont.truetype(path, 40)
    except Exception:
        raise RuntimeError(
            "ئەم فایلە فۆنتی دروست نییە. تکایە فایلی TTF یان OTF بنێرە."
        )

    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None  # library missing -> accept the font without glyph check

    try:
        font = TTFont(path, fontNumber=0, lazy=True)
        cmap = font.getBestCmap() or {}
        font.close()
    except Exception:
        return None

    if require_kurdish:
        # Kurdish-specific letters: the tafsir text cannot be rendered without them.
        required = "ڕڵۆێپچژگڤ"
    else:
        # Basic Arabic letters for the ayah font.
        required = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"

    return [char for char in required if ord(char) not in cmap]


SETTING_LABELS = {
    "ayah_color": "ڕەنگی ئایەت",
    "tafsir_color": "ڕەنگی تەفسیر",
    "ayah_size": "قەبارەی ئایەت",
    "tafsir_size": "قەبارەی تەفسیر",
    "other_size": "قەبارەی ناو و ژمارە",
    "footer_size": "قەبارەی دەقی خوارەوە",
    "ayah_y": "شوێنی ئایەت",
    "tafsir_y": "شوێنی تەفسیر",
    "ayah_font": "فۆنتی",
    "tafsir_font": "فۆنتی",
    "number_lang": "ژمارەی ئایەت",
    "text_anim": "ئەنیمەیشنی نووسین",
    "text_anim_speed": "خێرایی ئەنیمەیشن",
}


# =========================
# SETTINGS LIVE PREVIEW
# =========================

PREVIEW_DIR = os.path.join(JOBS_DIR, "previews")

SAMPLE_AYAH = {
    "surah_number": 94,
    "surah_name": "سورەتی الشرح",
    "ayah_number": 6,
    "arabic": "إِنَّ مَعَ الْعُسْرِ يُسْرًا",
    "tafsir": "بێگومان لەگەڵ هەموو سەختییەکەوە ئاسانییەک هەیە. ئەمە نموونەیەکە بۆ پیشاندانی شێوازی ڕێکخستنەکانت.",
}


def _preview_base_image(session, cache_key):
    """720x1280 PIL image of the chosen background, darkened like the video."""
    kind = session.get("background_kind", "image")
    # Darkness lives in text_settings now (the 🌑 button in the settings menu).
    darkness = (session.get("text_settings") or {}).get(
        "background_darkness", session.get("background_darkness", 0)
    )
    darkness = max(0, min(100, int(darkness or 0)))

    if kind == "black":
        base = Image.new("RGB", (qv.WIDTH, qv.HEIGHT), (0, 0, 0))
    elif kind == "video":
        path = session.get("background_path")
        if not path or not os.path.isfile(path):
            base = Image.new("RGB", (qv.WIDTH, qv.HEIGHT), (10, 10, 10))
        else:
            os.makedirs(PREVIEW_DIR, exist_ok=True)
            frame_path = os.path.join(PREVIEW_DIR, "frame_%s.png" % cache_key)
            if not os.path.isfile(frame_path):
                subprocess.run(
                    [
                        "ffmpeg", "-y",
                        "-ss", "1",
                        "-i", path,
                        "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280",
                        "-frames:v", "1",
                        frame_path,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                )
            base = Image.open(frame_path).convert("RGB")
    else:
        path = session.get("background_path")
        if not path or not os.path.isfile(path):
            base = Image.new("RGB", (qv.WIDTH, qv.HEIGHT), (10, 10, 10))
        else:
            base = Image.open(path).convert("RGB")
            base = ImageOps.fit(base, (qv.WIDTH, qv.HEIGHT))

    if darkness > 0:
        offset = int(255 * darkness / 100.0)
        base = base.point(lambda value: max(0, value - offset))

    return base


def build_settings_preview(session, chat_id):
    """Render a sample ayah with the user's current settings on their background."""
    settings = session.get("text_settings") or saved_text_settings(chat_id)
    include_tafsir = bool(session.get("include_tafsir", True))

    os.makedirs(PREVIEW_DIR, exist_ok=True)

    overlay_path = os.path.join(PREVIEW_DIR, "overlay_%s.png" % chat_id)

    qv.create_ayah_image(
        SAMPLE_AYAH, 1, overlay_path,
        include_tafsir=include_tafsir,
        settings=settings,
    )

    raw_key = (
        str(session.get("background_kind", "image"))
        + "|"
        + str(session.get("background_path", ""))
    )
    cache_key = hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:12]

    try:
        base = _preview_base_image(session, cache_key).convert("RGBA")
        overlay = Image.open(overlay_path).convert("RGBA")
        base.alpha_composite(overlay)
        preview_path = os.path.join(PREVIEW_DIR, "preview_%s.png" % chat_id)
        base.convert("RGB").save(preview_path)
        return preview_path
    finally:
        try:
            os.remove(overlay_path)
        except OSError:
            pass


def send_settings_preview(chat_id, session):
    """Send the live preview image; never breaks the settings flow."""
    preview_path = None

    try:
        preview_path = build_settings_preview(session, chat_id)
        send_photo(
            chat_id,
            preview_path,
            "👁️ ئەمە شێوازی پێشبینیکراوەی ڕێکخستنەکانتە "
            "(✨ ئەنیمەیشنەکە لە کاتی کارکردنی ڤیدیۆدا دەردەکەوێت).",
        )
    except Exception as exc:
        log_exception("SETTINGS PREVIEW FAILED | chat_id=%s" % chat_id, exc)
    finally:
        if preview_path:
            try:
                os.remove(preview_path)
            except OSError:
                pass


SETVAL_ALLOWED = {
    "ayah_color", "tafsir_color", "ayah_size", "tafsir_size",
    "other_size", "ayah_y", "tafsir_y", "footer_size",
    "ayah_font", "tafsir_font", "number_lang", "text_anim",
    "text_anim_speed",
}


def keyboard_text_settings(settings=None):
    settings = settings or default_text_settings()
    info_state = "✅ نیشان دەدرێت" if settings.get("show_surah_info", True) else "❌ شاراوەتەوە"
    info_pos = "⬆️ سەرەوە" if settings.get("surah_info_pos", "bottom") == "top" else "⬇️ خوارەوە"
    footer_state = "✅ نیشان دەدرێت" if settings.get("show_footer", True) else "❌ شاراوەتەوە"
    number_lang = settings.get("number_lang", "en")
    number_label = "کوردی (١٢٣)" if number_lang == "ku" else "English (123)"
    try:
        darkness = max(0, min(100, int(settings.get("background_darkness", 0) or 0)))
    except (TypeError, ValueError):
        darkness = 0
    return {"inline_keyboard": [
        # One tap -> a brand-new random beautiful design (colors, sizes,
        # positions, fonts and background all change together).
        [{"text": "🎲 دیزاینی هەڕەمەکی (Random)", "callback_data": "random_design"}],
        [{"text": "🎨 ڕەنگی ئایەت: " + color_label(settings.get("ayah_color", (255,255,255))), "callback_data": "setting:ayah_color"},
         {"text": "🟡 ڕەنگی تەفسیر: " + color_label(settings.get("tafsir_color", (255,210,90))), "callback_data": "setting:tafsir_color"}],
        [{"text": "🔠 قەبارەی ئایەت", "callback_data": "setting:ayah_size"},
         {"text": "🔡 قەبارەی تەفسیر", "callback_data": "setting:tafsir_size"}],
        [{"text": "📝 قەبارەی ناو و ژمارە", "callback_data": "setting:other_size"},
         {"text": "🔎 قەبارەی دەقی خوارەوە", "callback_data": "setting:footer_size"}],
        [{"text": "⬆️ شوێنی ئایەت", "callback_data": "setting:ayah_y"},
         {"text": "⬇️ شوێنی تەفسیر", "callback_data": "setting:tafsir_y"}],
        # ONE switch + ONE position for the surah name (was two toggles before).
        [{"text": "👁️ ناوی سوورەت و ژمارەی ئایەت: " + info_state, "callback_data": "toggle_surah_info"}],
        [{"text": "📍 شوێنی ناوی سوورەت: " + info_pos, "callback_data": "toggle_surah_info_pos"}],
        # Footer text: user can hide it and bring it back, like the surah name.
        [{"text": "📝 دەقی خوارەوە: " + footer_state, "callback_data": "toggle_footer"}],
        [{"text": "✍️ گۆڕینی دەقی خوارەوە", "callback_data": "footer_text"}],
        [{"text": "🔢 ژمارەی ئایەت: " + number_label, "callback_data": "setting:number_lang"}],
        [{"text": "✨ ئەنیمەیشنی نووسین: " + text_anim_label(settings.get("text_anim", "fade")), "callback_data": "setting:text_anim"}],
        [{"text": "⏱️ خێرایی ئەنیمەیشن: " + text_anim_speed_label(settings.get("text_anim_speed", "medium")), "callback_data": "setting:text_anim_speed"}],
        [{"text": "🌑 تاریککردنی پاشبنەما: %d%%" % darkness, "callback_data": "setting:background_darkness"}],
        [{"text": "🔤 فۆنتی: " + font_value_label("ayah_font", settings.get("ayah_font", qv.DEFAULT_AYAH_FONT)), "callback_data": "setting:ayah_font"},
         {"text": "✍️ فۆنتی: " + font_value_label("tafsir_font", settings.get("tafsir_font", qv.DEFAULT_TAFSIR_FONT)), "callback_data": "setting:tafsir_font"}],
        [{"text": "✅ تەواوە، بەردەوام بە", "callback_data": "settings:done"}],
    ]}


def keyboard_setting_values(setting):
    colors = [
        ("سپی", (255,255,255)), ("زەرد", (255,210,90)),
        ("سەوز", (130,255,170)), ("شین", (130,200,255)),
        ("پەمەیی", (255,145,190)), ("مۆر", (205,160,255)),
        ("پرتەقاڵی", (255,165,80)), ("فیرووزەیی", (100,240,230)),
        ("سوور", (255,90,90)), ("زەیتوونی", (190,220,120)),
        ("خۆڵەمێشی ڕوون", (230,230,230)), ("ڕەش", (20,20,20)),
    ]
    choices = {
        "ayah_color": colors,
        "tafsir_color": colors,
        "ayah_size": [("بچووک", 34), ("مامناوەند", 44), ("گەورە", 54), ("زۆر گەورە", 62)],
        "tafsir_size": [("بچووک", 22), ("مامناوەند", 30), ("گەورە", 36), ("زۆر گەورە", 42)],
        "other_size": [("بچووک", 18), ("مامناوەند", 24), ("گەورە", 30), ("زۆر گەورە", 36)],
        "footer_size": [("بچووک", 24), ("مامناوەند", 34), ("گەورە", 38), ("زۆر گەورە", 42)],
        "ayah_y": [("سەرەوە", 190), ("ناوەڕاست", 300), ("خوارەوە", 420)],
        "tafsir_y": [("ناوەڕاست", 600), ("خوارەوە", 730), ("زۆر خوارەوە", 830)],
        "number_lang": [("English (123)", "en"), ("کوردی (١٢٣)", "ku")],
        "text_anim": [
            ("✨ فەیدی نەرم", "fade"),
            ("⬆️ سەرکەوتن", "rise"),
            ("🔍 زووم", "zoom"),
            ("⬇️ دابەزین لە سەرەوە", "drop"),
        ],
        "text_anim_speed": [
            ("🐢 سەبر (هێواش)", "slow"),
            ("⏱️ ناوەند", "medium"),
            ("⚡ خێرا", "fast"),
        ],
    }
    rows = []
    for label, value in choices[setting]:
        encoded = ",".join(map(str, value)) if isinstance(value, tuple) else str(value)
        rows.append([{"text": label, "callback_data": "setval:%s:%s" % (setting, encoded)}])
    rows.append([{"text": "↩️ گەڕانەوە بۆ ڕێکخستن", "callback_data": "settings:back"}])
    return {"inline_keyboard": rows}


def download_telegram_file(file_id, destination):
    info = telegram("getFile", {"file_id": file_id})
    file_path = info.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram نەیتوانی فایلەکە بدۆزێتەوە.")
    url = "https://api.telegram.org/file/bot" + BOT_TOKEN + "/" + file_path
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 QuranBot/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response, open(destination, "wb") as output:
        shutil.copyfileobj(response, output)
    if not os.path.isfile(destination) or os.path.getsize(destination) < 100:
        raise RuntimeError("فایلی وەرگیراو بەتاڵە یان تەواو نییە.")
    return destination


# =========================
# VIDEO CREATION WITH PROGRESS
# =========================

def progress_bar(percent):
    filled = int(round(max(0, min(100, percent)) / 10.0))
    return "▰" * filled + "▱" * (10 - filled)


def make_progress_reporter(chat_id):
    """Builds a progress bar message and updates it in place (throttled)."""
    state = {"message_id": None, "last_edit": 0.0, "last_percent": -1}
    lock = threading.Lock()

    def report(percent, label=""):
        percent = int(max(0, min(100, percent)))

        with lock:
            is_first = state["message_id"] is None
            is_final = percent >= 100

            if not is_first and not is_final:
                if (
                    time.time() - state["last_edit"] < 2.5
                    and percent - state["last_percent"] < 8
                ):
                    return

            text = (
                "⏳ دروستکردنی ڤیدیۆ\n\n"
                + progress_bar(percent)
                + "  %d%%\n" % percent
            )

            if label:
                text += label + "\n"

            try:
                if is_first:
                    result = send_message(chat_id, text)
                    state["message_id"] = (result or {}).get("message_id")
                else:
                    edit_message(chat_id, state["message_id"], text)

                state["last_edit"] = time.time()
                state["last_percent"] = percent
            except Exception as exc:
                log_error(
                    "PROGRESS UPDATE FAILED | chat_id=%s | %r"
                    % (chat_id, exc)
                )

    return report


def create_selected_video(chat_id):
    session = SESSIONS.get(chat_id)
    if not session or session.get("state") != "design":
        send_message(
            chat_id,
            "داواکارییەکەت بەسەرچووە؛ دوگمەکە بگۆڕە:",
            keyboard_start(),
        )
        return

    with _ACTIVE_JOBS_LOCK:
        if chat_id in ACTIVE_JOBS:
            send_message(
                chat_id,
                "⚠️ ڤیدیۆیەکی تر بۆ تۆ لە دروستکردندایە.\n"
                "تکایە چاوەڕێ بکە تا ئەمە تەواو دەبێت.",
            )
            return
        ACTIVE_JOBS.add(chat_id)

    job_dir = None
    slot_acquired = False

    try:
        # Limit how many videos are built at the same time; extra
        # requests wait in a queue instead of blocking everyone.
        if not JOB_SLOTS.acquire(blocking=False):
            send_message(
                chat_id,
                "🕒 ئێستا %d داواکاری بە یەک کات جێبەجێ دەکرێن و "
                "هەموو شوێنەکان پڕن.\n"
                "داواکاریەکەت خرایە نۆرەوە و بەم زووانە دەست پێ دەکات."
                % MAX_CONCURRENT_JOBS,
            )
            JOB_SLOTS.acquire()

        slot_acquired = True

        surah_number = int(session["surah_number"])
        start_ayah = int(session["start_ayah"])
        end_ayah = int(session["end_ayah"])
        reciter_id = str(session["reciter_id"])
        ayahs = find_surah_rows(surah_number, start_ayah, end_ayah)
        surah = next(s for s in get_surahs() if int(s["number"]) == surah_number)
        reciter_name = RECITERS[reciter_id]["name"]

        # Chosen size preset (falls back to the user's last choice,
        # then to Telegram).
        size_preset = str(
            session.get("size_preset")
            or load_user_preferences().get(str(chat_id), {}).get("size_preset")
            or DEFAULT_SIZE_PRESET
        )

        progress = make_progress_reporter(chat_id)
        progress(2, "داواکاریەکەت تۆمار کرا")

        video_path, job_dir, send_note = build_video(
            chat_id, ayahs, reciter_id,
            background_path=session.get("background_path"),
            background_kind=session.get("background_kind", "image"),
            background_darkness=int(
                max(0, min(100, int((session.get("text_settings") or {}).get("background_darkness", 0) or 0)))
            ),
            include_tafsir=bool(session.get("include_tafsir", True)),
            text_settings=session.get("text_settings", {}),
            progress=progress,
            size_preset=size_preset,
        )

        progress(93, "ناردنی ڤیدیۆ بۆ تۆ...")

        caption = "قورئانی پیرۆز — %s\nقاری: %s\nئایەت %d–%d" % (
            surah["name"], reciter_name, start_ayah, end_ayah,
        )

        if send_note:
            caption += "\n\n" + send_note

        try:
            send_video(chat_id, video_path, caption)
        except Exception as send_exc:
            # Final safety net: if Telegram still refused the upload
            # (HTTP 413), shrink once more and retry.
            if _is_too_large_error(send_exc):
                progress(95, "قەبارە گەورەیە؛ بچووککردنەوە و دووبارە ناردن...")
                smaller = shrink_video_for_send(video_path, 40)
                send_video(chat_id, smaller, caption)
            else:
                raise
        progress(100, "✅ تەواو بوو!")
        send_message(
            chat_id,
            "✅ ڤیدیۆکە بە سەرکەوتوویی بۆ تۆ نێردرا.\n"
            "بۆ دروستکردنی ڤیدیۆیەکی تر دوگمەکە بگۆڕە:",
            keyboard_start(),
        )
    except Exception as exc:
        log_exception("VIDEO CREATION ERROR | chat_id=%s" % chat_id, exc)
        try:
            send_message(chat_id, "❌ نەمتوانی ڤیدیۆکە دروست بکەم.\n" + str(exc))
        except Exception as send_exc:
            log_exception("Could not send error to Telegram", send_exc)
    finally:
        if slot_acquired:
            JOB_SLOTS.release()
        with _ACTIVE_JOBS_LOCK:
            ACTIVE_JOBS.discard(chat_id)
        # Only remove the session if the user has not started a new
        # flow (e.g. /start) while this job was running.
        if SESSIONS.get(chat_id) is session:
            SESSIONS.pop(chat_id, None)
        if job_dir:
            shutil.rmtree(job_dir, ignore_errors=True)


# =========================
# BOT COMMANDS
# =========================

def start(chat_id, user_id=None):
    log_error("START COMMAND | chat_id=%s" % chat_id)
    if not require_channel_membership(chat_id, user_id):
        return

    SESSIONS[chat_id] = {"state": "reciter", "text_settings": saved_text_settings(chat_id)}

    welcome = (
        "بەخێربێیت بۆ بۆتی ڤیدیۆی قورئانی پیرۆز 🌙\n\n"
        "✨ لێرە دەتوانیت ڤیدیۆیەکی جیاواز دروست بکەیت لەگەڵ:\n"
        "🎙️ قاری هەڵبژێردراو\n"
        "🖼️ پاشبنەمای جوان\n"
        "✨ ئەنیمەیشنی نووسین\n\n"
        "🎙️ سەرەتا قاریی دڵخوازی خۆت هەڵبژێرە:"
    )

    if chat_id in ACTIVE_JOBS:
        welcome = (
            "⚠️ تێبینی: ڤیدیۆیەکی تر بۆ تۆ لە دروستکردندایە و "
            "کاتێک ئامادە بوو بۆت دەنێردرێت.\n\n" + welcome
        )

    send_message(chat_id, welcome, keyboard_reciters())


def handle_message(message):
    chat_id = message.get("chat", {}).get("id")

    if chat_id is None:
        return

    text = (message.get("text") or "").strip()

    log_error(
        "MESSAGE RECEIVED | chat_id=%s | text=%s"
        % (chat_id, text[:150])
    )

    if text.startswith("/start"):
        start(chat_id, message.get("from", {}).get("id", chat_id))
        return

    if not require_channel_membership(chat_id, message.get("from", {}).get("id", chat_id)):
        return

    if text.startswith("/help"):
        send_help(chat_id)
        return

    if text.startswith("/cancel"):
        SESSIONS.pop(chat_id, None)

        send_message(
            chat_id,
            "داواکارییەکەت هەڵوەشایەوە. "
            "بۆ دەستپێکردن دوگمەکە بگۆڕە:",
            keyboard_start(),
        )
        return

    session = SESSIONS.get(chat_id)

    if not session:
        send_message(
            chat_id,
            "بۆ دەستپێکردن دوگمەکە بگۆڕە:",
            keyboard_start(),
        )
        return

    # Receive a custom photo/video from the user's Telegram gallery.
    if session.get("state") == "await_gallery_upload":
        mode = session.get("background_kind", "image")
        try:
            os.makedirs(JOBS_DIR, exist_ok=True)
            if mode == "image":
                photos = message.get("photo") or []
                if not photos:
                    send_message(chat_id, "تکایە وێنەیەک بنێرە، یان /cancel بنووسە.")
                    return
                file_id = photos[-1]["file_id"]
                destination = os.path.join(JOBS_DIR, "bg_%s_custom.jpg" % chat_id)
            else:
                video = message.get("video")
                if not video:
                    send_message(chat_id, "تکایە ڤیدیۆیەکی MP4 بنێرە، یان /cancel بنووسە.")
                    return
                file_id = video["file_id"]
                destination = os.path.join(JOBS_DIR, "bg_%s_custom.mp4" % chat_id)
            download_telegram_file(file_id, destination)
            if mode == "video":
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", destination],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding="utf-8", errors="replace",
                )
                if probe.returncode != 0:
                    raise RuntimeError("فایلەکە ڤیدیۆیەکی دروستی MP4 نییە.")
            session["background_path"] = destination
            session["background_kind"] = mode
            session["state"] = "tafsir_choice"
            send_message(chat_id, "✅ پاشبنەماکەت وەرگیرا. ئایا دەتەوێت تەفسیر لە ڤیدیۆکەدا دەرکەوێت؟", keyboard_tafsir())
        except Exception as exc:
            log_exception("CUSTOM BACKGROUND UPLOAD ERROR", exc)
            send_message(chat_id, "❌ نەمتوانی فایلەکە وەربگرم: " + str(exc))
        return

    # Receive a font file (TTF/OTF) uploaded by the user from their phone.
    if session.get("state") == "await_font":
        slot = session.get("font_slot")
        document = message.get("document")

        if slot not in ("ayah_font", "tafsir_font"):
            session["state"] = "text_settings"
            send_message(
                chat_id,
                "داواکارییەکە بەسەرچووە. تکایە فۆنتەکە دووبارە هەڵبژێرە.",
                keyboard_text_settings(session.setdefault("text_settings", saved_text_settings(chat_id))),
            )
            return

        if not document:
            send_message(
                chat_id,
                "📤 تکایە فایلی فۆنت (TTF یان OTF) وەک «فایل/Document» بنێرە،"
                " یان /cancel بنووسە.",
            )
            return

        file_name = str(document.get("file_name") or "")

        if not file_name.lower().endswith((".ttf", ".otf")):
            send_message(
                chat_id,
                "❌ تەنها فایلی فۆنتی TTF یان OTF قبوڵ دەکرێت."
                " تکایە دووبارە بنێرەوە یان /cancel بنووسە.",
            )
            return

        destination = None

        try:
            os.makedirs(USER_FONTS_DIR, exist_ok=True)

            extension = ".otf" if file_name.lower().endswith(".otf") else ".ttf"
            destination = os.path.join(USER_FONTS_DIR, "font_%s%s" % (chat_id, extension))

            # Remove a previous copy with the other extension so the
            # user never keeps two stale font files at once.
            for old_extension in (".ttf", ".otf"):
                old_path = os.path.join(USER_FONTS_DIR, "font_%s%s" % (chat_id, old_extension))
                if old_path != destination and os.path.isfile(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

            download_telegram_file(document["file_id"], destination)

            missing_letters = validate_font_file(
                destination,
                require_kurdish=(slot == "tafsir_font"),
            )

            if missing_letters:
                try:
                    os.remove(destination)
                except OSError:
                    pass
                send_message(
                    chat_id,
                    "❌ ئەم فۆنتە ئەم پیتە پێویستانەی تێدا نییە: %s\n"
                    "تکایە فۆنتێکی تر بنێرە یان /cancel بنووسە."
                    % " ".join(missing_letters),
                )
                return

            settings = session.setdefault("text_settings", saved_text_settings(chat_id))
            settings[slot] = qv.CUSTOM_FONT_KEY
            settings[slot + "_custom"] = destination
            save_text_settings(chat_id, settings)

            session["state"] = "text_settings"
            session.pop("font_slot", None)

            send_message(
                chat_id,
                "✅ فۆنتەکەت هەڵگیرا و ئێستا بەکاردەهێنرێت. پێشبینینێکت بۆ ناردم:",
                keyboard_text_settings(settings),
            )
            send_settings_preview(chat_id, session)

        except Exception as exc:
            log_exception("CUSTOM FONT UPLOAD ERROR", exc)
            if destination and os.path.isfile(destination):
                try:
                    os.remove(destination)
                except OSError:
                    pass
            send_message(chat_id, "❌ نەمتوانی فۆنتەکە وەربگرم: " + str(exc))
        return

    if message.get("photo") or message.get("video"):
        send_message(chat_id, "بۆ بەکارهێنانی گەلەری، سەرەتا /start بنووسە و قۆناغەکان تەواو بکە.")
        return

    if session.get("state") == "footer_text":
        footer = text.strip()
        if not footer or len(footer) > 100:
            send_message(chat_id, "تکایە دەقێکی نەنەخشاو و لە 100 پیت کەمتر بنووسە.")
            return
        settings = session.setdefault("text_settings", saved_text_settings(chat_id))
        settings["footer_text"] = footer
        session["state"] = "text_settings"
        save_text_settings(chat_id, settings)
        send_message(chat_id, "✅ دەقی خوارەوە گۆڕدرا و پاشەکەوت کرا. پێشبینینێکت بۆ ناردم:", keyboard_text_settings(settings))
        send_settings_preview(chat_id, session)
        return

    if session.get("state") == "darkness_setting":
        # The 🌑 darkness option now lives inside the text settings menu:
        # the answer only saves + previews, it does not start the video.
        try:
            digit_map = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
            darkness = int(text.translate(digit_map).strip())
            if darkness < 0 or darkness > 100:
                raise ValueError
        except (TypeError, ValueError):
            send_message(chat_id, "❌ ژمارەکە دروست نییە. تەنها ژمارەیەک لە 0 تا 100 بنووسە.")
            return
        settings = session.setdefault("text_settings", saved_text_settings(chat_id))
        settings["background_darkness"] = darkness
        save_text_settings(chat_id, settings)
        session["state"] = "text_settings"
        send_message(
            chat_id,
            "✅ تاریککردنی پاشبنەما گۆڕدرا و پاشەکەوت کرا. پێشبینینێکت بۆ ناردم:",
            keyboard_text_settings(settings),
        )
        send_settings_preview(chat_id, session)
        return

    if session.get("state") == "start_ayah":
        try:
            number = int(text)

            surah = next(
                s for s in get_surahs()
                if int(s["number"]) == session["surah_number"]
            )

            if (
                number < 1
                or number > int(surah["numberOfAyahs"])
            ):
                raise ValueError(
                    "ژمارەی ئایەت لە 1 تا %d بێت."
                    % int(surah["numberOfAyahs"])
                )

            session["start_ayah"] = number
            session["state"] = "end_ayah"

            send_message(
                chat_id,
                "ئایەتی دەستپێک: %d\n"
                "ئێستا ژمارەی ئایەتی کۆتایی بنووسە (1–%d)."
                % (
                    number,
                    int(surah["numberOfAyahs"]),
                ),
            )

        except Exception as exc:
            log_error(
                "START AYAH INPUT ERROR | chat_id=%s | %r"
                % (chat_id, exc)
            )

            send_message(
                chat_id,
                "ژمارەیەکی دروست بنووسە. " + str(exc),
            )

        return

    if session.get("state") == "end_ayah":
        try:
            end_ayah = int(text)
            surah_number = int(session["surah_number"])
            start_ayah = int(session["start_ayah"])
            surah = next(s for s in get_surahs() if int(s["number"]) == surah_number)
            if end_ayah < start_ayah or end_ayah > int(surah["numberOfAyahs"]):
                raise ValueError("ژمارەی ئایەتی کۆتایی دەبێت لە %d تا %d بێت." % (start_ayah, int(surah["numberOfAyahs"])))
            session["end_ayah"] = end_ayah
            session["state"] = "output_mode"
            send_message(chat_id, "دەتەوێت پاشبنەماکە چۆن بێت؟", keyboard_output_modes())
        except Exception as exc:
            send_message(chat_id, "ژمارەی ئایەتی کۆتایی دروست نییە: " + str(exc))
        return

    send_message(
        chat_id,
        "تکایە بە هەنگاوەکانی بۆتەکە بەردەوام بە، یان دوگمەکە بگۆڕە:",
        keyboard_start(),
    )


# =========================
# INLINE BUTTONS
# =========================

def handle_callback(callback):
    callback_id = callback.get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    data = callback.get("data", "")

    log_error(
        "CALLBACK RECEIVED | chat_id=%s | data=%s"
        % (chat_id, data)
    )

    if chat_id is None:
        answer_callback(callback_id)
        return

    try:
        # 🚀 The start button works even before the membership check,
        # because start() itself guides the user into the channel if needed.
        if data == "start:go":
            user_id = callback.get("from", {}).get("id", chat_id)
            answer_callback(callback_id)
            start(chat_id, user_id)
            return

        # ℹ️ The help button is also always available.
        if data == "help:show":
            answer_callback(callback_id)
            send_help(chat_id)
            return

        if data == "join:check":
            user_id = callback.get("from", {}).get("id", chat_id)
            if require_channel_membership(chat_id, user_id):
                answer_callback(callback_id, "سوپاس، ئەندامبوون پشتڕاست کرایەوە")
                start(chat_id, user_id)
            else:
                answer_callback(callback_id, "سەرەتا ببە ئەندامی کەناڵەکە")
            return

        if not require_channel_membership(chat_id, callback.get("from", {}).get("id", chat_id)):
            answer_callback(callback_id, "سەرەتا ببە ئەندامی کەناڵەکە")
            return

        if data.startswith("previews:"):
            kind = data.split(":", 1)[1]
            if kind not in ("image", "video"):
                raise ValueError("جۆری پاشبنەما دروست نییە.")
            answer_callback(callback_id, "پێشبینینەکان دەنێردرێن")
            send_background_previews(chat_id, kind)
            return

        if data.startswith("mode:"):
            kind = data.split(":", 1)[1]
            if kind not in ("image", "video", "black"):
                raise ValueError("جۆری پاشبنەما دروست نییە.")
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "output_mode":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["background_kind"] = kind
            if kind == "black":
                session["background_path"] = None
                session["state"] = "tafsir_choice"
                edit_message(chat_id, message_id, "⚫ پاشبنەمای ڕەشی تەواو هەڵبژێردرا.")
                answer_callback(callback_id)
                send_message(chat_id, "ئایا دەتەوێت تەفسیر لە ڤیدیۆکەدا دەرکەوێت؟", keyboard_tafsir())
                return
            session["state"] = "background_choice"
            answer_callback(callback_id)
            prefs = user_preferences(chat_id)
            if prefs.get("previews_seen_" + kind):
                send_message(chat_id, "⬇️ ژمارەی پاشبنەما هەڵبژێرە:", keyboard_backgrounds(kind))
            else:
                edit_message(chat_id, message_id, "⏳ پێشبینینی ١٠ پاشبنەما بۆ یەکەم جار دەنێردرێن...")
                send_background_previews(chat_id, kind)
            return

        if data.startswith("gallery:"):
            kind = data.split(":", 1)[1]
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "background_choice":
                raise ValueError("تکایە /start دووبارە بکە.")
            if kind not in ("image", "video"):
                raise ValueError("جۆری پاشبنەما دروست نییە.")
            session["background_kind"] = kind
            session["state"] = "await_gallery_upload"
            answer_callback(callback_id)
            noun = "وێنە" if kind == "image" else "ڤیدیۆیەکی MP4"
            send_message(chat_id, "📱 ئێستا %sەکەت لە گەلەری بنێرە بۆ بۆتەکە." % noun)
            return

        if data.startswith("background:"):
            _, kind, raw_number = data.split(":", 2)
            number = int(raw_number)
            if kind not in ("image", "video"):
                raise ValueError("جۆری پاشبنەما دروست نییە.")
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "background_choice":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["background_path"] = resolve_background(kind, number)
            session["background_kind"] = kind
            session["state"] = "tafsir_choice"
            answer_callback(callback_id, "پاشبنەما هەڵبژێردرا")
            send_message(chat_id, "ئایا دەتەوێت تەفسیر لە ڤیدیۆکەدا دەرکەوێت؟", keyboard_tafsir())
            return

        if data.startswith("tafsir:"):
            choice = data.split(":", 1)[1]
            if choice not in ("on", "off"):
                raise ValueError("هەڵبژاردەی تەفسیر دروست نییە.")
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "tafsir_choice":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["include_tafsir"] = (choice == "on")
            session["text_settings"] = saved_text_settings(chat_id)
            session["state"] = "text_settings"
            answer_callback(callback_id, "هەڵبژێردرا")
            send_message(chat_id, "🎨 ڕێکخستنی دەق: ڕەنگ، قەبارە و فۆنت دیاری بکە. دوای هەر گۆڕانکارییەک پێشبینینێکت بۆ دەنێرم. ڕێکخستنەکان پاشەکەوت دەکرێن.", keyboard_text_settings(session["text_settings"]))
            send_settings_preview(chat_id, session)
            return

        if data.startswith("setting:"):
            session = SESSIONS.get(chat_id)
            setting = data.split(":", 1)[1]
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            answer_callback(callback_id)
            label = SETTING_LABELS.get(setting, setting)
            if setting == "ayah_font":
                current = (session.get("text_settings") or {}).get(setting)
                send_message(chat_id, "🔤 فۆنتی هەڵبژێرە (فۆنتی قورئان):", keyboard_fonts(setting, current))
            elif setting == "tafsir_font":
                current = (session.get("text_settings") or {}).get(setting)
                send_message(chat_id, "✍️ فۆنتی نووسین هەڵبژێرە (فۆنتی تەفسیر):", keyboard_fonts(setting, current))
            elif setting == "background_darkness":
                # 🌑 moved here from the old post-settings question.
                session["state"] = "darkness_setting"
                send_message(
                    chat_id,
                    "🌑 ڕێژەی تاریککردنی پاشبنەما بنووسە لە 0 تا 100.\n\n"
                    "0 = تاریککردن نییە\n25 = کەم\n50 = مامناوەند\n"
                    "75 = زۆر\n100 = تەواو تاریک",
                    {"inline_keyboard": [[{"text": "↩️ گەڕانەوە بۆ ڕێکخستن", "callback_data": "settings:back"}]]},
                )
            elif setting == "text_anim":
                # ✨ the 4 entrance animations of the ayah + tafsir text.
                lines = [
                    "✨ جۆری ئەنیمەیشنی نووسین هەڵبژێرە (بۆ ئایەت و تەفسیر):",
                    "",
                ]
                for anim_key in TEXT_ANIMS:
                    lines.append(
                        "%s — %s"
                        % (
                            TEXT_ANIM_LABELS[anim_key],
                            TEXT_ANIM_DESCRIPTIONS[anim_key],
                        )
                    )
                lines.append("")
                lines.append(
                    "⏱️ خێرایی ئەنیمەیشن لە مینیوودا دەگۆڕدرێت: "
                    + "، ".join(TEXT_ANIM_SPEED_LABELS.values())
                )
                send_message(
                    chat_id,
                    "\n".join(lines),
                    keyboard_setting_values(setting),
                )
            else:
                send_message(chat_id, "هەڵبژاردەی خۆت بۆ %s دیاری بکە:" % label, keyboard_setting_values(setting))
            return

        if data.startswith("setval:"):
            _, setting, raw = data.split(":", 2)
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings" or setting not in SETVAL_ALLOWED:
                raise ValueError("ڕێکخستنەکە دروست نییە.")
            if setting in ("ayah_font", "tafsir_font"):
                if raw == qv.CUSTOM_FONT_KEY:
                    # User wants to upload their own font file from their phone.
                    session["state"] = "await_font"
                    session["font_slot"] = setting
                    answer_callback(callback_id, "فۆنت بنێرە")
                    send_message(
                        chat_id,
                        "📤 تکایە فایلی فۆنتەکەت (TTF یان OTF) بنێرە بۆ بۆتەکە.\n\n"
                        "لە تێلەگرام: 📎 دوگمە بنگرە > «File» > فۆنتەکە هەڵبژێرە.\n"
                        "بۆ هەڵوەشاندنەوە /cancel بنووسە.",
                    )
                    return
                if qv.normalize_font_key(raw) != raw:
                    raise ValueError("فۆنتەکە دروست نییە.")
                value = raw
            elif setting == "number_lang":
                # Ayah-number digit language: English (123) or Kurdish (١٢٣).
                if raw not in qv.NUMBER_LANGS:
                    raise ValueError("هەڵبژاردەی ژمارە دروست نییە.")
                value = raw
            elif setting == "text_anim":
                # Entrance animation of the ayah + tafsir text.
                if raw not in TEXT_ANIMS:
                    raise ValueError("ئەنیمەیشنەکە دروست نییە.")
                value = raw
            elif setting == "text_anim_speed":
                # Speed of the entrance animation: slow / medium / fast.
                if raw not in TEXT_ANIM_SPEEDS:
                    raise ValueError("خێراییەکە دروست نییە.")
                value = raw
            elif setting.endswith("color"):
                value = tuple(int(x) for x in raw.split(","))
                if len(value) != 3 or any(x < 0 or x > 255 for x in value): raise ValueError("ڕەنگی دروست نییە.")
            else:
                value = int(raw)
            session.setdefault("text_settings", default_text_settings())[setting] = value
            save_text_settings(chat_id, session["text_settings"])
            answer_callback(callback_id, "ڕێکخستن هەڵگیرا")
            send_message(chat_id, "✅ %s گۆڕدرا و پاشەکەوت کرا. پێشبینینێکت بۆ ناردم:" % SETTING_LABELS.get(setting, setting), keyboard_text_settings(session["text_settings"]))
            send_settings_preview(chat_id, session)
            return

        if data == "toggle_surah_info":
            # ONE switch: show or hide the surah name + ayah number.
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            settings = session.setdefault("text_settings", default_text_settings())
            settings["show_surah_info"] = not settings.get("show_surah_info", True)
            save_text_settings(chat_id, settings)
            answer_callback(callback_id, "ڕێکخستن پاشەکەوت کرا")
            send_message(chat_id, "🎨 ڕێکخستنی دەق:", keyboard_text_settings(settings))
            send_settings_preview(chat_id, session)
            return

        if data == "toggle_surah_info_pos":
            # Position of the surah name: top or bottom.
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            settings = session.setdefault("text_settings", default_text_settings())
            settings["surah_info_pos"] = (
                "bottom" if settings.get("surah_info_pos", "bottom") == "top" else "top"
            )
            save_text_settings(chat_id, settings)
            answer_callback(callback_id, "ڕێکخستن پاشەکەوت کرا")
            send_message(chat_id, "🎨 ڕێکخستنی دەق:", keyboard_text_settings(settings))
            send_settings_preview(chat_id, session)
            return

        if data == "toggle_footer":
            # Show/hide the bottom footer text, like the surah-name toggle.
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            settings = session.setdefault("text_settings", default_text_settings())
            settings["show_footer"] = not settings.get("show_footer", True)
            save_text_settings(chat_id, settings)
            answer_callback(callback_id, "ڕێکخستن پاشەکەوت کرا")
            send_message(chat_id, "🎨 ڕێکخستنی دەق:", keyboard_text_settings(settings))
            send_settings_preview(chat_id, session)
            return

        if data == "random_design":
            # One tap -> a completely new random design: colors, sizes,
            # positions, fonts, darkness and even the background.
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            answer_callback(callback_id, "🎲 دیزاینی نوێ ئامادە دەکرێت...")
            theme_name, bg_note = apply_random_design(chat_id, session)
            save_text_settings(chat_id, session["text_settings"])
            session["state"] = "text_settings"
            send_message(
                chat_id,
                "🎲 دیزاینی هەڕەمەکی «%s» جێبەجێ کرا!%s\n"
                "دوگمەکە دووبارە بگەڕێ بۆ دیزاینیەکی تازە." % (theme_name, bg_note),
                keyboard_text_settings(session["text_settings"]),
            )
            send_settings_preview(chat_id, session)
            return

        if data == "footer_text":
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["state"] = "footer_text"
            answer_callback(callback_id)
            send_message(chat_id, "✍️ دەقی دڵخوازت بۆ خوارەوە بنووسە (تا 100 پیت). بۆ نموونە: قورئانی پیرۆز - ڕێگای ئیسلام")
            return

        if data == "settings:back":
            answer_callback(callback_id)
            session = SESSIONS.get(chat_id)
            # Leaving the "upload your font", the "background darkness"
            # or the "video size" step returns to the settings menu.
            if session and session.get("state") in ("await_font", "darkness_setting"):
                session["state"] = "text_settings"
                session.pop("font_slot", None)
            elif session and session.get("state") == "size_choice":
                session["state"] = "text_settings"
            send_message(chat_id, "🎨 ڕێکخستنی دەق:", keyboard_text_settings((session or {}).get("text_settings", saved_text_settings(chat_id))))
            return

        if data == "settings:done":
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            save_text_settings(chat_id, session.get("text_settings", default_text_settings()))
            answer_callback(callback_id, "ڕێکخستنەکان هەڵگیرا")
            # 🌑 darkness is set inside the settings menu; now ask for
            # the video size (Facebook / TikTok / Instagram / Telegram /
            # Gallery) BEFORE building the video.
            session["state"] = "size_choice"
            last_size = load_user_preferences().get(str(chat_id), {}).get("size_preset")
            send_message(
                chat_id,
                "📏 قەبارەی ڤیدیۆکە چۆن بێت؟\n\n"
                "بۆ هەر پلاتفۆرمێک قەبارەی گونجاو دانراوە:\n"
                "• Facebook / TikTok / Instagram: 1080×1920\n"
                "• Telegram: 720×1280 — سووکە و بە خێرایی دەنێردرێت\n"
                "• گەلەری: بەرزترین کوالیتی بۆ پاشەکەوتکردن لە مۆبایل",
                keyboard_video_size(last_size),
            )
            return

        if data.startswith("size:"):
            size_key = data.split(":", 1)[1]
            if size_key not in VIDEO_SIZE_PRESETS:
                raise ValueError("قەبارەی هەڵبژێردراو دروست نییە.")
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "size_choice":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["size_preset"] = size_key
            session["state"] = "design"
            # Remember the choice for the next video.
            all_prefs = load_user_preferences()
            all_prefs.setdefault(str(chat_id), {})["size_preset"] = size_key
            save_user_preferences(all_prefs)
            answer_callback(callback_id, "قەبارە هەڵبژێردرا")
            edit_message(
                chat_id,
                message_id,
                "✅ قەبارەی هەڵبژێردراو: %s\n⏳ دروستکردنی ڤیدیۆ دەست پێ دەکات..."
                % VIDEO_SIZE_PRESETS[size_key]["label"],
            )
            create_selected_video(chat_id)
            return

        if data == "change_reciter":
            SESSIONS[chat_id] = {"state": "reciter"}

            edit_message(
                chat_id,
                message_id,
                "🎙️ قاریی دڵخوازی خۆت هەڵبژێرە:",
                keyboard_reciters(),
            )

            answer_callback(callback_id)
            return

        if data.startswith("reciter:"):
            reciter_id = data.split(":", 1)[1]

            if reciter_id not in RECITERS:
                raise ValueError("قارییەکە نەدۆزرایەوە.")

            log_error(
                "RECITER SELECTED | chat_id=%s | id=%s | name=%s"
                % (
                    chat_id,
                    reciter_id,
                    RECITERS[reciter_id]["name"],
                )
            )

            SESSIONS[chat_id] = {
                "state": "surah",
                "page": 0,
                "reciter_id": reciter_id,
            }

            edit_message(
                chat_id,
                message_id,
                "✅ قاری هەڵبژێردرا: %s\n\n"
                "ئێستا سوورەتەکەت هەڵبژێرە:"
                % RECITERS[reciter_id]["name"],
                keyboard_surahs(0),
            )

            answer_callback(
                callback_id,
                "قاری هەڵبژێردرا",
            )
            return

        if data.startswith("page:"):
            page = max(
                0,
                int(data.split(":", 1)[1]),
            )

            edit_message(
                chat_id,
                message_id,
                "سوورەتەکەت هەڵبژێرە:",
                keyboard_surahs(page),
            )

            answer_callback(callback_id)
            return

        if data.startswith("surah:"):
            session = SESSIONS.get(chat_id)

            if not session or not session.get("reciter_id"):
                start(chat_id)

                answer_callback(
                    callback_id,
                    "سەرەتا قاری هەڵبژێرە.",
                )
                return

            number = int(data.split(":", 1)[1])

            surah = next(
                s for s in get_surahs()
                if int(s["number"]) == number
            )

            reciter_id = str(session["reciter_id"])

            SESSIONS[chat_id] = {
                "state": "start_ayah",
                "surah_number": number,
                "reciter_id": reciter_id,
            }

            edit_message(
                chat_id,
                message_id,
                "🎙️ قاری: %s\n"
                "سوورەت: %s\n\n"
                "ژمارەی ئایەتی دەستپێک بنووسە (1–%d).\n\n"
                "بۆ هەڵوەشاندنەوە /cancel بنووسە."
                % (
                    RECITERS[reciter_id]["name"],
                    surah["name"],
                    int(surah["numberOfAyahs"]),
                ),
            )

            answer_callback(
                callback_id,
                "سوورەت هەڵبژێردرا",
            )
            return

        answer_callback(callback_id)

    except Exception as exc:
        log_exception(
            "CALLBACK ERROR | data=%s" % data,
            exc,
        )

        answer_callback(callback_id, str(exc)[:180])


# =========================
# PER-CHAT WORKERS (concurrent users)
# =========================

class _ChatWorker:
    """Processes one chat's updates in order, while different chats run in parallel."""

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._start_lock = threading.Lock()

    def submit(self, job):
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    daemon=True,
                )
                self._thread.start()

        self._queue.put(job)

    def _run(self):
        while True:
            job = self._queue.get()

            try:
                job()
            except Exception as exc:
                log_exception("CHAT WORKER ERROR", exc)
            finally:
                self._queue.task_done()


_CHAT_WORKERS = {}
_CHAT_WORKERS_GUARD = threading.Lock()


def chat_worker(chat_id):
    with _CHAT_WORKERS_GUARD:
        return _CHAT_WORKERS.setdefault(chat_id, _ChatWorker())


def process_update(update):
    """Dispatch each update to its chat worker so multiple users never block each other."""
    if "message" in update:
        message = update["message"]
        chat_id = message.get("chat", {}).get("id")

        if chat_id is None:
            return

        chat_worker(chat_id).submit(
            lambda m=message: handle_message(m)
        )

    elif "callback_query" in update:
        callback = update["callback_query"]
        chat_id = callback.get("message", {}).get("chat", {}).get("id")

        if chat_id is None:
            threading.Thread(
                target=handle_callback,
                args=(callback,),
                daemon=True,
            ).start()
            return

        chat_worker(chat_id).submit(
            lambda c=callback: handle_callback(c)
        )


# =========================
# 24/7 SELF-CHAINING
#
# GitHub Actions jobs have a hard 6-hour limit. Instead of restarting the
# bot every hour with `schedule` (which killed running video jobs and left
# offline gaps), the bot now keeps itself alive around the clock:
#
#   1. After 5h20m of runtime this instance triggers a FRESH workflow run
#      through the GitHub API (workflow_dispatch).
#   2. The fresh instance boots (~3 min of setup) and starts polling.
#   3. The old instance immediately gets HTTP 409 from getUpdates,
#      finishes any in-flight video jobs (drain) and exits cleanly.
#   4. Result: ZERO gap between the two instances and no video is ever
#      killed mid-render.
#
# A separate watchdog workflow (watchdog.yml) additionally checks every
# 15 minutes that a run is active, and a tiny daily keepalive commit
# stops GitHub from disabling the scheduled workflows after 60 days of
# repository inactivity.
# =========================

GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_WORKFLOW_FILE = os.environ.get("BOT_WORKFLOW_FILE", "botyuser.yml")
GH_BRANCH = os.environ.get("BOT_WORKFLOW_BRANCH", "main")

BOT_START_TIME = time.time()
HANDOVER_AFTER_SECONDS = 5 * 3600 + 20 * 60   # 5h20m -> dispatch the next run
HARD_EXIT_SECONDS = 5 * 3600 + 40 * 60        # 5h40m -> exit no matter what

_handover_dispatched = False
_last_dispatch_attempt = 0.0
_last_keepalive = 0.0


def gh_api(path, method="GET", payload=None):
    """Call the GitHub REST API with the workflow's GITHUB_TOKEN."""
    url = "https://api.github.com" + path

    data = (
        json.dumps(payload).encode("utf-8")
        if payload is not None
        else None
    )

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + GH_TOKEN,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "quran-video-bot",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")

    return json.loads(body) if body.strip() else {}


def dispatch_next_run():
    """Start a fresh copy of this workflow so the handover is seamless."""
    try:
        gh_api(
            "/repos/%s/actions/workflows/%s/dispatches"
            % (GH_REPO, GH_WORKFLOW_FILE),
            method="POST",
            payload={"ref": GH_BRANCH},
        )
        log_error(
            "HANDOVER | Fresh bot run dispatched; this instance keeps "
            "polling until the new one takes over (409)."
        )
        return True

    except Exception as exc:
        log_error("HANDOVER | Dispatching the next run failed: %r" % exc)
        return False


def maybe_dispatch_next_run():
    global _handover_dispatched, _last_dispatch_attempt

    if _handover_dispatched:
        return

    now = time.time()

    if now - _last_dispatch_attempt < 60:
        return

    _last_dispatch_attempt = now

    if dispatch_next_run():
        _handover_dispatched = True


def keepalive_commit():
    """GitHub disables every scheduled workflow after 60 days without any
    commit activity. A tiny daily commit keeps the whole Actions setup
    (watchdog + daily report) permanently active."""
    global _last_keepalive

    if not GH_TOKEN or not GH_REPO:
        return

    now = time.time()

    if now - _last_keepalive < 20 * 3600:
        return

    _last_keepalive = now
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")

    try:
        body = {
            "message": "keepalive: " + stamp,
            "content": base64.b64encode(stamp.encode()).decode(),
            "branch": GH_BRANCH,
        }

        try:
            existing = gh_api(
                "/repos/%s/contents/keepalive.txt?ref=%s"
                % (GH_REPO, GH_BRANCH)
            )
            body["sha"] = existing["sha"]
        except Exception:
            pass  # file does not exist yet -> create it

        gh_api(
            "/repos/%s/contents/keepalive.txt" % GH_REPO,
            method="PUT",
            payload=body,
        )
        log_error("KEEPALIVE | Commit created (%s)." % stamp)

    except Exception as exc:
        log_error("KEEPALIVE | Commit failed: %r" % exc)


def drain_active_jobs(max_wait=600):
    """Wait until every running video job has finished, so a handover
    never kills a video the user is waiting for."""
    deadline = time.time() + max_wait
    announced = False

    while time.time() < deadline:
        with _ACTIVE_JOBS_LOCK:
            pending = len(ACTIVE_JOBS)

        if not pending:
            return

        if not announced:
            log_error(
                "HANDOVER | Waiting for %d in-flight video job(s)..."
                % pending
            )
            announced = True

        time.sleep(2)

    log_error("HANDOVER | Drain timeout reached; exiting anyway.")


# =========================
# MAIN LOOP
# =========================

def setup_bot_menu():
    """Register the bot commands so Telegram shows the blue Menu button
    with a proper Kurdish command list (the start button inside the bot)."""
    try:
        telegram(
            "setMyCommands",
            {
                "commands": json.dumps(
                    [
                        {"command": "start", "description": "🚀 دەستپێکردن"},
                        {"command": "help", "description": "ℹ️ ڕێنمایی"},
                        {"command": "cancel", "description": "✖️ هەڵوەشاندنەوە"},
                    ],
                    ensure_ascii=False,
                ),
            },
        )
        log_error("Bot command menu registered (start/help/cancel).")
    except Exception as exc:
        log_error("setMyCommands failed: " + repr(exc))


def main():
    log_error("========== BOT STARTING ==========")
    log_error("Python version: " + sys.version.replace("\n", " "))
    log_error("Base directory: " + BASE_DIR)
    log_error("Log file: " + LOG_PATH)

    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "FFmpeg دامەزرێنە؛ بۆ ئەم بۆتە پێویستە."
        )

    if shutil.which("ffprobe") is None:
        raise SystemExit(
            "ffprobe لەگەڵ FFmpeg پێویستە."
        )

    for font_info in qv.FONT_FILES.values():
        font_path = os.path.join(BASE_DIR, font_info["file"])
        if not os.path.exists(font_path):
            log_error(
                "WARNING: font file was not found: "
                + font_path
                + " (the bot will try to download it from GitHub)"
            )

    # The blue Telegram Menu button now carries the start command,
    # and every guidance message carries an inline 🚀 start button.
    setup_bot_menu()

    # If an old webhook is still set on this bot token, getUpdates can
    # never work (it fails with 409 forever). Clear it once at startup.
    try:
        telegram("deleteWebhook", {})
    except Exception as exc:
        log_error("deleteWebhook failed (harmless): " + repr(exc))

    keepalive_commit()

    log_error(
        "Bot running; each video is sent only "
        "to its requesting chat."
    )

    offset = None
    conflicts = 0

    while True:
        runtime = time.time() - BOT_START_TIME

        # --- 24/7 handover: shortly before GitHub's 6-hour job limit,
        # start the NEXT instance and keep polling until it takes over,
        # so the bot never goes offline and no video is ever killed. ---
        if runtime >= HANDOVER_AFTER_SECONDS:
            maybe_dispatch_next_run()

        if runtime >= HARD_EXIT_SECONDS:
            log_error(
                "HANDOVER | Hard exit time reached; finishing in-flight "
                "jobs and stopping this instance."
            )
            drain_active_jobs(300)
            break

        try:
            params = {
                "timeout": POLL_TIMEOUT,
                "allowed_updates": json.dumps([
                    "message",
                    "callback_query",
                ]),
            }

            if offset is not None:
                params["offset"] = offset

            updates = telegram(
                "getUpdates",
                params,
                timeout=POLL_TIMEOUT + 10,
            )

            conflicts = 0

            for update in updates:
                offset = update["update_id"] + 1

                # Hand the update to its per-chat worker thread, so a
                # slow video job for one user never blocks other users.
                process_update(update)

        except TelegramConflict:
            # Telegram allows only ONE poller per token. Who should win
            # depends on the situation (asymmetric rules, so the two
            # instances can never fight forever):
            conflicts += 1
            log_error(
                "POLL CONFLICT (%d) | another bot instance is polling"
                % conflicts
            )

            if runtime >= HANDOVER_AFTER_SECONDS or _handover_dispatched:
                # Normal 24/7 handover: a fresh instance took over, so
                # finish the videos that are still rendering and exit.
                log_error(
                    "HANDOVER | A newer bot instance took over (409); "
                    "finishing in-flight jobs and exiting."
                )
                drain_active_jobs(600)
                break

            if conflicts >= 60:
                # Two young instances kept stealing the poll from each
                # other for minutes (duplicate run). Give up; the
                # watchdog will start one clean instance.
                log_error(
                    "POLL CONFLICT | Repeated conflicts; finishing "
                    "in-flight jobs and exiting."
                )
                drain_active_jobs(120)
                break

            # Young instance + no handover yet: take the poll back
            # (this is how the fresh instance wins the handover).
            time.sleep(random.uniform(2.0, 5.0))

        except KeyboardInterrupt:
            log_error("Bot stopped by keyboard interrupt.")
            break

        except Exception as exc:
            log_exception("POLLING ERROR", exc)
            time.sleep(3)


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        log_error("Bot stopped.")

    except Exception as exc:
        log_exception("FATAL BOT ERROR", exc)
        raise
