
import os
import json
import random
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# =========================
# CONFIGURATION
# =========================
TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]


def get_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "QuranHadithBot/1.0"}
    )

    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# =========================
# RANDOM QURAN AYAH
# =========================
def random_ayah():
    try:
        # API خۆی ئایەتێکی هەڕەمەکی هەڵدەبژێرێت.
        data = get_json(
            "https://api.alquran.cloud/v1/ayah/random"
        )

        if data.get("code") != 200:
            raise ValueError("Quran API error")

        ayah = data["data"]

        return (
            "📖 ئایەتی ڕۆژ لە قورئانی پیرۆز\n\n"
            f"﴿ {ayah['text']} ﴾\n\n"
            f"📚 سوورەت: {ayah['surah']['name']}\n"
            f"🔢 ژمارەی ئایەت: {ayah['numberInSurah']}\n"
            f"🌐 سەرچاوە: https://alquran.cloud/"
        )

    except Exception:
        return (
            "📖 ئایەتی قورئان\n\n"
            "ئێستا نەمانتوانی ئایەتەکە وەربگرین.\n"
            "سەرچاوە: https://alquran.cloud/"
        )


# =========================
# RANDOM AUTHENTIC HADITH
# =========================
HADITHS = [
    {
        "text": "إِنَّمَا الْأَعْمَالُ بِالنِّيَّاتِ",
        "source": "صحيح البخاري، حديث ١"
    },
    {
        "text": (
            "مَنْ كَانَ يُؤْمِنُ بِاللَّهِ وَالْيَوْمِ الْآخِرِ "
            "فَلْيَقُلْ خَيْرًا أَوْ لِيَصْمُتْ"
        ),
        "source": "صحيح البخاري، حديث ٦٠١٨؛ صحيح مسلم، حديث ٤٧"
    },
    {
        "text": (
            "لَا يُؤْمِنُ أَحَدُكُمْ حَتَّى يُحِبَّ لِأَخِيهِ "
            "مَا يُحِبُّ لِنَفْسِهِ"
        ),
        "source": "صحيح البخاري، حديث ١٣؛ صحيح مسلم، حديث ٤٥"
    },
    {
        "text": (
            "الْمُسْلِمُ مَنْ سَلِمَ الْمُسْلِمُونَ "
            "مِنْ لِسَانِهِ وَيَدِهِ"
        ),
        "source": "صحيح البخاري، حديث ١٠؛ صحيح مسلم، حديث ٤٠"
    },
    {
        "text": (
            "يَسِّرُوا وَلَا تُعَسِّرُوا، "
            "وَبَشِّرُوا وَلَا تُنَفِّرُوا"
        ),
        "source": "صحيح البخاري، حديث ٦٩؛ صحيح مسلم، حديث ١٧٣٤"
    },
    {
        "text": "الْكَلِمَةُ الطَّيِّبَةُ صَدَقَةٌ",
        "source": "صحيح البخاري، حديث ٢٩٨٩؛ صحيح مسلم، حديث ١٠٠٩"
    },
    {
        "text": "مَنْ لَا يَرْحَمُ لَا يُرْحَمُ",
        "source": "صحيح البخاري، حديث ٥٩٩٧؛ صحيح مسلم، حديث ٢٣١٨"
    },
    {
        "text": "الدِّينُ النَّصِيحَةُ",
        "source": "صحيح مسلم، حديث ٥٥"
    },
    {
        "text": (
            "مَنْ سَلَكَ طَرِيقًا يَلْتَمِسُ فِيهِ عِلْمًا "
            "سَهَّلَ اللَّهُ لَهُ بِهِ طَرِيقًا إِلَى الْجَنَّةِ"
        ),
        "source": "صحيح مسلم، حديث ٢٦٩٩"
    },
    {
        "text": "لَا تَغْضَبْ",
        "source": "صحيح البخاري، حديث ٦١١٦"
    }
]


def random_hadith():
    hadith = random.choice(HADITHS)

    return (
        "🕌 فەرموودەی ڕۆژ\n\n"
        f"«{hadith['text']}»\n\n"
        f"📚 سەرچاوە: {hadith['source']}\n"
        "ℹ️ دەتوانیت ژمارە و دەقی فەرموودەکە "
        "لە کتێبی سەرچاوەکەدا بپشکنیت."
    )


# =========================
# BUILD REPORT
# =========================
def make_report():
    now = datetime.now(ZoneInfo("Asia/Baghdad"))
    date = now.strftime("%Y-%m-%d")

    ayah = random_ayah()
    hadith = random_hadith()

    return (
        "🕌 ڕۆحانیەتی ڕۆژ\n"
        f"📅 {date}\n\n"
        f"{ayah}\n\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{hadith}\n\n"
        "━━━━━━━━━━━━━━\n"
        "— ڕێگای ئیسلام"
    )


# =========================
# SEND TELEGRAM MESSAGE
# =========================
def send_message(message):
    import urllib.parse

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHANNEL,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    req = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(
                "Telegram rejected the message: "
                + str(result)
            )


if __name__ == "__main__":
    send_message(make_report())
