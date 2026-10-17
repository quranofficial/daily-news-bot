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
TIMINGS_DIR = os.path.join(BASE_DIR, "reciter_timings")
BACKGROUND_DIR = BASE_DIR
BACKGROUND_BASE_URL = "https://raw.githubusercontent.com/quranofficial/daily-news-bot/main/"
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


def send_photo(chat_id, path, caption):
    with open(path, "rb") as f:
        telegram(
            "sendPhoto",
            {"chat_id": chat_id, "caption": caption},
            {"photo": (os.path.basename(path), f.read(), "image/png")},
            timeout=180,
        )


def send_background_previews(chat_id, kind):
    """Send the five GitHub background previews, then show selection buttons."""
    ext = "png" if kind == "image" else "mp4"
    for number in range(1, 6):
        local_path = os.path.join(BACKGROUND_DIR, "%d.%s" % (number, ext))
        if not os.path.isfile(local_path):
            try:
                download_file(BACKGROUND_BASE_URL + "%d.%s" % (number, ext), local_path, min_size=1000)
            except Exception as exc:
                log_error("BACKGROUND PREVIEW UNAVAILABLE | %s | %r" % (local_path, exc))
                send_message(chat_id, "⚠️ فایل %d.%s لە GitHub نییە. دڵنیابە ناوەکە و شوێنی فایلەکە ڕاستە." % (number, ext))
                continue
        try:
            if kind == "image":
                send_photo(chat_id, local_path, "🖼️ وێنەی پاشبنەما %d/5" % number)
            else:
                send_video(chat_id, local_path, "🎬 ڤیدیۆی پاشبنەما %d/5" % number)
        except Exception as exc:
            log_exception("BACKGROUND PREVIEW SEND FAILED", exc)
            send_message(chat_id, "نەتوانرا پێشبینینی فایل %d بنێردرێت." % number)
    send_message(chat_id, "⬇️ ئێستا ژمارەی ئەو پاشبنەمایە هەڵبژێرە کە دەتەوێت، یان لە گەلەری مۆبایل هەڵیبژێرە:", keyboard_backgrounds(kind))


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


def build_video(chat_id, ayahs, reciter_id, design=1, background_path=None, background_kind="image", include_tafsir=True, text_settings=None):
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

            qv.create_ayah_image(
                ayah,
                i,
                image_path,
                design=design,
                include_tafsir=include_tafsir,
                settings=text_settings or {},
            )

            bg_path = background_path
            if background_kind == "black":
                bg_input = ["-f", "lavfi", "-i", "color=c=black:s=720x1280:r=24"]
            else:
                if not bg_path or not os.path.isfile(bg_path):
                    raise RuntimeError("Background ـی هەڵبژێردراو نەدۆزرایەوە.")
                bg_input = (["-stream_loop", "-1", "-i", bg_path]
                            if background_kind == "video"
                            else ["-loop", "1", "-i", bg_path])
            ffmpeg_args = ["ffmpeg", "-y"] + bg_input + [
                "-loop", "1", "-i", image_path,
                "-ss", "%.3f" % start_time,
                "-i", full_audio_path,
                "-filter_complex",
                "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280[bg];"
                "[1:v]scale=720:1280,format=rgba[ov];[bg][ov]overlay=0:0:shortest=1[v]",
                "-map", "[v]", "-map", "2:a:0",
                "-t", "%.3f" % duration,
                "-c:v", "libx264", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "128k",
                "-pix_fmt", "yuv420p", "-shortest",
                "-movflags", "+faststart", clip_path,
            ]
            run_ffmpeg(ffmpeg_args)

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


def keyboard_output_modes():
    return {"inline_keyboard": [
        [{"text": "🖼️ ١. ڕسم", "callback_data": "mode:image"}],
        [{"text": "🎬 ٢. ڤیدیۆ", "callback_data": "mode:video"}],
        [{"text": "⚫ پاشبنەمای ڕەشی تەواو", "callback_data": "mode:black"}],
    ]}


def keyboard_backgrounds(kind):
    rows = []
    for number in range(1, 6):
        ext = "png" if kind == "image" else "mp4"
        rows.append([{"text": "%s %d" % ("🖼️" if kind == "image" else "🎬", number),
                      "callback_data": "background:%s:%d" % (kind, number)}])
    rows.append([{"text": "📱 هەڵبژاردن لە گەلەری مۆبایل", "callback_data": "gallery:%s" % kind}])
    return {"inline_keyboard": rows}


def keyboard_tafsir():
    return {"inline_keyboard": [
        [{"text": "📖 لەگەڵ تەفسیر", "callback_data": "tafsir:on"}],
        [{"text": "🕋 تەنها ئایەت، بێ تەفسیر", "callback_data": "tafsir:off"}],
    ]}



def keyboard_text_settings():
    return {"inline_keyboard": [
        [{"text": "🎨 ڕەنگی ئایەت: سپی", "callback_data": "setting:ayah_color"},
         {"text": "🟡 ڕەنگی تەفسیر: زەرد", "callback_data": "setting:tafsir_color"}],
        [{"text": "🔠 قەبارەی ئایەت", "callback_data": "setting:ayah_size"},
         {"text": "🔡 قەبارەی تەفسیر", "callback_data": "setting:tafsir_size"}],
        [{"text": "📝 قەبارەی ناو و ژمارە", "callback_data": "setting:other_size"}],
        [{"text": "⬆️ شوێنی ئایەت", "callback_data": "setting:ayah_y"},
         {"text": "⬇️ شوێنی تەفسیر", "callback_data": "setting:tafsir_y"}],
        [{"text": "✅ تەواوە، دروستکردنی ڤیدیۆ", "callback_data": "settings:done"}],
    ]}


def keyboard_setting_values(setting):
    choices = {
        "ayah_color": [("سپی", (255,255,255)), ("زەرد", (255,210,90)), ("سەوز", (130,255,170)), ("شین", (130,200,255))],
        "tafsir_color": [("زەرد", (255,210,90)), ("سپی", (255,255,255)), ("سەوز", (130,255,170)), ("شین", (130,200,255))],
        "ayah_size": [("بچووک", 34), ("مامناوەند", 44), ("گەورە", 54), ("زۆر گەورە", 62)],
        "tafsir_size": [("بچووک", 22), ("مامناوەند", 30), ("گەورە", 36), ("زۆر گەورە", 42)],
        "other_size": [("بچووک", 18), ("مامناوەند", 24), ("گەورە", 30), ("زۆر گەورە", 36)],
        "ayah_y": [("سەرەوە", 190), ("ناوەڕاست", 300), ("خوارەوە", 420)],
        "tafsir_y": [("ناوەڕاست", 600), ("خوارەوە", 730), ("زۆر خوارەوە", 830)],
    }
    rows = []
    for label, value in choices[setting]:
        encoded = ",".join(map(str, value)) if isinstance(value, tuple) else str(value)
        rows.append([{"text": label, "callback_data": "setval:%s:%s" % (setting, encoded)}])
    rows.append([{"text": "↩️ گەڕانەوە بۆ ڕێکخستن", "callback_data": "settings:back"}])
    return {"inline_keyboard": rows}


def keyboard_designs():
    names = [
        ("🏔️ ١. شاخ و ئاویشار", 1),
        ("🕌 ٢. کەوانەی مزگەوت", 2),
        ("🌙 ٣. شەوی ئەستێردار", 3),
        ("🏜️ ٤. خۆرئاوابوون", 4),
        ("🌿 ٥. دارستانی سەوز", 5),
        ("📜 ٦. پەڕەی کۆن", 6),
        ("💎 ٧. کریستاڵی شەفاف", 7),
        ("👑 ٨. شاهانەی زێڕین", 8),
        ("🌌 ٩. ئاسمانی نیلی", 9),
        ("🍁 ١٠. پاییزی زێڕین", 10),
    ]
    return {"inline_keyboard": [
        [{"text": name, "callback_data": "design:%d" % number}]
        for name, number in names
    ]}


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


def prompt_design(chat_id):
    session = SESSIONS[chat_id]
    session["state"] = "design"
    send_message(chat_id, "🎨 ئێستا یەکێک لە ٥ دیزاینەکە هەڵبژێرە:", keyboard_designs())


def create_selected_video(chat_id, design):
    session = SESSIONS.get(chat_id)
    if not session or session.get("state") != "design":
        send_message(chat_id, "داواکارییەکەت بەسەرچووە؛ /start بنووسە.")
        return
    job_dir = None
    try:
        surah_number = int(session["surah_number"])
        start_ayah = int(session["start_ayah"])
        end_ayah = int(session["end_ayah"])
        reciter_id = str(session["reciter_id"])
        ayahs = find_surah_rows(surah_number, start_ayah, end_ayah)
        surah = next(s for s in get_surahs() if int(s["number"]) == surah_number)
        reciter_name = RECITERS[reciter_id]["name"]
        send_message(chat_id, "⏳ ڤیدیۆکەت دروست دەکەم...\n🎙️ قاری: %s\nسوورەت: %s\nئایەتەکان: %d–%d\n🎨 دیزاین: %d/10\nتکایە چاوەڕێ بکە." % (reciter_name, surah["name"], start_ayah, end_ayah, design))
        video_path, job_dir = build_video(
            chat_id, ayahs, reciter_id,
            design=design,
            background_path=session.get("background_path"),
            background_kind=session.get("background_kind", "image"),
            include_tafsir=bool(session.get("include_tafsir", True)),
            text_settings=session.get("text_settings", {}),
        )
        send_video(chat_id, video_path, "قورئانی پیرۆز — %s\nقاری: %s\nئایەت %d–%d" % (surah["name"], reciter_name, start_ayah, end_ayah))
        send_message(chat_id, "✅ ڤیدیۆکە بە سەرکەوتوویی بۆ تۆ نێردرا.")
    except Exception as exc:
        log_exception("VIDEO CREATION ERROR | chat_id=%s" % chat_id, exc)
        try:
            send_message(chat_id, "❌ نەمتوانی ڤیدیۆکە دروست بکەم.\n" + str(exc))
        except Exception as send_exc:
            log_exception("Could not send error to Telegram", send_exc)
    finally:
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

    if not session:
        send_message(
            chat_id,
            "بۆ دەستپێکردن /start بنووسە.",
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
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", destination], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

    if message.get("photo") or message.get("video"):
        send_message(chat_id, "بۆ بەکارهێنانی گەلەری، سەرەتا /start بنووسە و قۆناغەکان تەواو بکە.")
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
            send_message(chat_id, "دەتەوێت پاشبنەماکە ڕسم بێت یان ڤیدیۆ؟", keyboard_output_modes())
        except Exception as exc:
            send_message(chat_id, "ژمارەی ئایەتی کۆتایی دروست نییە: " + str(exc))
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
            edit_message(chat_id, message_id, "⏳ پێشبینینی ٥ پاشبنەما دەنێرم...")
            answer_callback(callback_id)
            send_background_previews(chat_id, kind)
            return

        if data.startswith("gallery:"):
            kind = data.split(":", 1)[1]
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "background_choice":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["background_kind"] = kind
            session["state"] = "await_gallery_upload"
            answer_callback(callback_id)
            send_message(chat_id, "📱 ئێستا %s ـەکەت لە گەلەری بنێرە بۆ بۆتەکە." % ("وێنە" if kind == "image" else "ڤیدیۆی MP4"))
            return

        if data.startswith("background:"):
            _, kind, raw_number = data.split(":", 2)
            number = int(raw_number)
            if kind not in ("image", "video") or number not in range(1, 6):
                raise ValueError("پاشبنەماکە دروست نییە.")
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "background_choice":
                raise ValueError("تکایە /start دووبارە بکە.")
            ext = "png" if kind == "image" else "mp4"
            local_path = os.path.join(BACKGROUND_DIR, "%d.%s" % (number, ext))
            if not os.path.isfile(local_path):
                url = BACKGROUND_BASE_URL + "%d.%s" % (number, ext)
                try:
                    download_file(url, local_path, min_size=1000)
                except Exception as exc:
                    raise RuntimeError("فایلی %d.%s لە GitHub نەدۆزرایەوە. دڵنیابە فایلەکە لە ڕەگی repository ـەکەت بارکراوە. %s" % (number, ext, exc))
            session["background_path"] = local_path
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
            session["text_settings"] = {"plain_overlay": True, "ayah_color": (255,255,255), "tafsir_color": (255,210,90), "other_color": (255,255,255), "ayah_size": 44, "tafsir_size": 30, "other_size": 24, "ayah_y": 250, "tafsir_y": 690, "other_y": 115}
            session["state"] = "text_settings"
            answer_callback(callback_id, "هەڵبژێردرا")
            send_message(chat_id, "🎨 ڕێکخستنی دەق: ڕەنگ، قەبارە و شوێنی ئایەت و تەفسیر هەڵبژێرە. هیچ پەنجەرە و پاشبنەمای تایبەت بۆ دەقەکان نییە.", keyboard_text_settings())
            return

        if data.startswith("setting:"):
            session = SESSIONS.get(chat_id)
            setting = data.split(":", 1)[1]
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            answer_callback(callback_id)
            send_message(chat_id, "هەڵبژاردەی خۆت بۆ %s دیاری بکە:" % setting, keyboard_setting_values(setting))
            return

        if data.startswith("setval:"):
            _, setting, raw = data.split(":", 2)
            session = SESSIONS.get(chat_id)
            allowed = {"ayah_color", "tafsir_color", "ayah_size", "tafsir_size", "other_size", "ayah_y", "tafsir_y"}
            if not session or session.get("state") != "text_settings" or setting not in allowed:
                raise ValueError("ڕێکخستنەکە دروست نییە.")
            if setting.endswith("color"):
                value = tuple(int(x) for x in raw.split(","))
                if len(value) != 3 or any(x < 0 or x > 255 for x in value): raise ValueError("ڕەنگی دروست نییە.")
            else:
                value = int(raw)
            session.setdefault("text_settings", {"plain_overlay": True})[setting] = value
            answer_callback(callback_id, "ڕێکخستن هەڵگیرا")
            send_message(chat_id, "✅ ڕێکخستنەکە گۆڕدرا. دەتوانیت ڕێکخستنێکی تر هەڵبژێریت:", keyboard_text_settings())
            return

        if data == "settings:back":
            answer_callback(callback_id)
            send_message(chat_id, "🎨 ڕێکخستنی دەق:", keyboard_text_settings())
            return

        if data == "settings:done":
            session = SESSIONS.get(chat_id)
            if not session or session.get("state") != "text_settings":
                raise ValueError("تکایە /start دووبارە بکە.")
            session["state"] = "design"
            answer_callback(callback_id, "ڕێکخستنەکان هەڵگیرا")
            send_message(chat_id, "دیزاینی سادەی دەق هەڵبژێرە؛ دەقەکان بەبێ پەنجەرە دەرکەون:", keyboard_designs())
            return

        if data.startswith("design:"):
            design = int(data.split(":", 1)[1])
            if design not in range(1, 11):
                raise ValueError("دیزاینەکە دروست نییە.")
            answer_callback(callback_id, "دیزاین هەڵبژێردرا")
            create_selected_video(chat_id, design)
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
