import os
import json
import random
import sqlite3
import urllib.request
import urllib.parse
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
# SEND TELEGRAM MESSAGE
# =========================
def send_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHANNEL,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram rejected the message: " + str(result)
        )


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        message = make_report()
        send_message(message)
        print("Daily Quran ayah and tafsir sent successfully.")

    except Exception as error:
        print(f"ERROR: {error}")
        raise
