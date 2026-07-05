"""
checkpoint_20260731.py — One-shot: 31 Jul 2026 KDP decision checkpoint.

Summarizes the ADHD-series experiment + easy-taxes numbers and Telegrams the
decision rule Bui set on 3 Jul 2026 (see memory: libra-kdp-pause-focus-decision):
- movement (reviews / KU pages / book 2-3 orders) -> trial Amazon Ads $3-5/day
- flat -> freeze Libra as passive lottery
Removes its own cron line after running.
"""
import json
import subprocess
import sys
from pathlib import Path

LIBRA = Path(__file__).resolve().parent.parent
KDP = LIBRA.parent / "kdp"
sys.path.insert(0, str(LIBRA))

BOOKS = [
    ("adhd-self-help-adults-es", "ADHD เล่ม1 (แจกฟรี 2-6 ก.ค., ลด $2.99 7 ก.ค.)"),
    ("adhd-adults-workbook-es", "ADHD เล่ม2 $2.99"),
    ("adhd-adults-focus-work-relationships-es", "ADHD เล่ม3 $2.99"),
    ("easy-taxes-self-employed-spain", "ภาษีสเปน (เคยขายจริง)"),
]
CRON_MARK = "checkpoint_20260731.py"


def tg(msg):
    import os
    import urllib.parse
    import urllib.request

    def _env(k):
        v = os.getenv(k)
        if v:
            return v
        envf = LIBRA / ".env"
        if envf.exists():
            for line in envf.read_text().splitlines():
                if line.startswith(k + "="):
                    return line.split("=", 1)[1].strip()
        return None

    tok, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    payload = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{tok}/sendMessage", data=payload, timeout=20)


def month_totals(slug):
    f = KDP / slug / "feedback-history.json"
    if not f.exists():
        return 0, 0
    hist = json.loads(f.read_text())
    jul = [s for s in hist if str(s.get("date", "")).startswith("2026-07")]
    if not jul:
        return 0, 0
    last = jul[-1]
    return last.get("mtd_orders", 0), last.get("mtd_kenp", 0)


def main():
    lines = ["🔖 CHECKPOINT 31 ก.ค. — ตัดสินอนาคต Libra KDP\n", "ตัวเลขเดือน ก.ค.:"]
    moved = False
    for slug, label in BOOKS:
        o, k = month_totals(slug)
        lines.append(f"• {label}: {o} orders / {k} KU pages")
        if slug != "adhd-self-help-adults-es" and (o or k):
            moved = True
    _, k1 = month_totals("adhd-self-help-adults-es")
    if k1:
        moved = True
    lines.append("\n(รีวิว: เช็คมือที่หน้า Amazon ของแต่ละเล่ม)")
    lines.append("\nกฎที่ตกลงไว้ 3 ก.ค.:")
    if moved:
        lines.append("→ มีความเคลื่อนไหว ✅ = คุยเรื่องทดลอง Amazon Ads $3-5/วัน "
                     "เฉพาะเล่มที่มีรีวิว")
    else:
        lines.append("→ ตัวเลขนิ่ง ❌ = แช่แข็ง Libra เป็น passive, ย้ายแรงไปช่องที่มีคนดู")
    lines.append("\nบอก AI ในแชทเพื่อตัดสินใจต่อได้เลย")
    tg("\n".join(lines))

    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    keep = [l for l in (r.stdout or "").splitlines() if CRON_MARK not in l]
    subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True, check=True)


if __name__ == "__main__":
    main()
