
import os
import urllib.request
import subprocess

OUTPUT = "quran_ikhlas.mp4"

def download_file(url, path):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QuranVideoBot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        with open(path, "wb") as file:
            file.write(response.read())

def main():
    print("ئامادەکردنی ڤیدیۆی سووڕەتی الإخلاص...")

    # لە هەنگاوەکانی دواتردا دەنگ و دیزاینی ڤیدیۆ زیاد دەکەین.
    print("ئەم فایلە ئامادەیە بۆ دروستکردنی ڤیدیۆ.")

if __name__ == "__main__":
    main()
