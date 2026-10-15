
import os
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]

def get_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "KurdishDailyNewsBot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def get_text(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "KurdishDailyNewsBot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")

def news(query, limit=3):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en"
    })
    try:
        root = ET.fromstring(get_text(url))
        items = []
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title", "هەواڵ")
            link = item.findtext("link", "")
            if link:
                items.append(f"• {title}\n{link}")
        return "\n".join(items) or "هەواڵی نوێ نەدۆزرایەوە."
    except Exception:
        return "سەرچاوەی هەواڵ بۆ کاتی ئێستا بەردەست نییە."

def weather():
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            "latitude=36.19&longitude=44.01"
            "&current=temperature_2m,wind_speed_10m"
            "&daily=temperature_2m_max,temperature_2m_min"
            "&timezone=Asia%2FBaghdad&forecast_days=1"
        )
        d = get_json(url)
        c = d["current"]
        day = d["daily"]
        return (
            f"پلەی گەرمی ئێستا: {c['temperature_2m']}°C\n"
            f"زۆرترین: {day['temperature_2m_max'][0]}°C\n"
            f"کەمترین: {day['temperature_2m_min'][0]}°C\n"
            f"خێرایی با: {c['wind_speed_10m']} km/h\n"
            "سەرچاوە: https://open-meteo.com/"
        )
    except Exception:
        return "زانیاری کەشوهەوا بەردەست نییە."

def currency():
    try:
        d = get_json("https://open.er-api.com/v6/latest/USD")
        rate = d["rates"]["IQD"]
        return (
            f"١ دۆلار ≈ {rate:,.0f} دیناری عێراقی\n"
            "ئەمە نرخی ئاڵوگۆڕی API ـە؛ "
            "نرخی بازاڕی ناوخۆیی نییە.\n"
            "سەرچاوە: https://www.exchangerate-api.com/"
        )
    except Exception:
        return "نرخی دۆلار بە سەرچاوەی بەردەست نەدۆزرایەوە."

def gold():
    try:
        d = get_json(
            "https://api.goldprice.dev/v1/prices?symbol=XAU-USD-SPOT"
        )
        prices = d.get("prices", [])
        item = next(
            (x for x in prices if x.get("symbol") == "XAU-USD-SPOT"),
            prices[0] if prices else None
        )
        if not item or not item.get("price"):
            raise ValueError("No gold price")
        ounce = float(item["price"])
        gram24 = ounce / 31.1034768
        return (
            f"زێڕی ٢٤ عەیار: نزیکەی ${gram24:.2f} بۆ هەر گرامێک\n"
            f"زێڕی ٢١ عەیار: نزیکەی ${gram24 * 21 / 24:.2f} بۆ هەر گرامێک\n"
            f"ئۆنس: ${ounce:.2f}\n"
            "نرخی جیهانیی خاوە؛ کرێ و قازانجی فرۆشیار تێدا نییە.\n"
            "سەرچاوە: https://goldprice.dev/"
        )
    except Exception:
        return (
            "نرخی زێڕ ئێستا بە شێوەی دڵنیابەخش نەدۆزرایەوە؛ "
            "تکایە سەرچاوەکە بپشکنە: https://goldprice.dev/"
        )

def make_report():
    now = datetime.now(ZoneInfo("Asia/Baghdad"))
    date = now.strftime("%Y-%m-%d")

    return (
        f"📰 ڕاپۆرتی شەوانە\n"
        f"📅 {date}\n\n"
        f"💵 نرخی دۆلار\n{currency()}\n\n"
        f"🪙 نرخی زێڕ\n{gold()}\n\n"
        f"🤖 دوا هەواڵەکانی AI\n"
        f"{news('artificial intelligence AI latest news', 3)}\n\n"
        f"🌍 ئێران و ئەمریکا\n"
        f"{news('Iran United States conflict latest news', 3)}\n\n"
        f"🌦️ کەشوهەوای هەولێر\n{weather()}\n\n"
        "ℹ️ هەواڵەکان لە سەرچاوەکانەوە وەردەگیرێن؛ "
        "ناونیشانی هەواڵ بە تەنها بەڵگەی پشتڕاستکردنەوە نییە.\n"
        "— ڕاپۆرتی خۆکار"
    )

def send_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHANNEL,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode())
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected the message")

if __name__ == "__main__":
    send_message(make_report())
