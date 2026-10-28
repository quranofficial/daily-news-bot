import os
import json
import time
import random
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

# =========================
# CONFIGURATION
# =========================
TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]

DATABASE_URL = (
    "https://raw.githubusercontent.com/"
    "sahanddler/home_dialog/main/quran.db"
)


# =========================
# DOWNLOAD DATABASE
# =========================
def download_database():
    request = urllib.request.Request(
        DATABASE_URL,
        headers={"User-Agent": "RegayIslamBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        database_bytes = response.read()

    if not database_bytes:
        raise ValueError("Database is empty")

    return database_bytes


# =========================
# GET RANDOM AYAH + TAFSIR
# =========================
def get_random_ayah():
    database_bytes = download_database()

    with tempfile.NamedTemporaryFile(
        suffix=".db",
        delete=False
    ) as temp_file:
        temp_file.write(database_bytes)
        database_path = temp_file.name

    try:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row

        try:
            cursor = connection.cursor()

            tables = cursor.execute("""
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
            """).fetchall()

            # Find the table containing the required columns.
            selected_table = None
            column_map = {}

            aliases = {
                "text": ["text"],
                "asan": ["asan"],
                "sura_name_ar": ["sura_name_ar"],
                "ayah": ["ayah"]
            }

            for table_row in tables:
                table_name = table_row["name"]

                columns = cursor.execute(
                    f'PRAGMA table_info("{table_name}")'
                ).fetchall()

                actual_columns = {
                    column["name"].lower(): column["name"]
                    for column in columns
                }

                found_columns = {}
                valid_table = True

                for required_name, possible_names in aliases.items():
                    matched_column = None

                    for possible_name in possible_names:
                        if possible_name.lower() in actual_columns:
                            matched_column = actual_columns[
                                possible_name.lower()
                            ]
                            break

                    if matched_column is None:
                        valid_table = False
                        break

                    found_columns[required_name] = matched_column

                if valid_table:
                    selected_table = table_name
                    column_map = found_columns
                    break

            if selected_table is None:
                raise ValueError(
                    "Could not find a table with columns: "
                    "text, asan, sura_name_ar, ayah"
                )

            # Pick one random verse from the database.
            query = (
                f'SELECT '
                f'"{column_map["text"]}" AS verse_text, '
                f'"{column_map["asan"]}" AS tafsir, '
                f'"{column_map["sura_name_ar"]}" AS surah_name, '
                f'"{column_map["ayah"]}" AS ayah_number '
                f'FROM "{selected_table}" '
                f'ORDER BY RANDOM() LIMIT 1'
            )

            row = cursor.execute(query).fetchone()

            if row is None:
                raise ValueError("No verses found in database")

            verse_text = str(row["verse_text"] or "").strip()
            tafsir = str(row["tafsir"] or "").strip()
            surah_name = str(row["surah_name"] or "").strip()
            ayah_number = str(row["ayah_number"] or "").strip()

            if not verse_text:
                raise ValueError("The selected verse has no text")

            if not tafsir:
                tafsir = "تەفسیری ئەم ئایەتە لە داتابەیسدا بەردەست نییە."

            if not surah_name:
                surah_name = "نەناسراو"

            if not ayah_number:
                ayah_number = "نەناسراو"

            return {
                "text": verse_text,
                "tafsir": tafsir,
                "surah": surah_name,
                "ayah": ayah_number
            }

        finally:
            connection.close()

    finally:
        try:
            os.remove(database_path)
        except OSError:
            pass


# =========================
# BUILD DAILY MESSAGE
# =========================
def make_report():
    now = datetime.now(ZoneInfo("Asia/Baghdad"))
    date = now.strftime("%Y-%m-%d")

    ayah = get_random_ayah()

    return (
        "📖 هەر ڕۆژە و ئایەتێک\n"
        f"📅 {date}\n\n"
        f"﴿ {ayah['text']} ﴾\n\n"
        "📖 تەفسیری ئاسان:\n\n"
        f"{ayah['tafsir']}\n\n"
        f"📚 سوڕەتی: {ayah['surah']}\n"
        f"🔢 ژمارەی ئایەت: {ayah['ayah']}\n\n"
        "━━━━━━━━━━━━━━\n"
        "— ڕێگای ئیسلام"
    )


# =========================
# TELEGRAM API HELPERS
# =========================

def telegram_call(method, payload, timeout=30):
    """Call the Telegram Bot API and return the full JSON result.

    On HTTP errors the response body is read and included in the raised
    error, so the Actions log always shows the REAL reason (for example
    "bot is not a member of the channel chat" instead of a bare 403).
    """
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    data = urllib.parse.urlencode(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", "replace")
        except Exception:
            body = ""

        raise RuntimeError(
            f"Telegram {method} -> HTTP {error.code}: {body[:600]}"
        )


# Diagnostics: log who the bot is and whether it may post in the channel.
# This makes the Actions log immediately show WHY a send could fail.
def preflight_checks():
    me = telegram_call("getMe", {})

    if not me.get("ok"):
        raise RuntimeError("getMe failed: " + str(me))

    bot_id = me["result"].get("id")
    bot_username = me["result"].get("username", "?")

    print(f"PREFLIGHT: bot @{bot_username} (id={bot_id}) is valid.", flush=True)

    try:
        chat = telegram_call("getChat", {"chat_id": CHANNEL})
        print(
            "PREFLIGHT: channel OK -> "
            + json.dumps(chat.get("result", {}), ensure_ascii=False)[:300],
            flush=True,
        )

        member = telegram_call(
            "getChatMember",
            {"chat_id": CHANNEL, "user_id": bot_id},
        )
        print(
            "PREFLIGHT: bot status in channel -> "
            + json.dumps(member.get("result", {}), ensure_ascii=False)[:300],
            flush=True,
        )

    except Exception as error:
        print(
            "PREFLIGHT WARNING: channel check failed "
            "(the bot probably cannot post there): " + str(error),
            flush=True,
        )


# =========================
# SEND TELEGRAM MESSAGE (with retries)
#
# GitHub Actions runners sometimes get temporary HTTP 403 / network
# errors from api.telegram.org. Retrying with a backoff for a few
# minutes makes the daily report survive those transient failures.
# =========================
SEND_ATTEMPTS = 8
SEND_BACKOFF_SECONDS = (15, 30, 45, 60, 60, 60, 60)


def send_message(message):
    payload = {
        "chat_id": CHANNEL,
        "text": message,
        "disable_web_page_preview": "true"
    }

    last_error = None

    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            result = telegram_call("sendMessage", payload)

            if not result.get("ok"):
                raise RuntimeError(
                    "Telegram rejected the message: " + str(result)
                )

            return

        except Exception as error:
            last_error = error

            print(
                f"SEND ATTEMPT {attempt}/{SEND_ATTEMPTS} FAILED: {error}",
                flush=True,
            )

            if attempt < SEND_ATTEMPTS:
                wait = SEND_BACKOFF_SECONDS[
                    min(attempt - 1, len(SEND_BACKOFF_SECONDS) - 1)
                ]
                time.sleep(wait)

    raise RuntimeError(
        f"Sending failed after {SEND_ATTEMPTS} attempts: {last_error}"
    )


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        preflight_checks()

        message = make_report()
        send_message(message)
        print("Daily Quran ayah and tafsir sent successfully.")

    except Exception as error:
        print(f"ERROR: {error}")
        raise
