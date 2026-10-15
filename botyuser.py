
# -*- coding: utf-8 -*-
"""Telegram Quran video bot with reciter selection and timing databases."""

import os
import sys
import json
import time
import shutil
import sqlite3
import subprocess
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

MAX_AYAHS = max(1, int(os.environ.get("MAX_AYAHS_PER_VIDEO", "20")))
POLL_TIMEOUT = 30

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "quran_user_jobs")
TIMINGS_DIR = os.path.join(BASE_DIR, "reciter_timings")
RECITERS_JSON_PATH = os.path.join(BASE_DIR, "reciters.json")

# Use GitHub's direct raw file host.
TIMINGS_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "quranofficial/daily-news-bot/main/"
)

SESSIONS = {}
_SURAH_CACHE = None


# =========================
# URL HELPERS
# =========================

def normalize_github_url(url):
    """
    Convert GitHub file URLs into direct raw URLs where possible.
    Other URLs are returned unchanged.
    """
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

    except Exception:
        return url


# =========================
# LOAD RECITERS FROM JSON
# =========================

def load_reciters():
    """Load enabled reciters from reciters.json."""

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
            print(
                "Skipping incomplete reciter entry:",
                item,
                file=sys.stderr,
            )
            continue

        if not url.startswith(("http://", "https://")):
            print(
                "Skipping reciter with invalid URL:",
                reciter_id,
                file=sys.stderr,
            )
            continue

        url = normalize_github_url(url)

        # Expected layout:
        # URL + 001.mp3, URL + 002.mp3, etc.
        reciters[reciter_id] = {
            "name": name,
            "base": url.rstrip("/") + "/",
        }

    if not reciters:
        raise ValueError(
            "هیچ قارییەکی دروست بە doxx=true "
            "لە reciters.json نەدۆزرایەوە."
        )

    print(
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
    except Exception:
        pass


def send_video(chat_id, path, caption):
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
        data = qv.get_json(QURAN_API + "/surah")

        if not isinstance(data, dict) or data.get("code") != 200:
            raise RuntimeError(
                "نەتوانرا لیستی سوورەتەکان وەربگیرێت."
            )

        _SURAH_CACHE = data["data"]

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
# DOWNLOAD FILES
# =========================

def download_file(url, output_path, min_size=100):
    """
    Download a file with retries and GitHub raw URL fallback.
    Partial downloads are never kept as valid output files.
    """

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
        for attempt in range(1, 5):
            temp_path = output_path + ".part"

            try:
                request = urllib.request.Request(
                    current_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 QuranVideoBot/1.0",
                        "Accept": "*/*",
                        "Connection": "close",
                    },
                )

                with urllib.request.urlopen(
                    request, timeout=120
                ) as response:
                    content = response.read()

                if len(content) < min_size:
                    raise RuntimeError(
                        "فایلە وەرگیراوەکە زۆر بچووکە: "
                        + str(len(content)) + " bytes"
                    )

                with open(temp_path, "wb") as f:
                    f.write(content)

                os.replace(temp_path, output_path)

                print(
                    "Download successful:",
                    current_url,
                    len(content),
                    "bytes",
                )

                return output_path

            except Exception as exc:
                last_error = exc

                print(
                    "Download failed:",
                    "attempt", attempt,
                    "URL:", current_url,
                    "error:", repr(exc),
                    file=sys.stderr,
                )

                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass

                # Retry temporary server/network errors.
                if attempt < 4:
                    time.sleep(attempt * 3)

    raise RuntimeError(
        "دابەزاندنی فایل سەرکەوتوو نەبوو.\n"
        "URL: " + str(url) + "\n"
        "هەڵەی کۆتایی: " + str(last_error)
    )


def download_surah_audio(surah_number, reciter_id, output_path):
    reciter = RECITERS.get(str(reciter_id))

    if not reciter:
        raise ValueError("قاریی هەڵبژێردراو دروست نییە.")

    filename = "%03d.mp3" % int(surah_number)
    url = reciter["base"] + filename

    try:
        download_file(url, output_path, min_size=1000)

        # Verify that the downloaded file is readable audio.
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
            raise RuntimeError(
                "فایلی دابەزێنراو MP3 ـێکی دروست نییە."
            )

        duration = float(proc.stdout.strip())

        if duration <= 0:
            raise RuntimeError("ماوەی دەنگەکە دروست نییە.")

        print(
            "Audio verified:",
            reciter["name"],
            "surah", surah_number,
            "duration", duration,
        )

    except Exception as exc:
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

def download_timings_database(reciter_id):
    os.makedirs(TIMINGS_DIR, exist_ok=True)

    db_path = os.path.join(
        TIMINGS_DIR,
        str(reciter_id) + ".db",
    )

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "SELECT sura, ayah, time FROM timings LIMIT 1"
            )
            conn.close()
            return db_path

        except Exception:
            try:
                os.remove(db_path)
            except OSError:
                pass

    url = TIMINGS_BASE_URL + str(reciter_id) + ".db"

    try:
        download_file(url, db_path, min_size=100)

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "SELECT sura, ayah, time FROM timings LIMIT 1"
            )
        finally:
            conn.close()

        return db_path

    except Exception as exc:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass

        raise RuntimeError(
            "نەتوانرا داتابەیسی کاتی قاری وەربگیرێت.\n"
            "ID: %s\nURL: %s\nهۆکار: %s"
            % (reciter_id, url, exc)
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

    # Some databases store timestamps in milliseconds.
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

    if count > MAX_AYAHS:
        raise ValueError(
            "زۆرترین %d ئایەت بۆ هەر ڤیدیۆیەک ڕێگەپێدراوە."
            % MAX_AYAHS
        )

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
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg: "
            + (proc.stderr[-1500:] or "هەڵەی نەناسراو")
        )


def build_video(chat_id, ayahs, reciter_id):
    stamp = str(int(time.time()))

    job_dir = os.path.join(
        JOBS_DIR,
        str(chat_id),
        stamp,
    )

    os.makedirs(job_dir, exist_ok=True)

    try:
        surah_number = int(ayahs[0]["surah_number"])

        full_audio_path = os.path.join(
            job_dir,
            "%03d.mp3" % surah_number,
        )

        # Download and verify the full surah audio.
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
            )

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

        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise RuntimeError(
                "فایلی ڤیدیۆی کۆتایی دروست نەکرا."
            )

        return output, job_dir

    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


# =========================
# BOT COMMANDS
# =========================

def start(chat_id):
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
            "زۆرترین ئایەت بۆ هەر ڤیدیۆیەک: %d"
            % MAX_AYAHS,
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
            send_message(
                chat_id,
                "ژمارەیەکی دروست بنووسە. " + str(exc),
            )

        return

    if session.get("state") == "end_ayah":
        job_dir = None

        try:
            end_ayah = int(text)
            surah_number = session["surah_number"]
            start_ayah = session["start_ayah"]
            reciter_id = session["reciter_id"]

            ayahs = find_surah_rows(
                surah_number,
                start_ayah,
                end_ayah,
            )

            surah = next(
                s for s in get_surahs()
                if int(s["number"]) == surah_number
            )

            reciter_name = RECITERS[str(reciter_id)]["name"]

            send_message(
                chat_id,
                "⏳ ڤیدیۆکەت دروست دەکەم...\n"
                "🎙️ قاری: %s\n"
                "سوورەت: %s\n"
                "ئایەتەکان: %d–%d\n"
                "تکایە چاوەڕێ بکە."
                % (
                    reciter_name,
                    surah["name"],
                    start_ayah,
                    end_ayah,
                ),
            )

            video_path, job_dir = build_video(
                chat_id,
                ayahs,
                reciter_id,
            )

            send_video(
                chat_id,
                video_path,
                "قورئانی پیرۆز — %s\n"
                "قاری: %s\n"
                "ئایەت %d–%d"
                % (
                    surah["name"],
                    reciter_name,
                    start_ayah,
                    end_ayah,
                ),
            )

            send_message(
                chat_id,
                "✅ ڤیدیۆکە بە سەرکەوتوویی بۆ تۆ نێردرا.",
            )

        except Exception as exc:
            print(
                "Video creation error:",
                repr(exc),
                file=sys.stderr,
            )

            try:
                send_message(
                    chat_id,
                    "❌ نەمتوانی ڤیدیۆکە دروست بکەم.\n"
                    + str(exc),
                )
            except Exception as send_exc:
                print(
                    "Could not send error to Telegram:",
                    repr(send_exc),
                    file=sys.stderr,
                )

        finally:
            SESSIONS.pop(chat_id, None)

            if job_dir:
                shutil.rmtree(job_dir, ignore_errors=True)

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

    if chat_id is None:
        answer_callback(callback_id)
        return

    try:
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
        print(
            "Callback error:",
            repr(exc),
            file=sys.stderr,
        )
        answer_callback(callback_id, str(exc)[:180])


# =========================
# MAIN LOOP
# =========================

def main():
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
        print(
            "ئاگاداری: quran.ttf لە هەمان فۆڵدەری کۆدەکە دابنێ."
        )

    print(
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
            break

        except Exception as exc:
            print(
                "Polling error:",
                repr(exc),
                file=sys.stderr,
            )
            time.sleep(3)


if __name__ == "__main__":
    main()
