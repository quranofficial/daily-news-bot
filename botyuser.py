# -*- coding: utf-8 -*-
"""Telegram Quran video bot with reciter selection and timing databases."""

import os
import sys
import json
import time
import shutil
import sqlite3
import subprocess
import traceback
import urllib.request
import urllib.parse
import urllib.error

from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_get_display
except Exception:
    arabic_reshaper = None
    bidi_get_display = None

import quran_video as qv


# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
TG = "https://api.telegram.org/bot" + BOT_TOKEN + "/"
QURAN_API = "https://api.alquran.cloud/v1"

# No fixed maximum ayah count per video.
MAX_AYAHS = None
MAX_BACKGROUND_BYTES = 20 * 1024 * 1024

POLL_TIMEOUT = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "quran_user_jobs")
TIMINGS_DIR = os.path.join(BASE_DIR, "reciter_timings")
RECITERS_JSON_PATH = os.path.join(BASE_DIR, "reciters.json")
LOG_PATH = os.path.join(BASE_DIR, "bot_debug.log")

TIMINGS_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "quranofficial/daily-news-bot/main/"
)

SESSIONS = {}
_SURAH_CACHE = None


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


# =========================
# BACKGROUND VIDEO HELPERS
# =========================

def ensure_preset_background(number, job_dir):
    """Create a preset animated background; option 4 is pure black."""
    number = int(number)
    path = os.path.join(job_dir, "preset_background_%d.mp4" % number)

    if number == 4:
        # Completely black background: no gradient, noise, or decoration.
        run_ffmpeg([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=black:s=720x1280:r=24:d=8",
            "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "30", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", path,
        ])
        return path

    presets = {
        1: ("0b302f", "1c6152"),
        2: ("081a42", "24558c"),
        3: ("30123f", "783d76"),
    }
    if number not in presets:
        number = 1
        path = os.path.join(job_dir, "preset_background_1.mp4")
    start_color, end_color = presets[number]
    run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i",
        "color=c=#%s:s=720x1280:r=24:d=8" % start_color,
        "-vf",
        "drawbox=x=0:y='mod(t*75\\,1280)':w=720:h=260:color=#%s@0.35:t=fill,"
        "noise=alls=8:allf=t+u,format=yuv420p" % end_color,
        "-an", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "30", "-movflags", "+faststart", path,
    ])
    return path


def download_telegram_video(file_id, destination, expected_size=None):
    """Download a video sent by a user through Telegram's Gallery picker."""
    if expected_size and int(expected_size) > MAX_BACKGROUND_BYTES:
        raise RuntimeError("قەبارەی ڤیدیۆکە زۆرە؛ تکایە ڤیدیۆیەک لە ٢٠ MB بچووکتر بنێرە.")

    file_info = telegram("getFile", {"file_id": file_id})
    file_path = file_info.get("file_path")
    file_size = int(file_info.get("file_size") or expected_size or 0)
    if not file_path:
        raise RuntimeError("Telegram نەیتوانی شوێنی فایلەکە بدۆزێتەوە.")
    if file_size > MAX_BACKGROUND_BYTES:
        raise RuntimeError("قەبارەی ڤیدیۆکە زۆرە؛ سنوور ٢٠ MB ـە.")

    url = "https://api.telegram.org/file/bot%s/%s" % (BOT_TOKEN, file_path)
    request = urllib.request.Request(url, headers={"User-Agent": "QuranVideoBot/1.0"})
    total = 0
    with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as output:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BACKGROUND_BYTES:
                output.close()
                try:
                    os.remove(destination)
                except OSError:
                    pass
                raise RuntimeError("قەبارەی ڤیدیۆکە لە ٢٠ MB زیاترە.")
            output.write(chunk)

    if total < 1000:
        raise RuntimeError("فایلی ڤیدیۆکە بە تەواوی دابەزێنراو نییە.")
    # Validate that FFmpeg can read the uploaded media.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", destination],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        try:
            os.remove(destination)
        except OSError:
            pass
        raise RuntimeError("ئەم فایلە ڤیدیۆیەکی پشتڕاستکراو نییە؛ تکایە MP4 یان ڤیدیۆیەکی تر بنێرە.")
    return destination


def keyboard_backgrounds():
    return {"inline_keyboard": [
        [{"text": "🌿 ڤیدیۆی پاشبنەما ١", "callback_data": "background:1"}],
        [{"text": "🌌 ڤیدیۆی پاشبنەما ٢", "callback_data": "background:2"}],
        [{"text": "💜 ڤیدیۆی پاشبنەما ٣", "callback_data": "background:3"}],
        [{"text": "⚫ پاشبنەمای تەواو ڕەش", "callback_data": "background:4"}],
        [{"text": "📂 ڤیدیۆی خۆم لە Gallery", "callback_data": "background:gallery"}],
    ]}


def keyboard_tafsir_choice():
    return {"inline_keyboard": [
        [{"text": "📖 لەگەڵ تەفسیر", "callback_data": "tafsir:yes"}],
        [{"text": "📜 بەبێ تەفسیر", "callback_data": "tafsir:no"}],
    ]}


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
# TRANSPARENT QURAN TEXT OVERLAY
# =========================

OVERLAY_WIDTH = 720
OVERLAY_HEIGHT = 1280


def _font(path, size):
    try:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size=size)
    except Exception:
        pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def _shape_rtl(text):
    text = str(text or "").strip()
    if not text:
        return text
    if arabic_reshaper and bidi_get_display:
        try:
            return bidi_get_display(arabic_reshaper.reshape(text))
        except Exception:
            return text
    return text


def _wrap_text(draw, text, font, max_width):
    words = str(text or "").split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        try:
            width = draw.textbbox((0, 0), _shape_rtl(trial), font=font)[2]
        except Exception:
            width = draw.textlength(_shape_rtl(trial), font=font)
        if width <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_centered_block(draw, text, font_path, start_y, max_width,
                         max_height, start_size, fill, stroke_fill=(0, 0, 0, 220),
                         min_size=18, line_gap=12):
    """Draw transparent text only; no panels or background rectangles."""
    text = str(text or "").strip()
    if not text:
        return start_y
    selected = None
    for size in range(start_size, min_size - 1, -2):
        font = _font(font_path, size)
        lines = _wrap_text(draw, text, font, max_width)
        line_height = size + line_gap
        if len(lines) * line_height <= max_height:
            selected = (font, lines, line_height)
            break
    if selected is None:
        font = _font(font_path, min_size)
        lines = _wrap_text(draw, text, font, max_width)
        selected = (font, lines, min_size + line_gap)
    font, lines, line_height = selected
    y = start_y
    for line in lines:
        shaped = _shape_rtl(line)
        draw.text((OVERLAY_WIDTH // 2, y), shaped, font=font, fill=fill,
                  anchor="mt", align="center", stroke_width=2,
                  stroke_fill=stroke_fill)
        y += line_height
    return y


def create_transparent_ayah_image(ayah, index, image_path, design=1,
                                  show_tafsir=False, ayah_font_size=48,
                                  tafsir_font_size=29):
    """Render surah/ayah/optional tafsir over a fully transparent canvas."""
    image = Image.new("RGBA", (OVERLAY_WIDTH, OVERLAY_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    quran_font = os.path.join(BASE_DIR, "quran.ttf")
    tafsir_font = os.path.join(BASE_DIR, "rabartafsir.ttf")

    # Design changes text accents only; it never paints a separate background.
    accents = {
        1: (244, 211, 135, 255),
        2: (151, 210, 255, 255),
        3: (225, 174, 255, 255),
        4: (255, 219, 169, 255),
        5: (245, 245, 245, 255),
    }
    accent = accents.get(int(design or 1), accents[1])
    white = (255, 255, 255, 255)
    surah_name = str(ayah.get("surah_name") or ayah.get("surah_name_ar") or "القرآن الكريم")
    ayah_number = ayah.get("ayah_number", index)

    # No frame and no filled rectangles: the selected background stays visible.
    header_font = _font(tafsir_font, 32)
    draw.text((OVERLAY_WIDTH // 2, 110), _shape_rtl(surah_name),
              font=header_font, fill=accent, anchor="mt", align="center",
              stroke_width=2, stroke_fill=(0, 0, 0, 230))
    small_font = _font(tafsir_font, 24)
    draw.text((OVERLAY_WIDTH // 2, 158), _shape_rtl("ئایەت " + str(ayah_number)),
              font=small_font, fill=white, anchor="mt", align="center",
              stroke_width=2, stroke_fill=(0, 0, 0, 230))

    arabic = str(ayah.get("arabic") or ayah.get("text") or "").strip()
    if show_tafsir:
        verse_y, verse_height = 245, 320
    else:
        verse_y, verse_height = 320, 650
    after_verse = _draw_centered_block(
        draw, arabic, quran_font, verse_y, 620, verse_height,
        int(ayah_font_size), white, min_size=24, line_gap=15,
    )

    if show_tafsir:
        label_font = _font(tafsir_font, 30)
        label_y = max(625, after_verse + 35)
        draw.text((OVERLAY_WIDTH // 2, label_y), _shape_rtl("تەفسیری ئاسان"),
                  font=label_font, fill=accent, anchor="mt", align="center",
                  stroke_width=2, stroke_fill=(0, 0, 0, 230))
        tafsir_text = str(ayah.get("tafsir") or ayah.get("translation") or "").strip()
        _draw_centered_block(
            draw, tafsir_text, tafsir_font, label_y + 55, 610, 390,
            int(tafsir_font_size), white, min_size=16, line_gap=8,
        )

    image.save(image_path, "PNG")
    return image_path


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


def build_video(chat_id, ayahs, reciter_id, design=1, background_video=None,
                show_tafsir=False, tafsir_start=None, tafsir_end=None,
                ayah_font_size=48, tafsir_font_size=29):
    stamp = str(int(time.time()))

    job_dir = os.path.join(
        JOBS_DIR,
        str(chat_id),
        stamp,
    )

    os.makedirs(job_dir, exist_ok=True)

    log_error(
        "VIDEO JOB START | chat_id=%s | reciter_id=%s | job_dir=%s"
        % (chat_id, reciter_id, job_dir)
    )

    try:
        surah_number = int(ayahs[0]["surah_number"])

        full_audio_path = os.path.join(
            job_dir,
            "%03d.mp3" % surah_number,
        )

        download_surah_audio(
            surah_number,
            reciter_id,
            full_audio_path,
        )

        timings = get_ayah_timings(
            reciter_id,
            surah_number,
        )

        audio_duration = get_audio_duration(
            full_audio_path
        )

        clips = []

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

            image_path = os.path.join(
                job_dir,
                "image_%03d.png" % i,
            )

            clip_path = os.path.join(
                job_dir,
                "clip_%03d.mp4" % i,
            )

            ayah_show_tafsir = bool(show_tafsir)
            if ayah_show_tafsir:
                ayah_show_tafsir = (
                    tafsir_start is not None
                    and tafsir_end is not None
                    and int(tafsir_start) <= ayah_number <= int(tafsir_end)
                )
            create_transparent_ayah_image(
                ayah,
                i,
                image_path,
                design=design,
                show_tafsir=ayah_show_tafsir,
                ayah_font_size=ayah_font_size,
                tafsir_font_size=tafsir_font_size,
            )

            if background_video:
                # Put the existing verse design semi-transparent over the moving background.
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-stream_loop", "-1", "-i", background_video,
                    "-loop", "1", "-framerate", "24", "-i", image_path,
                    "-ss", "%.3f" % start_time, "-i", full_audio_path,
                    "-filter_complex",
                    "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[bg];"
                    "[1:v]scale=720:1280,format=rgba[fg];"
                    "[bg][fg]overlay=shortest=1,format=yuv420p[v]",
                    "-map", "[v]", "-map", "2:a:0",
                    "-t", "%.3f" % duration,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                    "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
                    "-shortest", "-movflags", "+faststart", clip_path,
                ])
            else:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-loop", "1",
                    "-i", image_path,
                    "-ss", "%.3f" % start_time,
                    "-i", full_audio_path,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-t", "%.3f" % duration,
                    "-c:v", "libx264",
                    "-tune", "stillimage",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-pix_fmt", "yuv420p",
                    "-vf",
                    "scale=720:1280:force_original_aspect_ratio=decrease,"
                    "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
                    "-shortest",
                    "-movflags", "+faststart",
                    clip_path,
                ])

            clips.append(clip_path)

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

        output = os.path.join(
            job_dir,
            "quran_video.mp4",
        )

        run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            output,
        ])

        if (
            not os.path.isfile(output)
            or os.path.getsize(output) == 0
        ):
            raise RuntimeError(
                "فایلی ڤیدیۆی کۆتایی دروست نەکرا."
            )

        log_error(
            "VIDEO JOB SUCCESS | output=%s | size=%s"
            % (output, os.path.getsize(output))
        )

        return output, job_dir

    except Exception as exc:
        log_exception("VIDEO JOB FAILED", exc)
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def keyboard_designs():
    names = [
        ("🌿 ١. زمردی و زێڕین", 1),
        ("🌌 ٢. شەوی شین", 2),
        ("💜 ٣. مۆر و زێڕ", 3),
        ("🏜️ ٤. خاکی و کرێمی", 4),
        ("⚪ ٥. سپی و ڕوون", 5),
    ]
    return {"inline_keyboard": [
        [{"text": name, "callback_data": "design:%d" % number}]
        for name, number in names
    ]}


def keyboard_ayah_font_sizes():
    sizes = [36, 42, 48, 54, 60]
    return {"inline_keyboard": [
        [{"text": "%d px" % size, "callback_data": "ayahsize:%d" % size}
         for size in sizes[:3]],
        [{"text": "%d px" % size, "callback_data": "ayahsize:%d" % size}
         for size in sizes[3:]],
    ]}


def keyboard_tafsir_font_sizes():
    sizes = [18, 22, 26, 30, 34]
    return {"inline_keyboard": [
        [{"text": "%d px" % size, "callback_data": "tafsirsize:%d" % size}
         for size in sizes[:3]],
        [{"text": "%d px" % size, "callback_data": "tafsirsize:%d" % size}
         for size in sizes[3:]],
    ]}


def create_selected_video(chat_id, background_choice=None, uploaded_background=None, design=1):
    session = SESSIONS.get(chat_id)
    if not session or session.get("state") not in (
        "background", "background_upload", "design", "confirm_background",
        "ayah_font_size", "tafsir_font_size",
    ):
        send_message(chat_id, "داواکارییەکەت بەسەرچووە؛ /start بنووسە.")
        return

    if uploaded_background is None:
        uploaded_background = session.get("uploaded_background")
    if background_choice is None:
        background_choice = session.get("background_choice", 1)

    job_dir = None
    try:
        surah_number = int(session["surah_number"])
        start_ayah = int(session["start_ayah"])
        end_ayah = int(session["end_ayah"])
        reciter_id = str(session["reciter_id"])
        show_tafsir = bool(session.get("show_tafsir", False))
        tafsir_start = session.get("tafsir_start")
        tafsir_end = session.get("tafsir_end")
        design = int(session.get("design", design) or design)
        ayah_font_size = int(session.get("ayah_font_size", 48))
        tafsir_font_size = int(session.get("tafsir_font_size", 29))
        ayahs = find_surah_rows(surah_number, start_ayah, end_ayah)
        surah = next(s for s in get_surahs() if int(s["number"]) == surah_number)
        reciter_name = RECITERS[reciter_id]["name"]

        # Create the job directory first so preset backgrounds can live inside it.
        stamp = str(int(time.time()))
        job_dir = os.path.join(JOBS_DIR, str(chat_id), stamp)
        os.makedirs(job_dir, exist_ok=True)

        if uploaded_background:
            background_video = uploaded_background
            background_label = "ڤیدیۆی خۆت"
        else:
            background_video = ensure_preset_background(int(background_choice), job_dir)
            background_label = "پاشبنەمای %s" % background_choice

        send_message(
            chat_id,
            "⏳ ڤیدیۆکەت دروست دەکەم...\n"
            "🎙️ قاری: %s\nسوورەت: %s\nئایەت: %d–%d\n"
            "🎞️ پاشبنەما: %s\n"
            "📝 تەفسیر: %s\n"
            "🎨 شێوازی نووسین: %s\nتکایە چاوەڕێ بکە."
            % (reciter_name, surah["name"], start_ayah, end_ayah,
               background_label,
               ("ئایەت %s–%s" % (tafsir_start, tafsir_end)) if show_tafsir else "بەبێ تەفسیر",
               design),
        )

        # build_video creates its own job directory; use the background copied into it.
        build_background = background_video
        if uploaded_background:
            stable_bg = os.path.join(job_dir, "uploaded_background.mp4")
            shutil.copy2(uploaded_background, stable_bg)
            build_background = stable_bg

        video_path, build_job_dir = build_video(
            chat_id, ayahs, reciter_id, design=design,
            background_video=build_background,
            show_tafsir=show_tafsir,
            tafsir_start=tafsir_start,
            tafsir_end=tafsir_end,
            ayah_font_size=ayah_font_size,
            tafsir_font_size=tafsir_font_size,
        )
        try:
            send_video(
                chat_id, video_path,
                "قورئانی پیرۆز — %s\nقاری: %s\nئایەت %d–%d\nتەفسیر: %s"
                % (surah["name"], reciter_name, start_ayah, end_ayah,
                   ("ئایەت %s–%s" % (tafsir_start, tafsir_end)) if show_tafsir else "بەبێ تەفسیر"),
            )
            send_message(chat_id, "✅ ڤیدیۆکە بە سەرکەوتوویی بۆ تۆ نێردرا.")
        finally:
            shutil.rmtree(build_job_dir, ignore_errors=True)
    except Exception as exc:
        log_exception("VIDEO CREATION ERROR | chat_id=%s" % chat_id, exc)
        try:
            send_message(chat_id, "❌ نەمتوانی ڤیدیۆکە دروست بکەم.\n" + str(exc))
        except Exception as send_exc:
            log_exception("Could not send error to Telegram", send_exc)
    finally:
        # Keep session only if waiting for a Gallery video; otherwise finish this request.
        current = SESSIONS.get(chat_id, {})
        if current.get("state") != "background_upload":
            uploaded_path = current.get("uploaded_background")
            if uploaded_path:
                shutil.rmtree(os.path.dirname(uploaded_path), ignore_errors=True)
            SESSIONS.pop(chat_id, None)
        if job_dir:
            shutil.rmtree(job_dir, ignore_errors=True)


# =========================
# BOT COMMANDS
# =========================

def start(chat_id):
    log_error("START COMMAND | chat_id=%s" % chat_id)

    SESSIONS[chat_id] = {"state": "reciter"}

    send_message(
        chat_id,
        "بەخێربێیت 🌙\n\n"
        "🎙️ سەرەتا قاریی دڵخوازی خۆت هەڵبژێرە:",
        keyboard_reciters(),
    )


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
        start(chat_id)
        return

    if text.startswith("/help"):
        send_message(
            chat_id,
            "/start — دەستپێکردن\n"
            "/cancel — هەڵوەشاندنەوە\n\n"
            "قاری هەڵبژێرە، سوورەت دیاری بکە، "
            "پاشان ژمارەی ئایەتی دەستپێک و کۆتایی بنووسە.\n"
            "ژمارەی ئایەتەکان بە پێی سنووری سوورەت "
            "و توانای سێرڤەر دیاری دەکرێت.",
        )
        return

    if text.startswith("/cancel"):
        SESSIONS.pop(chat_id, None)

        send_message(
            chat_id,
            "داواکارییەکەت هەڵوەشایەوە. "
            "بۆ دەستپێکردن /start بنووسە.",
        )
        return

    session = SESSIONS.get(chat_id)

    # Users can choose a clip from their phone Gallery and send it to the bot.
    if session and session.get("state") == "background_upload":
        media = message.get("video")
        document = message.get("document")
        file_id = None
        file_size = None
        if media:
            file_id = media.get("file_id")
            file_size = media.get("file_size")
        elif document:
            mime = (document.get("mime_type") or "").lower()
            filename = (document.get("file_name") or "").lower()
            if mime.startswith("video/") or filename.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv")):
                file_id = document.get("file_id")
                file_size = document.get("file_size")
        if file_id:
            try:
                if file_size and int(file_size) > MAX_BACKGROUND_BYTES:
                    raise RuntimeError("قەبارەی ڤیدیۆکە زۆرە؛ تکایە ڤیدیۆیەک لە ٢٠ MB بچووکتر بنێرە.")
                upload_dir = os.path.join(JOBS_DIR, str(chat_id), "gallery_upload")
                os.makedirs(upload_dir, exist_ok=True)
                upload_path = os.path.join(upload_dir, "user_background.mp4")
                send_message(chat_id, "⏬ ڤیدیۆکەت وەردەگرم، تکایە چاوەڕێ بکە...")
                download_telegram_video(file_id, upload_path, file_size)
                # Store the path and wait for a simple confirmation callback.
                session["uploaded_background"] = upload_path
                session["state"] = "confirm_background"
                send_message(
                    chat_id,
                    "✅ ڤیدیۆکە وەرگیرا. ئەم ڤیدیۆیە وەک پاشبنەما بەکاربهێنم؟",
                    {"inline_keyboard": [
                        [{"text": "✅ بەڵێ، بەکاریبهێنە", "callback_data": "background:use_upload"}],
                        [{"text": "↩️ گەڕانەوە بۆ پاشبنەماکان", "callback_data": "background:back"}],
                    ]},
                )
            except Exception as exc:
                log_exception("BACKGROUND UPLOAD FAILED | chat_id=%s" % chat_id, exc)
                send_message(chat_id, "❌ نەمتوانی ئەم ڤیدیۆیە وەربگرم. " + str(exc) + "\nڤیدیۆیەکی تر بنێرە یان /cancel بکە.")
            return
        send_message(chat_id, "تکایە ڤیدیۆیەک لە Gallery هەڵبژێرە و وەک ڤیدیۆ بینێرە (تا ٢٠ MB).")
        return

    if not session:
        send_message(
            chat_id,
            "بۆ دەستپێکردن /start بنووسە.",
        )
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
            session["state"] = "tafsir_choice"
            send_message(
                chat_id,
                "ئایا دەتەوێت تەفسیر لە ڤیدیۆکەدا پیشان بدرێت؟\n\n"
                "ئەگەر بەڵێ هەڵبژێریت، دواتر دەستپێک و کۆتایی مەودای تەفسیر دیاری دەکەیت."
                , keyboard_tafsir_choice(),
            )
        except Exception as exc:
            send_message(chat_id, "ژمارەی ئایەتی کۆتایی دروست نییە: " + str(exc))
        return

    if session.get("state") in ("tafsir_start", "tafsir_end"):
        try:
            number = int(text)
            start_ayah = int(session["start_ayah"])
            end_ayah = int(session["end_ayah"])
            if number < start_ayah or number > end_ayah:
                raise ValueError("ژمارەکە دەبێت لە %d تا %d بێت." % (start_ayah, end_ayah))
            if session.get("state") == "tafsir_start":
                session["tafsir_start"] = number
                session["state"] = "tafsir_end"
                send_message(chat_id, "دەستپێکی تەفسیر: %d\nئێستا ژمارەی ئایەتی کۆتایی تەفسیر بنووسە (%d–%d)." % (number, number, end_ayah))
            else:
                tafsir_start = int(session["tafsir_start"])
                if number < tafsir_start:
                    raise ValueError("کۆتایی تەفسیر نابێت بچووکتر بێت لە دەستپێکی تەفسیر.")
                session["tafsir_end"] = number
                session["state"] = "background"
                send_message(chat_id, "🎞️ ئێستا پاشبنەمای ڤیدیۆکەت هەڵبژێرە:", keyboard_backgrounds())
        except Exception as exc:
            send_message(chat_id, "ژمارەی مەودای تەفسیر دروست نییە. " + str(exc))
        return

    send_message(
        chat_id,
        "تکایە بە هەنگاوەکانی بۆتەکە بەردەوام بە.",
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
        if data.startswith("background:"):
            choice = data.split(":", 1)[1]
            session = SESSIONS.get(chat_id)
            if not session:
                answer_callback(callback_id, "داواکارییەکە بەسەرچووە")
                return
            if choice in ("1", "2", "3", "4"):
                if session.get("state") != "background":
                    answer_callback(callback_id, "سەرەتا هەنگاوەکان تەواو بکە")
                    return
                session["background_choice"] = int(choice)
                session["state"] = "design"
                answer_callback(callback_id, "پاشبنەما هەڵبژێردرا")
                send_message(chat_id, "🎨 ڕەنگی نووسین هەڵبژێرە؛ دواتر قەبارەی ئایەت و تەفسیر بە جیا هەڵدەبژێریت:", keyboard_designs())
                return
            if choice == "gallery":
                session["state"] = "background_upload"
                answer_callback(callback_id, "ڤیدیۆکەت بنێرە")
                send_message(chat_id, "📂 ئێستا ڤیدیۆکەت لە Gallery هەڵبژێرە و بینێرە بۆ ئەم بۆتە.\nقەبارە: تا ٢٠ MB. باشترە MP4 و ستوونی بێت.")
                return
            if choice == "use_upload":
                upload_path = session.get("uploaded_background")
                if not upload_path or not os.path.isfile(upload_path):
                    answer_callback(callback_id, "ڤیدیۆکە نەماوە؛ تکایە دووبارە بینێرە")
                    session["state"] = "background_upload"
                    return
                session["state"] = "design"
                session["background_choice"] = 1
                answer_callback(callback_id, "پاشبنەما هەڵبژێردرا")
                send_message(chat_id, "🎨 ڕەنگی نووسین هەڵبژێرە:", keyboard_designs())
                return
            if choice == "back":
                session.pop("uploaded_background", None)
                session["state"] = "background"
                answer_callback(callback_id)
                send_message(chat_id, "🎞️ پاشبنەمایەک هەڵبژێرە:", keyboard_backgrounds())
                return

        if data.startswith("tafsir:"):
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "tafsir_choice":
                answer_callback(callback_id, "سەرەتا مەودای ئایەت دیاری بکە")
                return
            choice = data.split(":", 1)[1]
            if choice == "yes":
                session["show_tafsir"] = True
                session["state"] = "tafsir_start"
                answer_callback(callback_id, "تەفسیر چالاک کرا")
                send_message(chat_id, "ژمارەی ئایەتی دەستپێکی تەفسیر بنووسە (%d–%d)." % (int(session["start_ayah"]), int(session["end_ayah"])))
            elif choice == "no":
                session["show_tafsir"] = False
                session.pop("tafsir_start", None)
                session.pop("tafsir_end", None)
                session["state"] = "background"
                answer_callback(callback_id, "بەبێ تەفسیر")
                send_message(chat_id, "🎞️ ئێستا پاشبنەمای ڤیدیۆکەت هەڵبژێرە:", keyboard_backgrounds())
            else:
                answer_callback(callback_id, "هەڵبژاردنەکە نادروستە")
            return

        if data.startswith("design:"):
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "design":
                answer_callback(callback_id, "سەرەتا پاشبنەما هەڵبژێرە")
                return
            design = int(data.split(":", 1)[1])
            if design not in (1, 2, 3, 4, 5):
                design = 1
            session["design"] = design
            session["state"] = "ayah_font_size"
            answer_callback(callback_id, "شێوازی نووسین هەڵبژێردرا")
            send_message(
                chat_id,
                "🔠 قەبارەی نووسینی ئایەت هەڵبژێرە:",
                keyboard_ayah_font_sizes(),
            )
            return

        if data.startswith("ayahsize:"):
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "ayah_font_size":
                answer_callback(callback_id, "سەرەتا دیزاین هەڵبژێرە")
                return
            size = int(data.split(":", 1)[1])
            if size not in (36, 42, 48, 54, 60):
                size = 48
            session["ayah_font_size"] = size
            if session.get("show_tafsir"):
                session["state"] = "tafsir_font_size"
                answer_callback(callback_id, "قەبارەی ئایەت هەڵبژێردرا")
                send_message(
                    chat_id,
                    "🔠 ئێستا قەبارەی نووسینی تەفسیر هەڵبژێرە:",
                    keyboard_tafsir_font_sizes(),
                )
            else:
                answer_callback(callback_id, "قەبارە هەڵبژێردرا؛ ڤیدیۆ دروست دەکرێت")
                create_selected_video(
                    chat_id,
                    session.get("background_choice", 1),
                    uploaded_background=session.get("uploaded_background"),
                    design=session.get("design", 1),
                )
            return

        if data.startswith("tafsirsize:"):
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "tafsir_font_size":
                answer_callback(callback_id, "سەرەتا قەبارەی ئایەت هەڵبژێرە")
                return
            size = int(data.split(":", 1)[1])
            if size not in (18, 22, 26, 30, 34):
                size = 26
            session["tafsir_font_size"] = size
            answer_callback(callback_id, "قەبارە هەڵبژێردرا؛ ڤیدیۆ دروست دەکرێت")
            create_selected_video(
                chat_id,
                session.get("background_choice", 1),
                uploaded_background=session.get("uploaded_background"),
                design=session.get("design", 1),
            )
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
# MAIN LOOP
# =========================

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

    if not os.path.exists(
        os.path.join(BASE_DIR, "quran.ttf")
    ):
        log_error(
            "WARNING: quran.ttf was not found in the bot directory."
        )

    log_error(
        "Bot running; each video is sent only "
        "to its requesting chat."
    )

    offset = None

    while True:
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

            for update in updates:
                offset = update["update_id"] + 1

                if "message" in update:
                    handle_message(update["message"])

                elif "callback_query" in update:
                    handle_callback(update["callback_query"])

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
