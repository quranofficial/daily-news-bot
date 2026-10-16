
# -*- coding: utf-8 -*-
"""Telegram Quran video bot with 5 video designs."""

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

DESIGN_NAMES = {
    1: "سەوزی زمردی و زێڕ",
    2: "ڕەش و زێڕ",
    3: "شینی ئاسمانی",
    4: "کرێمی کلاسیک",
    5: "مۆر و مۆدێرن",
}


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
# LOAD RECITERS
# =========================

def load_reciters():
    if not os.path.isfile(RECITERS_JSON_PATH):
        raise FileNotFoundError(
            "فایلی reciters.json لە فۆڵدەری بۆتەکە نەدۆزرایەوە: "
            + RECITERS_JSON_PATH
        )

    try:
        with open(
            RECITERS_JSON_PATH,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

    except json.JSONDecodeError as exc:
        raise ValueError(
            "فایلی reciters.json دروست نییە: " + str(exc)
        )

    if not isinstance(data, list):
        raise ValueError(
            "ناوەڕۆکی reciters.json دەبێت لیستێک بێت."
        )

    reciters = {}

    for item in data:
        if not isinstance(item, dict):
            continue

        enabled = str(
            item.get("doxx", "")
        ).strip().lower()

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
            "هیچ قارییەکی چالاک بە doxx=true "
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
                "Content-Type": (
                    "multipart/form-data; boundary=" + boundary
                ),
            },
        )

    else:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=encoded)

    try:
        with urllib.request.urlopen(
            req,
            timeout=timeout,
        ) as response:
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
            result.get(
                "description",
                "Telegram API request failed",
            )
        )

    return result.get("result")


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False,
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
            reply_markup,
            ensure_ascii=False,
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
        log_error(
            "answerCallbackQuery failed: " + repr(exc)
        )


def send_video(chat_id, path, caption):
    log_error(
        "Sending video | chat_id=%s | path=%s | size=%s"
        % (
            chat_id,
            path,
            os.path.getsize(path)
            if os.path.isfile(path)
            else "missing",
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


# =========================
# KEYBOARDS
# =========================

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

        for surah in chunk[i:i + 2]:
            row.append({
                "text": "%s. %s" % (
                    surah["number"],
                    surah["name"],
                ),
                "callback_data": "surah:%d" % surah["number"],
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


def keyboard_designs():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "1️⃣ سەوزی زمردی و زێڕ",
                    "callback_data": "design:1",
                },
                {
                    "text": "2️⃣ ڕەش و زێڕ",
                    "callback_data": "design:2",
                },
            ],
            [
                {
                    "text": "3️⃣ شینی ئاسمانی",
                    "callback_data": "design:3",
                },
                {
                    "text": "4️⃣ کرێمی کلاسیک",
                    "callback_data": "design:4",
                },
            ],
            [
                {
                    "text": "5️⃣ مۆر و مۆدێرن",
                    "callback_data": "design:5",
                },
            ],
        ]
    }



# =========================
# DOWNLOAD HELPERS
# =========================

def download_file(url, output_path, retries=3):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()

            if not data:
                raise RuntimeError("فایلەکە بەتاڵە.")

            with open(output_path, "wb") as file:
                file.write(data)

            return output_path

        except Exception as error:
            log.warning(
                "Download attempt %s/%s failed: %s",
                attempt + 1,
                retries,
                error
            )
            time.sleep(1)

    raise RuntimeError("نەتوانرا فایلەکە دابەزێندرێت.")


def run_ffmpeg(command, timeout=900):
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            log.error("FFmpeg error: %s", result.stderr[-3000:])
            raise RuntimeError("FFmpeg نەتوانی ڤیدیۆکە دروست بکات.")

        return result

    except subprocess.TimeoutExpired:
        raise RuntimeError("دروستکردنی ڤیدیۆ زۆر درێژ بوو و وەستا.")


def get_audio_duration(audio_path):
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        raise RuntimeError("نەتوانرا ماوەی دەنگەکە بزانرێت.")

    return max(0.0, float(result.stdout.strip()))


# =========================
# AUDIO DOWNLOAD
# =========================

def download_surah_audio(reciter_id, surah_id):
    os.makedirs(AUDIO_DIR, exist_ok=True)

    filename = f"{int(surah_id):03d}.mp3"
    audio_path = os.path.join(
        AUDIO_DIR,
        str(reciter_id),
        filename
    )

    if os.path.isfile(audio_path) and os.path.getsize(audio_path) > 1000:
        return audio_path

    reciter = next(
        (
            item for item in RECITERS
            if str(item.get("id")) == str(reciter_id)
        ),
        None
    )

    if not reciter:
        raise RuntimeError("خوێنەرەوەکە نەدۆزرایەوە.")

    base_url = (
        reciter.get("server")
        or reciter.get("url")
        or reciter.get("audio_url")
    )

    if not base_url:
        raise RuntimeError("بەستەری دەنگی ئەم خوێنەرەوەیە نییە.")

    base_url = str(base_url).rstrip("/")

    if "{surah}" in base_url:
        audio_url = base_url.format(surah=f"{int(surah_id):03d}")
    elif "{id}" in base_url:
        audio_url = base_url.format(id=f"{int(surah_id):03d}")
    else:
        audio_url = f"{base_url}/{filename}"

    download_file(audio_url, audio_path)

    if os.path.getsize(audio_path) < 1000:
        try:
            os.remove(audio_path)
        except OSError:
            pass
        raise RuntimeError("فایلی دەنگەکە دروست نییە.")

    return audio_path


# =========================
# TIMINGS DATABASE
# =========================

def valid_timings_database(db_path):
    if not os.path.isfile(db_path):
        return False

    try:
        connection = sqlite3.connect(db_path)
        cursor = connection.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {
            row[0].lower()
            for row in cursor.fetchall()
        }

        connection.close()

        return bool(tables)

    except Exception:
        return False


def download_timings_database():
    os.makedirs(DATA_DIR, exist_ok=True)

    db_path = os.path.join(DATA_DIR, "timings.db")

    if valid_timings_database(db_path):
        return db_path

    if not TIMINGS_DB_URL:
        raise RuntimeError(
            "TIMINGS_DB_URL دانەنراوە و داتابەیسی کاتەکانی ئایەت بەردەست نییە."
        )

    temporary_path = db_path + ".download"

    try:
        download_file(TIMINGS_DB_URL, temporary_path)

        if not valid_timings_database(temporary_path):
            raise RuntimeError("داتابەیسی کاتەکان دروست نییە.")

        os.replace(temporary_path, db_path)
        return db_path

    finally:
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass


def get_ayah_timings(surah_id, audio_duration, ayah_count):
    """
    هەوڵ دەدات کاتەکانی ئایەتەکان لە داتابەیسی timings بخوێنێتەوە.
    ئەگەر خشتە یان ناوی ستوونەکان نەگونجان، بەشی یەکسان بەکاردێت.
    """

    if ayah_count <= 0:
        return []

    timings = []
    db_path = os.path.join(DATA_DIR, "timings.db")

    try:
        if not valid_timings_database(db_path):
            download_timings_database()

        connection = sqlite3.connect(db_path)
        cursor = connection.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            cursor.execute(f'PRAGMA table_info("{table}")')
            columns = [
                row[1].lower()
                for row in cursor.fetchall()
            ]

            surah_column = next(
                (
                    name for name in columns
                    if name in (
                        "surah", "surah_id", "sura", "suraid",
                        "chapter", "chapter_id"
                    )
                ),
                None
            )

            ayah_column = next(
                (
                    name for name in columns
                    if name in (
                        "ayah", "ayah_id", "ayah_number",
                        "verse", "verse_number", "aya"
                    )
                ),
                None
            )

            start_column = next(
                (
                    name for name in columns
                    if name in (
                        "start", "start_time", "start_ms",
                        "timestamp_from", "from_time", "begin"
                    )
                ),
                None
            )

            end_column = next(
                (
                    name for name in columns
                    if name in (
                        "end", "end_time", "end_ms",
                        "timestamp_to", "to_time", "finish"
                    )
                ),
                None
            )

            if not all([
                surah_column,
                ayah_column,
                start_column,
                end_column
            ]):
                continue

            query = (
                f'SELECT "{ayah_column}", "{start_column}", "{end_column}" '
                f'FROM "{table}" '
                f'WHERE "{surah_column}" = ? '
                f'ORDER BY CAST("{ayah_column}" AS INTEGER)'
            )

            cursor.execute(query, (int(surah_id),))
            rows = cursor.fetchall()

            if rows:
                for row in rows:
                    try:
                        ayah_number = int(row[0])
                        start_time = float(row[1])
                        end_time = float(row[2])

                        if start_time > 100000:
                            start_time /= 1000.0

                        if end_time > 100000:
                            end_time /= 1000.0

                        timings.append(
                            (ayah_number, start_time, end_time)
                        )
                    except (TypeError, ValueError):
                        continue

                if timings:
                    break

        connection.close()

    except Exception as error:
        log.warning("Could not read timings database: %s", error)

    if timings:
        timings.sort(key=lambda item: item[0])

        selected = timings[:ayah_count]
        cleaned = []

        for index, item in enumerate(selected):
            _, start_time, end_time = item

            start_time = max(0.0, min(start_time, audio_duration))
            end_time = max(start_time, min(end_time, audio_duration))

            if end_time <= start_time:
                continue

            cleaned.append((start_time, end_time))

        if len(cleaned) == ayah_count:
            return cleaned

    # Fallback: دابەشکردنی ماوەی دەنگ بەسەر ئایەتەکاندا
    interval = audio_duration / ayah_count

    return [
        (
            index * interval,
            min((index + 1) * interval, audio_duration)
        )
        for index in range(ayah_count)
    ]


# =========================
# QURAN DATABASE
# =========================

def find_surah_rows(surah_id, start_ayah, end_ayah):
    db_path = os.path.join(BASE_DIR, "quran.db")

    if not os.path.isfile(db_path):
        db_path = os.path.join(DATA_DIR, "quran.db")

    if not os.path.isfile(db_path):
        raise RuntimeError(
            "فایلی quran.db نەدۆزرایەوە. دڵنیابە لە بوونی لە فۆڵدەری پرۆژە."
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.cursor()

        cursor.execute("PRAGMA table_info(ayah_text)")
        columns = {
            row[1]
            for row in cursor.fetchall()
        }

        required = {
            "suraId",
            "sura_name_ar",
            "text",
            "asan",
            "ayah"
        }

        if not required.issubset(columns):
            raise RuntimeError(
                "ستوونەکانی خشتەی ayah_text لەگەڵ پێکهاتەی چاوەڕوانکراو ناگونجێن."
            )

        cursor.execute(
            """
            SELECT suraId, sura_name_ar, text, asan, ayah
            FROM ayah_text
            WHERE CAST(suraId AS INTEGER) = ?
              AND CAST(ayah AS INTEGER) BETWEEN ? AND ?
            ORDER BY CAST(ayah AS INTEGER)
            """,
            (
                int(surah_id),
                int(start_ayah),
                int(end_ayah)
            )
        )

        rows = [
            dict(row)
            for row in cursor.fetchall()
        ]

        if not rows:
            raise RuntimeError(
                "هیچ ئایەتێک لەم ژمارە و سورەتەدا نەدۆزرایەوە."
            )

        return rows

    finally:
        connection.close()


# =========================
# VIDEO BUILDING
# =========================

def build_video(chat_id, ayahs, reciter_id, design_id=1):
    if not ayahs:
        raise RuntimeError("هیچ ئایەتێک بۆ دروستکردنی ڤیدیۆ نییە.")

    if len(ayahs) > MAX_AYAH_COUNT:
        raise RuntimeError(
            f"زۆرترین {MAX_AYAH_COUNT} ئایەت بۆ هەر ڤیدیۆیەک ڕێگەپێدراوە."
        )

    design_id = int(design_id)

    if design_id not in (1, 2, 3, 4, 5):
        design_id = 1

    job_id = f"{chat_id}_{int(time.time())}"
    work_dir = os.path.join(WORK_DIR, job_id)

    os.makedirs(work_dir, exist_ok=True)

    audio_path = download_surah_audio(
        reciter_id,
        ayahs[0]["suraId"]
    )

    audio_duration = get_audio_duration(audio_path)

    all_surah_rows = find_surah_rows(
        ayahs[0]["suraId"],
        1,
        1000
    )

    timings = get_ayah_timings(
        ayahs[0]["suraId"],
        audio_duration,
        len(all_surah_rows)
    )

    timing_by_ayah = {}

    for index, row in enumerate(all_surah_rows):
        if index < len(timings):
            timing_by_ayah[int(row["ayah"])] = timings[index]

    clip_paths = []

    for index, ayah in enumerate(ayahs):
        ayah_number = int(ayah["ayah"])

        image_path = os.path.join(
            work_dir,
            f"ayah_{index:03d}.png"
        )

        clip_path = os.path.join(
            work_dir,
            f"clip_{index:03d}.mp4"
        )

        # design_id بۆ quran_video.py دەنێردرێت
        qv.create_ayah_image(
            ayah,
            index,
            image_path,
            design_id=design_id
        )

        if ayah_number in timing_by_ayah:
            start_time, end_time = timing_by_ayah[ayah_number]
        else:
            start_time = 0.0
            end_time = audio_duration

        if end_time <= start_time:
            raise RuntimeError(
                f"کاتی ئایەتی {ayah_number} دروست نییە."
            )

        duration = end_time - start_time

        command = [
            "ffmpeg",
            "-y",
            "-loop", "1",
            "-framerate", "25",
            "-i", image_path,
            "-i", audio_path,
            "-ss", str(start_time),
            "-t", str(duration),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                   "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            clip_path
        ]

        run_ffmpeg(command)

        if not os.path.isfile(clip_path):
            raise RuntimeError("کلیپی ئایەتەکە دروست نەبوو.")

        clip_paths.append(clip_path)

    concat_file = os.path.join(work_dir, "concat.txt")

    with open(concat_file, "w", encoding="utf-8") as file:
        for clip_path in clip_paths:
            escaped_path = os.path.abspath(clip_path).replace(
                "'", "'\\''"
            )
            file.write(f"file '{escaped_path}'\n")

    output_path = os.path.join(
        work_dir,
        "quran_video.mp4"
    )

    concat_command = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]

    run_ffmpeg(concat_command)

    if not os.path.isfile(output_path):
        raise RuntimeError("ڤیدیۆی کۆتایی دروست نەبوو.")

    if os.path.getsize(output_path) < 1000:
        raise RuntimeError("ڤیدیۆی کۆتایی بەتاڵە یان تەواو نییە.")

    return output_path



# =========================
# START COMMAND
# =========================

def start(chat_id):
    SESSIONS[chat_id] = {
        "state": "reciter",
        "page": 0
    }

    telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": (
                "🕌 بەخێربێیت بۆ بۆتی قورئانی پیرۆز\n\n"
                "تکایە یەکێک لە خوێنەرەوەکان هەڵبژێرە:"
            ),
            "reply_markup": keyboard_reciters(0)
        }
    )


# =========================
# MESSAGE HANDLER
# =========================

def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if not chat_id:
        return

    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        start(chat_id)
        return

    session = SESSIONS.get(chat_id)

    if not session:
        start(chat_id)
        return

    state = session.get("state")

    if state == "start_ayah":
        try:
            start_ayah = int(text)
        except ValueError:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ تکایە ژمارەی سەرەتای ئایەت بە ژمارە بنووسە."
                }
            )
            return

        surah_id = int(session["surah_id"])

        if start_ayah < 1:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ ژمارەی ئایەت نابێت لە ١ کەمتر بێت."
                }
            )
            return

        try:
            rows = find_surah_rows(surah_id, start_ayah, start_ayah)
            if not rows:
                raise RuntimeError("ئەم ئایەتە نەدۆزرایەوە.")
        except Exception as error:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": f"❌ {error}"
                }
            )
            return

        session["start_ayah"] = start_ayah
        session["state"] = "end_ayah"

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    f"✅ ئایەتی دەستپێک: {start_ayah}\n\n"
                    "تکایە ژمارەی کۆتایی ئایەت بنووسە:"
                )
            }
        )
        return

    if state == "end_ayah":
        try:
            end_ayah = int(text)
        except ValueError:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ تکایە ژمارەی کۆتایی ئایەت بە ژمارە بنووسە."
                }
            )
            return

        start_ayah = int(session["start_ayah"])
        surah_id = int(session["surah_id"])

        if end_ayah < start_ayah:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ ئایەتی کۆتایی نابێت لە ئایەتی دەستپێک بچووکتر بێت."
                }
            )
            return

        if end_ayah - start_ayah + 1 > MAX_AYAH_COUNT:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        f"❌ زۆرترین {MAX_AYAH_COUNT} ئایەت "
                        "بۆ هەر ڤیدیۆیەک ڕێگەپێدراوە."
                    )
                }
            )
            return

        try:
            ayahs = find_surah_rows(
                surah_id,
                start_ayah,
                end_ayah
            )

            if len(ayahs) != end_ayah - start_ayah + 1:
                raise RuntimeError(
                    "هەندێک ئایەت لە داتابەیسدا نەدۆزرایەوە."
                )

        except Exception as error:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": f"❌ {error}"
                }
            )
            return

        session["ayahs"] = ayahs
        session["end_ayah"] = end_ayah
        session["state"] = "design"

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "🎨 دیزاینی ڤیدیۆکە هەڵبژێرە:\n\n"
                    "١. سەوز و زێڕین\n"
                    "٢. ڕەش و زێڕین\n"
                    "٣. شینی ئاسمانی\n"
                    "٤. کرێمی و کلاسیک\n"
                    "٥. مۆر و لاڤەندەر"
                ),
                "reply_markup": keyboard_designs()
            }
        )
        return

    telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "بۆ دەستپێکردن /start بنێرە."
        }
    )


# =========================
# CALLBACK HANDLER
# =========================

def handle_callback(callback):
    callback_id = callback.get("id")
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if callback_id:
        telegram(
            "answerCallbackQuery",
            {"callback_query_id": callback_id}
        )

    if not chat_id:
        return

    session = SESSIONS.setdefault(chat_id, {})

    if data == "change_reciter":
        session.clear()
        session.update({"state": "reciter", "page": 0})

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "🎙️ خوێنەرەوەیەک هەڵبژێرە:",
                "reply_markup": keyboard_reciters(0)
            }
        )
        return

    if data.startswith("reciter:"):
        reciter_id = data.split(":", 1)[1]

        reciter = next(
            (
                item for item in RECITERS
                if str(item.get("id")) == reciter_id
            ),
            None
        )

        if not reciter:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ خوێنەرەوەکە نەدۆزرایەوە."
                }
            )
            return

        session.clear()
        session.update(
            {
                "state": "surah",
                "reciter_id": reciter_id,
                "page": 0
            }
        )

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "✅ خوێنەرەوە هەڵبژێردرا:\n"
                    f"{reciter.get('name', 'خوێنەرەوە')}\n\n"
                    "📖 سورەتێک هەڵبژێرە:"
                ),
                "reply_markup": keyboard_surahs(0)
            }
        )
        return

    if data.startswith("page:"):
        try:
            page = max(0, int(data.split(":", 1)[1]))
        except ValueError:
            return

        state = session.get("state")

        if state == "reciter":
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "🎙️ خوێنەرەوە هەڵبژێرە:",
                    "reply_markup": keyboard_reciters(page)
                }
            )
        elif state == "surah":
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "📖 سورەتێک هەڵبژێرە:",
                    "reply_markup": keyboard_surahs(page)
                }
            )
        return

    if data.startswith("surah:"):
        try:
            surah_id = int(data.split(":", 1)[1])
        except ValueError:
            return

        if not session.get("reciter_id"):
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "تکایە سەرەتا خوێنەرەوە هەڵبژێرە. /start"
                }
            )
            return

        session["surah_id"] = surah_id
        session["state"] = "start_ayah"

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    f"📖 سورەت: {surah_id}\n\n"
                    "تکایە ژمارەی ئایەتی دەستپێک بنووسە:"
                )
            }
        )
        return

    # =========================
    # DESIGN SELECTION
    # =========================

    if data.startswith("design:"):
        try:
            design_id = int(data.split(":", 1)[1])
        except ValueError:
            return

        if design_id not in (1, 2, 3, 4, 5):
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "❌ ئەم دیزاینە بەردەست نییە."
                }
            )
            return

        ayahs = session.get("ayahs")
        reciter_id = session.get("reciter_id")

        if not ayahs or not reciter_id:
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "❌ زانیارییەکان تەواو نین. "
                        "تکایە دووبارە /start بکە."
                    )
                }
            )
            return

        session["state"] = "building"

        telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    f"⏳ ڤیدیۆکە بە دیزاینی {design_id} "
                    "دروست دەکرێت...\n"
                    "تکایە چاوەڕێ بکە."
                )
            }
        )

        output_path = None

        try:
            output_path = build_video(
                chat_id,
                ayahs,
                reciter_id,
                design_id=design_id
            )

            with open(output_path, "rb") as video_file:
                telegram(
                    "sendVideo",
                    {
                        "chat_id": chat_id,
                        "caption": (
                            "قورئانی پیرۆز - ڕێگای ئیسلام\n"
                            f"🎨 دیزاین: {design_id}"
                        ),
                        "supports_streaming": True
                    },
                    files={"video": video_file}
                )

            session["state"] = "done"

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "✅ ڤیدیۆکە نێردرا.\n\n"
                        "بۆ دروستکردنی ڤیدیۆیەکی نوێ /start بنێرە."
                    )
                }
            )

        except Exception as error:
            log.exception("Video generation failed")

            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": (
                        "❌ نەتوانرا ڤیدیۆکە دروست بکرێت.\n"
                        f"هۆکار: {str(error)[:700]}"
                    )
                }
            )

        finally:
            if output_path:
                try:
                    output_dir = os.path.dirname(output_path)

                    if os.path.isdir(output_dir):
                        shutil.rmtree(output_dir, ignore_errors=True)

                except Exception:
                    log.exception("Could not clean video files")

        return


# =========================
# MAIN TELEGRAM LOOP
# =========================

def main():
    log.info("Quran video bot started")

    offset = None

    while True:
        try:
            params = {"timeout": 30}

            if offset is not None:
                params["offset"] = offset

            result = telegram("getUpdates", params)

            if not result.get("ok"):
                log.error("getUpdates returned an error: %s", result)
                time.sleep(3)
                continue

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                try:
                    if "message" in update:
                        handle_message(update["message"])

                    elif "callback_query" in update:
                        handle_callback(update["callback_query"])

                except Exception:
                    log.exception("Error processing Telegram update")

        except KeyboardInterrupt:
            log.info("Bot stopped by user")
            break

        except Exception:
            log.exception("Telegram polling failed")
            time.sleep(5)


if __name__ == "__main__":
    main()
