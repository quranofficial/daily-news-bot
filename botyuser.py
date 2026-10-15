
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

import quran_video as qv

BOT_TOKEN = os.environ["BOT_TOKEN"]
TG = "https://api.telegram.org/bot" + BOT_TOKEN + "/"
QURAN_API = "https://api.alquran.cloud/v1"

MAX_AYAHS = max(1, int(os.environ.get("MAX_AYAHS_PER_VIDEO", "20")))
POLL_TIMEOUT = 30
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "quran_user_jobs")
TIMINGS_DIR = os.path.join(BASE_DIR, "reciter_timings")

TIMINGS_BASE_URL = (
    "https://github.com/quranofficial/daily-news-bot/raw/main/"
)

SESSIONS = {}
_SURAH_CACHE = None


# Only reciters with doxx == "true".
# Each audio URL is BASE + SSS.mp3, for example 003.mp3.
RECITERS = {
    "3": {
        "name": "هەندرێن عبدالرحمان",
        "base": "https://download.tvquran.com/download/recitations/343/266/",
    },
    "1": {
        "name": "پێشەوا قادر",
        "base": "https://github.com/Hiwaselah/qari_kurdi_mutasil/releases/download/peshawa_kurdi_mutasil/",
    },
    "2": {
        "name": "ڕەعد محمد",
        "base": "https://github.com/Hiwaselah/qari_kurdi_mutasil/releases/download/Raad/",
    },
    "62": {
        "name": "ڕزگار محمد",
        "base": "https://github.com/Hiwaselah/qari_kurdi_mutasil/releases/download/rzgar_kurdi_mutasil/",
    },
    "5": {
        "name": "فەرمان شوانی",
        "base": "https://github.com/Hiwaselah/qari_kurdi_mutasil/releases/download/farman/",
    },
    "6": {
        "name": "عبدالهادی",
        "base": "https://github.com/Hiwaselah/qari_kurdi_mutasil/releases/download/abdulhadi_kurdi_mutasil/",
    },
    "11": {
        "name": "سعد الغامدي",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/Sa3d_ghamdi/",
    },
    "8": {
        "name": "ياسر الدوسري",
        "base": "http://download.quranicaudio.com/quran/yasser_ad-dussary/",
    },
    "23": {
        "name": "إدريس أبكر",
        "base": "https://github.com/Hiwaselah/qari_kurdi_mutasil/releases/download/idrees_abkar/",
    },
    "21": {
        "name": "محمد اللحيدان",
        "base": "https://server8.mp3quran.net/lhdan/",
    },
    "66": {
        "name": "نبیل الرفاعي",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/nabil_rifa3i/",
    },
    "10": {
        "name": "ماهر المعيقلي",
        "base": "https://github.com/Hiwaselah/sounds011_11qari3_gapless/releases/download/maher_al_muaiqly/",
    },
    "76": {
        "name": "ماهر المعيقلي (مجود)",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/muaiqly_kfgqpc/",
    },
    "37": {
        "name": "عبدالباسط عبدالصمد",
        "base": "https://download.quranicaudio.com/quran/abdul_basit_murattal/",
    },
    "75": {
        "name": "عبدالباسط عبدالصمد (مجود)",
        "base": "http://download.quranicaudio.com/quran/abdulbaset_mujawwad/",
    },
    "31": {
        "name": "محمود خليل الحصري",
        "base": "https://download.quranicaudio.com/quran/mahmood_khaleel_al-husaree/",
    },
    "77": {
        "name": "محمود خليل الحصري (مجود)",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/husary_muallim/",
    },
    "42": {
        "name": "محمد صديق المنشاوي",
        "base": "https://download.quranicaudio.com/quran/muhammad_siddeeq_al-minshaawee/",
    },
    "81": {
        "name": "محمد صديق المنشاوي (مجود)",
        "base": "https://download.tvquran.com/download/TvQuran.com__Al-Minshawi-Mojawad/",
    },
    "12": {
        "name": "أيمن سويد",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/dr_ayman_suwaid/",
    },
    "74": {
        "name": "أكرم العلاقمي",
        "base": "http://download.quranicaudio.com/quran/akram_al_alaqmi/",
    },
    "78": {
        "name": "عبدالرحمن السديس",
        "base": "https://download.quranicaudio.com/quran/abdurrahmaan_as-sudays/",
    },
    "79": {
        "name": "أبو بكر الشاطري",
        "base": "https://server11.mp3quran.net/shatri/",
    },
    "65": {
        "name": "إسلام صبحي",
        "base": "https://github.com/H4zh4n/TafseeriNoor/raw/files/mp3/Islam_Sobhi/",
    },
    "73": {
        "name": "عبدالله كامل",
        "base": "https://server16.mp3quran.net/kamel/Rewayat-Hafs-A-n-Assem/",
    },
    "46": {
        "name": "هيثم الدخين",
        "base": "https://server16.mp3quran.net/h_dukhain/Rewayat-Hafs-A-n-Assem/",
    },
    "14": {
        "name": "سعود الشريم",
        "base": "http://download.quranicaudio.com/quran/sa3ood_al-shuraym/",
    },
    "16": {
        "name": "أحمد النفيس",
        "base": "https://server16.mp3quran.net/nufais/Rewayat-Hafs-A-n-Assem/",
    },
    "19": {
        "name": "محمد أيوب",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/Mhamad_ayub/",
    },
    "20": {
        "name": "نورين محمد صديق",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/noreen_siddiq/",
    },
    "22": {
        "name": "غسان الشوربجي",
        "base": "https://download.tvquran.com/download/recitations/313/220/",
    },
    "24": {
        "name": "أحمد بن علي العجمي",
        "base": "https://server10.mp3quran.net/ajm/",
    },
    "25": {
        "name": "عبدالله الخلف",
        "base": "https://server14.mp3quran.net/khalf/",
    },
    "26": {
        "name": "ناصر القطامي",
        "base": "http://download.quranicaudio.com/quran/nasser_bin_ali_alqatami/",
    },
    "27": {
        "name": "صلاح الهاشم",
        "base": "https://server12.mp3quran.net/salah_hashim_m/",
    },
    "28": {
        "name": "صلاح البدير",
        "base": "http://download.quranicaudio.com/quran/salahbudair/",
    },
    "30": {
        "name": "توفيق الصايغ",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/tawfeeq_as_sawaigh/",
    },
    "32": {
        "name": "بدر التركي",
        "base": "https://server10.mp3quran.net/bader/Rewayat-Hafs-A-n-Assem/",
    },
    "34": {
        "name": "سهل ياسين",
        "base": "http://download.quranicaudio.com/quran/sahl_yaaseen/",
    },
    "36": {
        "name": "صلاح بو خاطر",
        "base": "https://github.com/Hiwaselah/sounds011_11qari3_gapless/releases/download/salah_bukhatir/",
    },
    "38": {
        "name": "علي جابر",
        "base": "https://github.com/Hiwaselah/sounds011_11qari3_gapless/releases/download/ali_jaber/",
    },
    "39": {
        "name": "عبدالعزيز الأحمد",
        "base": "https://server11.mp3quran.net/a_ahmed/",
    },
    "40": {
        "name": "عادل ريان",
        "base": "https://server8.mp3quran.net/ryan/",
    },
    "41": {
        "name": "خليفة الطنيجي",
        "base": "https://github.com/Hiwaselah/sounds011_11qari3_gapless/releases/download/khalifa_taniji/",
    },
    "44": {
        "name": "فارس عباد",
        "base": "https://github.com/Hiwaselah/sounds011_11qari3_gapless/releases/download/fares_abbad/",
    },
    "45": {
        "name": "بندر بليله",
        "base": "https://server6.mp3quran.net/balilah/",
    },
    "47": {
        "name": "خالد الجليل",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/khalid_jalil/",
    },
    "48": {
        "name": "عبدالودود حنيف",
        "base": "https://server8.mp3quran.net/wdod/",
    },
    "49": {
        "name": "فؤاد الخامري",
        "base": "https://download.tvquran.com/download/tvQuran.com__Alkhamre/",
    },
    "51": {
        "name": "هاني الرفاعي",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/hani_rifai/",
    },
    "52": {
        "name": "عبدالعزيز الزهراني",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/abdulaziz_zahrani/",
    },
    "53": {
        "name": "عبدالرحمن العوسي",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/abdulrahman_aloosi/",
    },
    "54": {
        "name": "عبدالله بصفر",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/abdullah_basfar/",
    },
    "56": {
        "name": "وديع اليمني",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/wadee3_alyamani/",
    },
    "58": {
        "name": "نعمة الحسن",
        "base": "https://server6.mp3quran.net/aloosi/",
    },
    "59": {
        "name": "صلاح البدير",
        "base": "http://download.quranicaudio.com/quran/salahbudair/",
    },
    "60": {
        "name": "أحمد الحواشي",
        "base": "https://server11.mp3quran.net/hawashi/",
    },
    "61": {
        "name": "ماجد الزامل",
        "base": "https://server9.mp3quran.net/zaml/",
    },
    "9": {
        "name": "مشاري بن راشد العفاسي",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/mishari_alafasy/",
    },
    "63": {
        "name": "عبد الرشيد صوفي",
        "base": "https://github.com/Hiwaselah/audio003/releases/download/abdurrashid_sufi/",
    },
    "13": {
        "name": "خالد القحطاني",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/khalid_alqahtani/",
    },
    "67": {
        "name": "ابراهيم الأخضر",
        "base": "https://github.com/Hiwaselah/19_xatm_mutasl_2024/releases/download/ibrahim_alakhdar/",
    },
    "71": {
        "name": "محمد عثمان خان",
        "base": "https://download.tvquran.com/download/recitations/319/235/",
    },
    "80": {
        "name": "وليد إبراهيم",
        "base": "https://server9.mp3quran.net/waleed/",
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
            raise RuntimeError("نەتوانرا لیستی سوورەتەکان وەربگیرێت.")

        _SURAH_CACHE = data["data"]

    return _SURAH_CACHE


def keyboard_reciters():
    rows = []
    items = list(RECITERS.items())

    # Two buttons per row to keep the list compact.
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


def download_file(url, output_path, min_size=100):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QuranVideoBot/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            content = response.read()

        if len(content) < min_size:
            raise RuntimeError("فایلە وەرگیراوەکە زۆر بچووکە.")

        with open(output_path, "wb") as f:
            f.write(content)

    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise


def download_surah_audio(surah_number, reciter_id, output_path):
    reciter = RECITERS.get(str(reciter_id))

    if not reciter:
        raise ValueError("قاریی هەڵبژێردراو دروست نییە.")

    filename = "%03d.mp3" % int(surah_number)
    url = reciter["base"] + filename

    try:
        download_file(url, output_path, min_size=1000)
    except Exception as exc:
        raise RuntimeError(
            "نەتوانرا دەنگی سوورەتەکە وەربگیرێت.\n"
            "قاری: %s\n"
            "URL: %s\n"
            "هۆکار: %s"
            % (reciter["name"], url, exc)
        )


def download_timings_database(reciter_id):
    os.makedirs(TIMINGS_DIR, exist_ok=True)

    db_path = os.path.join(
        TIMINGS_DIR,
        str(reciter_id) + ".db",
    )

    # Reuse a downloaded database unless it is missing or invalid.
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT sura, ayah, time FROM timings LIMIT 1")
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
        conn.execute("SELECT sura, ayah, time FROM timings LIMIT 1")
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
            "هیچ زانیارییەکی timing بۆ ئەم سوورەتە لە داتابەیسدا نییە."
        )

    result = {}

    for sura, ayah, start_time in rows:
        result[int(ayah)] = float(start_time)

    # Detect whether values are milliseconds or seconds.
    # Quran surah timestamps in milliseconds are usually large values.
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
        raise RuntimeError("نەتوانرا ماوەی فایلە دەنگەکە بخوێندرێتەوە.")

    try:
        duration = float(proc.stdout.strip())
    except Exception:
        raise RuntimeError("ماوەی فایلە دەنگەکە دروست نییە.")

    if duration <= 0:
        raise RuntimeError("ماوەی دەنگەکە صفرە.")

    return duration


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

        missing = [x for x in required if x not in columns]
        if missing:
            raise RuntimeError(
                "ستوونی پێویست نەدۆزرایەوە: " + ", ".join(missing)
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
            + "WHERE CAST(" + quoted(columns["suraid"]) + " AS INTEGER)=? "
            + "AND CAST(" + quoted(columns["ayah"]) + " AS INTEGER) BETWEEN ? AND ? "
            + "ORDER BY CAST(" + quoted(columns["ayah"]) + " AS INTEGER)"
        )

        rows = conn.execute(
            sql,
            (int(surah_number), int(start_ayah), int(end_ayah)),
        ).fetchall()

        if len(rows) != count:
            raise RuntimeError(
                "ئایەتە داواکراوەکان لە داتابەیسدا بە تەواوی نەدۆزرایەوە."
            )

        result = []

        for row in rows:
            arabic = str(row["arabic_text"] or "").strip()
            tafsir = str(row["tafsir_text"] or "").strip()

            if not arabic:
                raise RuntimeError("دەقی عەرەبی ئایەت بەتاڵە.")

            result.append({
                "surah_number": int(surah_number),
                "surah_name": str(row["sura_name"] or surah["name"]).strip(),
                "ayah_number": int(row["ayah_number"]),
                "arabic": arabic,
                "tafsir": tafsir or "تەفسیری ئەم ئایەتە بەردەست نییە.",
                "translation": tafsir or "تەفسیری ئەم ئایەتە بەردەست نییە.",
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
            "FFmpeg: " + (proc.stderr[-1500:] or "هەڵەی نەناسراو")
        )


def build_video(chat_id, ayahs, reciter_id):
    stamp = str(int(time.time()))
    job_dir = os.path.join(JOBS_DIR, str(chat_id), stamp)
    os.makedirs(job_dir, exist_ok=True)

    try:
        surah_number = int(ayahs[0]["surah_number"])

        # Download the whole surah once: 003.mp3, 001.mp3, etc.
        full_audio_path = os.path.join(
            job_dir,
            "%03d.mp3" % surah_number,
        )
        download_surah_audio(
            surah_number,
            reciter_id,
            full_audio_path,
        )

        timings = get_ayah_timings(reciter_id, surah_number)
        audio_duration = get_audio_duration(full_audio_path)

        # Build clips using the start time of each ayah and the next
        # ayah's start time. The last ayah ends at the audio duration.
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

            # Some databases may not include the next ayah if the
            # selected ayah is not the final ayah of the surah.
            if end_time <= start_time:
                raise RuntimeError(
                    "کاتی timing بۆ ئایەتی %d دروست نییە."
                    % ayah_number
                )

            end_time = min(end_time, audio_duration)
            duration = end_time - start_time

            if start_time < 0 or start_time >= audio_duration or duration <= 0:
                raise RuntimeError(
                    "کاتی ئایەتی %d لە سنووری دەنگەکەدا نییە."
                    % ayah_number
                )

            image_path = os.path.join(
                job_dir, "image_%03d.png" % i
            )
            clip_path = os.path.join(
                job_dir, "clip_%03d.mp4" % i
            )

            qv.create_ayah_image(ayah, i, image_path)

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

        list_path = os.path.join(job_dir, "clips.txt")

        with open(list_path, "w", encoding="utf-8") as f:
            for clip in clips:
                safe_clip = clip.replace("'", "'\\''")
                f.write("file '%s'\n" % safe_clip)

        output = os.path.join(job_dir, "quran_video.mp4")

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
            "زۆرترین ئایەت بۆ هەر ڤیدیۆیەک: %d" % MAX_AYAHS,
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
        send_message(chat_id, "بۆ دەستپێکردن /start بنووسە.")
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
                    "ژمارەی ئایەت لە 1 تا %d بێت."
                    % int(surah["numberOfAyahs"])
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
            send_message(chat_id, "ژمارەیەکی دروست بنووسە. " + str(exc))

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

    send_message(chat_id, "تکایە بە هەنگاوەکانی بۆتەکە بەردەوام بە.")


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

            answer_callback(callback_id, "قاری هەڵبژێردرا")
            return

        if data.startswith("page:"):
            page = max(0, int(data.split(":", 1)[1]))

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
                answer_callback(callback_id, "سەرەتا قاری هەڵبژێرە.")
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

            answer_callback(callback_id, "سوورەت هەڵبژێردرا")
            return

        answer_callback(callback_id)

    except Exception as exc:
        answer_callback(callback_id, str(exc)[:180])


def main():
    if shutil.which("ffmpeg") is None:
        raise SystemExit("FFmpeg دامەزرێنە؛ بۆ ئەم بۆتە پێویستە.")

    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe لەگەڵ FFmpeg پێویستە.")

    if not os.path.exists(os.path.join(BASE_DIR, "quran.ttf")):
        print("ئاگاداری: quran.ttf لە هەمان فۆڵدەری کۆدەکە دابنێ.")

    print("Bot running; each video is sent only to its requesting chat.")

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
            print("Polling error:", exc, file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    main()
