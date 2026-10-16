
# -*- coding: utf-8 -*-
"""Telegram Quran video bot with per-user reciter selection."""

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

os.environ.setdefault("CHANNEL_ID", "")
import quran_video as qv

BOT_TOKEN = os.environ["BOT_TOKEN"]
TG = "https://api.telegram.org/bot" + BOT_TOKEN + "/"
QURAN_API = "https://api.alquran.cloud/v1"

MAX_AYAHS = max(1, int(os.environ.get("MAX_AYAHS_PER_VIDEO", "20")))
POLL_TIMEOUT = 30
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "quran_user_jobs")

SESSIONS = {}
_SURAH_CACHE = None

# Reciter choices. Audio filenames use the EveryAyah format:
# SSSAAA.mp3, for example 001001.mp3.
RECITERS = {
    "1": {
        "name": "هەندرێن عبدالرحمان کوردی",
        "base": "https://github.com/H4zh4n/TafseeriNoor/raw/files/mp3/Handren_Abdulrahman/",
    },
    "2": {
        "name": "سعد الغامدي",
        "base": "https://mirrors.quranicaudio.com/everyayah/Ghamadi_40kbps/",
    },
    "3": {
        "name": "عبد الباسط عبد الصمد",
        "base": "https://mirrors.quranicaudio.com/everyayah/Abdul_Basit_Murattal_192kbps/",
    },
    "4": {
        "name": "أبو بكر الشاطري",
        "base": "https://mirrors.quranicaudio.com/everyayah/Abu_Bakr_Ash-Shaatree_128kbps/",
    },
    "5": {
        "name": "ماهر المعيقلي",
        "base": "https://mirrors.quranicaudio.com/everyayah/Maher_AlMuaiqly_64kbps/",
    },
    "6": {
        "name": "ياسر الدوسري",
        "base": "https://mirrors.quranicaudio.com/everyayah/Yasser_Ad-Dussary_128kbps/",
    },
    "7": {
        "name": "هاني الرفاعي",
        "base": "https://mirrors.quranicaudio.com/everyayah/Hani_Rifai_192kbps/",
    },
}


def telegram(method, data=None, files=None, timeout=120):
    data = data or {}
    url = TG + method

    if files:
        boundary = "----QuranBotBoundary7MA4YWxkTrZu0gW"
        body = bytearray()

        for key, value in data.items():
            body.extend(("--" + boundary + "\r\n").encode())
            body.extend(
                ('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode()
            )
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        for key, (filename, content, mime) in files.items():
            body.extend(("--" + boundary + "\r\n").encode())
            body.extend(
                ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                 % (key, filename)).encode()
            )
            body.extend(("Content-Type: %s\r\n\r\n" % mime).encode())
            body.extend(content)
            body.extend(b"\r\n")

        body.extend(("--" + boundary + "--\r\n").encode())

        req = urllib.request.Request(
            url,
            data=bytes(body),
            headers={
                "Content-Type": "multipart/form-data; boundary=" + boundary
            },
        )
    else:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=encoded)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Telegram API error: " + str(exc))

    if not result.get("ok"):
        raise RuntimeError(
            result.get("description", "Telegram API request failed")
        )

    return result.get("result")


def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}

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

    for reciter_id, reciter in RECITERS.items():
        rows.append([
            {
                "text": reciter["name"],
                "callback_data": "reciter:" + reciter_id,
            }
        ])

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

    # Let the user return to the reciter selection.
    rows.append([
        {
            "text": "🎙️ گۆڕینی قاری",
            "callback_data": "change_reciter",
        }
    ])

    return {"inline_keyboard": rows}


def download_selected_reciter_audio(ayah, output_path, reciter_id):
    """Download the selected reciter's audio for one ayah."""

    reciter = RECITERS.get(str(reciter_id))

    if not reciter:
        raise ValueError("قاریی هەڵبژێردراو دروست نییە.")

    surah_number = int(ayah["surah_number"])
    ayah_number = int(ayah["ayah_number"])

    filename = "%03d%03d.mp3" % (
        surah_number,
        ayah_number,
    )

    url = reciter["base"] + filename

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QuranVideoBot/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            audio_data = response.read()

        if not audio_data or len(audio_data) < 100:
            raise RuntimeError("فایلی دەنگەکە بەتاڵە.")

        with open(output_path, "wb") as f:
            f.write(audio_data)

    except Exception as exc:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass

        raise RuntimeError(
            "نەتوانرا دەنگی قاری (%s) بۆ سوورەت %d، ئایەت %d "
            "وەربگیرێت. لینکی دەنگ یان ناوی فایلەکە ڕەنگە جیاواز بێت. "
            "هۆکار: %s"
            % (
                reciter["name"],
                surah_number,
                ayah_number,
                str(exc),
            )
        )


def find_surah_rows(surah_number, start_ayah, end_ayah):
    if end_ayah < start_ayah:
        raise ValueError(
            "ئایەتی کۆتایی دەبێت گەورەتر یان یەکسان بێت بە ئایەتی دەستپێک."
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

    if start_ayah < 1 or end_ayah > int(surah["numberOfAyahs"]):
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

        actual_columns = {
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
            name for name in required
            if name not in actual_columns
        ]

        if missing:
            raise RuntimeError(
                "ستوونی پێویست نەدۆزرایەوە: "
                + ", ".join(missing)
            )

        sura_id_col = actual_columns["suraid"]
        ayah_col = actual_columns["ayah"]
        text_col = actual_columns["text"]
        tafsir_col = actual_columns["asan"]
        name_col = actual_columns["sura_name_ar"]

        sql = (
            'SELECT "{name}" AS sura_name, '
            '"{ayah}" AS ayah_number, '
            '"{text}" AS arabic_text, '
            '"{tafsir}" AS tafsir_text '
            'FROM "{table}" '
            'WHERE CAST("{sura_id}" AS INTEGER)=? '
            'AND CAST("{ayah}" AS INTEGER) BETWEEN ? AND ? '
            'ORDER BY CAST("{ayah}" AS INTEGER)'
        ).format(
            name=name_col.replace('"', '""'),
            ayah=ayah_col.replace('"', '""'),
            text=text_col.replace('"', '""'),
            tafsir=tafsir_col.replace('"', '""'),
            table=table_name.replace('"', '""'),
            sura_id=sura_id_col.replace('"', '""'),
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
                "ئایەتە داواکراوەکان لە داتابەیسدا بە تەواوی نەدۆزرایەوە."
            )

        global_start = sum(
            int(s["numberOfAyahs"])
            for s in surahs
            if int(s["number"]) < int(surah_number)
        ) + int(start_ayah)

        result = []

        for index, row in enumerate(rows):
            arabic = str(row["arabic_text"] or "").strip()
            tafsir = str(row["tafsir_text"] or "").strip()

            if not arabic:
                raise RuntimeError(
                    "دەقی عەرەبی ئایەت بەتاڵە."
                )

            db_surah_name = str(
                row["sura_name"] or ""
            ).strip()

            result.append({
                "surah_number": int(surah_number),
                "surah_name": db_surah_name or surah["name"],
                "ayah_number": int(row["ayah_number"]),
                "global_number": global_start + index,
                "arabic": arabic,
                "tafsir": tafsir or
                    "تەفسیری ئەم ئایەتە لە داتابەیسدا بەردەست نییە.",
                "translation": tafsir or
                    "تەفسیری ئەم ئایەتە لە داتابەیسدا بەردەست نییە.",
            })

        return result

    finally:
        conn.close()


def run_ffmpeg(args):
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg: " + (proc.stderr[-1200:] or "هەڵەی نەناسراو")
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
        clips = []

        for i, ayah in enumerate(ayahs, 1):
            image_path = os.path.join(
                job_dir, "image_%03d.png" % i
            )
            audio_path = os.path.join(
                job_dir, "audio_%03d.mp3" % i
            )
            clip_path = os.path.join(
                job_dir, "clip_%03d.mp4" % i
            )

            qv.create_ayah_image(
                ayah,
                i,
                image_path,
            )

            # Download audio from the selected reciter.
            download_selected_reciter_audio(
                ayah,
                audio_path,
                reciter_id,
            )

            run_ffmpeg([
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", image_path,
                "-i", audio_path,
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

        list_path = os.path.join(job_dir, "clips.txt")

        with open(list_path, "w", encoding="utf-8") as f:
            for clip in clips:
                f.write(
                    "file '%s'\n"
                    % clip.replace("'", "'\\''")
                )

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

        return output, job_dir

    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def start(chat_id):
    SESSIONS[chat_id] = {
        "state": "reciter",
    }

    send_message(
        chat_id,
        "بەخێربێیت 🌙\n\n"
        "🎙️ سەرەتا قاریی دڵخوازی خۆت هەڵبژێرە:",
        keyboard_reciters(),
    )


def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")

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
            "داواکارییەکەت هەڵوەشایەوە. بۆ دەستپێکردن /start بنووسە.",
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

            if number < 1 or number > int(surah["numberOfAyahs"]):
                raise ValueError(
                    "ژمارەی ئایەت لە سنوورەکەدا نییە."
                )

            session["start_ayah"] = number
            session["state"] = "end_ayah"

            send_message(
                chat_id,
                "ئایەتی دەستپێک: %d\n"
                "ئێستا ژمارەی ئایەتی کۆتایی بنووسە (1–%d)."
                % (number, int(surah["numberOfAyahs"])),
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

            reciter_name = RECITERS[reciter_id]["name"]

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
            send_message(
                chat_id,
                "❌ نەمتوانی ڤیدیۆکە دروست بکەم.\n" + str(exc),
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
            SESSIONS[chat_id] = {
                "state": "reciter",
            }

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

            SESSIONS[chat_id] = {
                "state": "start_ayah",
                "surah_number": number,
                "reciter_id": session["reciter_id"],
            }

            edit_message(
                chat_id,
                message_id,
                "🎙️ قاری: %s\n"
                "سوورەت: %s\n\n"
                "ژمارەی ئایەتی دەستپێک بنووسە (1–%d).\n\n"
                "بۆ هەڵوەشاندنەوە /cancel بنووسە."
                % (
                    RECITERS[session["reciter_id"]]["name"],
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
        answer_callback(
            callback_id,
            str(exc)[:180],
        )


def main():
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "FFmpeg دامەزرێنە؛ بۆ ئەم بۆتە پێویستە."
        )

    if not os.path.exists(
        os.path.join(BASE_DIR, "quran.ttf")
    ):
        print(
            "ئاگاداری: quran.ttf لە هەمان فۆڵدەری کۆدەکە دابنێ."
        )

    print(
        "Bot running; each video is sent only to its requesting chat."
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
                exc,
                file=sys.stderr,
            )
            time.sleep(3)


if __name__ == "__main__":
    main()
