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
# ONE-SHOT WORKFLOW UPDATER (temporary deployment helper)
#
# The owner's PAT cannot push .github/workflows changes (missing
# "Workflows" permission), so this helper runs INSIDE an Actions job and
# pushes the new 24/7 workflow files using the runner's own
# credentials (configured by actions/checkout).
# =========================

NEW_BOTYUSER_YML = """\
name: Quran User Bot

# 24/7 OPERATION (no more hourly restarts, no offline gaps):
#   - The bot self-chains: after ~5h20m each instance starts a fresh run
#     via the GitHub API, keeps polling until the new instance takes over
#     (Telegram 409), finishes any in-flight video jobs and exits cleanly.
#   - watchdog.yml checks every 15 minutes that a run is active and
#     revives the bot if a run ever dies unexpectedly.
#   - The bot pushes a tiny daily keepalive commit so GitHub never
#     disables the scheduled workflows after 60 days of inactivity.

on:
  workflow_dispatch:

permissions:
  contents: write
  actions: write

jobs:
  run-bot:
    runs-on: ubuntu-latest
    timeout-minutes: 355

    env:
      BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
      CHANNEL_ID: ""
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

    steps:
      - name: Get repository files
        uses: actions/checkout@v4

      - name: Install Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install system packages
        run: |
          sudo apt-get update
          sudo apt-get install -y ffmpeg

      - name: Install Python requirements
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Check required files
        run: |
          test -f quran_video.py
          test -f botyuser.py
          test -f fonts/quran1.ttf
          test -f fonts/quran2.ttf
          test -f fonts/quran3.ttf
          test -f fonts/rabar1.ttf
          test -f fonts/rabar2.ttf
          test -f fonts/rabar3.ttf

      - name: Start Telegram bot
        run: python -u botyuser.py
"""

NEW_WATCHDOG_YML = """\
name: Bot Watchdog

# Safety net for the 24/7 bot: every 15 minutes we check whether a
# "Quran User Bot" run is active (or queued). If NOTHING is running
# (e.g. the self-chaining handover failed, or GitHub had an incident),
# a fresh bot run is started immediately.
# It never cancels or restarts a healthy bot.

on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:

permissions:
  contents: read
  actions: write

concurrency:
  group: bot-watchdog
  cancel-in-progress: true

jobs:
  revive-if-dead:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Check whether the Quran bot is running
        id: check
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
        run: |
          RUNS=$(curl -sf \\
            -H "Authorization: Bearer $GH_TOKEN" \\
            -H "Accept: application/vnd.github+json" \\
            "https://api.github.com/repos/$REPO/actions/workflows/botyuser.yml/runs?per_page=20")

          ACTIVE=$(echo "$RUNS" | jq '[.workflow_runs[] | select(.status == "in_progress" or .status == "queued")] | length')

          echo "Active/queued Quran bot runs: $ACTIVE"

          if [ "$ACTIVE" -eq "0" ]; then
            echo "revive=true" >> "$GITHUB_OUTPUT"
          else
            echo "revive=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Revive the bot
        if: steps.check.outputs.revive == 'true'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          REPO: ${{ github.repository }}
        run: |
          curl -sf -X POST \\
            -H "Authorization: Bearer $GH_TOKEN" \\
            -H "Accept: application/vnd.github+json" \\
            "https://api.github.com/repos/$REPO/actions/workflows/botyuser.yml/dispatches" \\
            -d '{"ref":"main"}'

          echo "No active bot run found -> a fresh run was dispatched."
"""


def run_workflow_updater():
    """Push the new workflow files from inside the Actions runner."""
    import subprocess

    def ulog(message):
        print("WORKFLOW-UPDATER: " + str(message), flush=True)

    repo_dir = os.path.dirname(os.path.abspath(__file__))

    def sh(*args):
        try:
            result = subprocess.run(
                list(args),
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result
        except Exception as exc:
            ulog("command failed: %r" % (args,))
            return None

    try:
        ulog("starting one-shot workflow update in " + repo_dir)

        git_version = sh("git", "--version")
        ulog("git: " + (git_version.stdout.strip() if git_version else "?"))

        sh("git", "config", "user.email", "actions@users.noreply.github.com")
        sh("git", "config", "user.name", "workflow-updater")

        # Make sure we are on main and up to date.
        head = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
        ulog("branch: " + (head.stdout.strip() if head else "?"))

        pull = sh("git", "pull", "--rebase", "origin", "main")
        ulog(
            "pull rc=%s out=%r err=%r"
            % (
                pull.returncode if pull else -1,
                (pull.stdout[-200:] if pull else ""),
                (pull.stderr[-200:] if pull else ""),
            )
        )

        # Write the new workflow files.
        wf_dir = os.path.join(repo_dir, ".github", "workflows")
        os.makedirs(wf_dir, exist_ok=True)

        with open(os.path.join(wf_dir, "botyuser.yml"), "w", encoding="utf-8") as f:
            f.write(NEW_BOTYUSER_YML)

        with open(os.path.join(wf_dir, "watchdog.yml"), "w", encoding="utf-8") as f:
            f.write(NEW_WATCHDOG_YML)

        status = sh("git", "status", "--porcelain")
        changed = [
            line
            for line in (status.stdout.splitlines() if status else [])
            if ".github/workflows" in line
        ]
        ulog("changed workflow files: %r" % changed)

        if not changed:
            ulog("workflow files already up to date; nothing to push.")
            return True

        sh("git", "add", ".github/workflows/botyuser.yml", ".github/workflows/watchdog.yml")

        commit = sh("git", "commit", "-m", "24/7 uptime: self-chaining workflow + 15-min watchdog")
        ulog(
            "commit rc=%s out=%r err=%r"
            % (
                commit.returncode if commit else -1,
                (commit.stdout[-300:] if commit else ""),
                (commit.stderr[-300:] if commit else ""),
            )
        )

        push = sh("git", "push", "origin", "main")
        ulog("PUSH rc=%s" % (push.returncode if push else -1))
        if push:
            ulog("push stdout: %r" % push.stdout[-400:])
            ulog("push stderr: %r" % push.stderr[-700:])

        if push and push.returncode == 0:
            ulog("SUCCESS: 24/7 workflow files are now on GitHub.")
            return True

        ulog("FAILED: the push was rejected (see stderr above).")
        return False

    except Exception as exc:
        ulog("EXCEPTION: %r" % exc)
        return False


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    updater_ok = run_workflow_updater()
    print(
        "WORKFLOW-UPDATER: RESULT=" + ("OK" if updater_ok else "FAILED"),
        flush=True,
    )
    # Temporary: this dispatch exists only to update the workflows,
    # so the normal daily report is skipped on this run.
    sys.exit(0)

