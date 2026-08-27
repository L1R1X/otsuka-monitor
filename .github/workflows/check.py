# -*- coding: utf-8 -*-
"""
Облачный монитор новинок Fishing Otsuka.
Запускается автоматически на серверах GitHub каждые 30 минут.
Компьютер пользователя не нужен вообще.
"""

import gzip
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

# ============================================================
#  НАСТРОЙКИ
# ============================================================

KEYWORDS = [
    "オリカラ　スプーン",   # オオツカオリカラスプーン - блёсны
    "オリカラ　プラグ",     # オオツカオリカラプラグ - воблеры
]

TARGET_CAT = "all"

# Японский сайт блокирует запросы из дата-центров (ошибка 403 Forbidden),
# поэтому с серверов GitHub идём через читающий прокси r.jina.ai.
# Если однажды он перестанет работать - поставь False и запускай локально.
USE_MIRROR = True

NOTIFY_RESTOCK = True
NOTIFY_PRICE_DROP = True

# Токен и chat_id берутся из «секретов» GitHub.
TELEGRAM_TOKEN = os.environ.get("TG_TOKEN", "")

# Кому слать. Можно несколько получателей: в секрете TG_CHAT
# перечисли их через запятую, например:  111111111,222222222
TELEGRAM_CHATS = [c.strip() for c in os.environ.get("TG_CHAT", "").split(",")
                  if c.strip()]

# ============================================================

BASE = "https://www.fishing-otsuka.co.jp/troutshopjp/ja/index.php"
ITEM_URL = "https://www.fishing-otsuka.co.jp/troutshopjp/ja/index.php?uid={}"
HERE = os.path.dirname(os.path.abspath(__file__))

# База сохраняется в КОРЕНЬ репозитория, а не рядом со скриптом.
# Внутри .github/workflows/ GitHub запрещает боту создавать файлы
# (нужно особое разрешение "workflows"), поэтому кладём наружу.
STATE_FILE = os.path.join(os.getcwd(), "state.json")
NET_TIMEOUT = 90

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
    "Accept-Encoding": "gzip",
}

MSK = timezone(timedelta(hours=3))


def log(msg):
    print("[{}] {}".format(datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S"), msg),
          flush=True)


def fetch(url, tries=5):
    """Качает страницу. С серверов GitHub - через прокси r.jina.ai,
    потому что магазин отдаёт 403 на запросы из дата-центров."""
    if USE_MIRROR:
        target = "https://r.jina.ai/" + url
        # r.jina.ai отвергает браузерные заголовки - шлём голый запрос
        hdrs = {"Accept": "text/plain"}
    else:
        target = url
        hdrs = HEADERS
    last = None
    delay = 5
    for n in range(1, tries + 1):
        try:
            req = urllib.request.Request(target, headers=hdrs)
            data = urllib.request.urlopen(req, timeout=NET_TIMEOUT).read()
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return data.decode("utf-8", "replace")
        except Exception as e:
            last = e
            log("  сеть моргнула ({}/{}): {}".format(n, tries, e))
            if n < tries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
    raise last


MIRROR_RE = re.compile(
    r'\[!\[Image \d+:[^\]]*\]\([^)]*\)\]\('
    r'[^)]*?uid=([A-Za-z0-9_-]+)\s+"([^"]*)"\)\s*'
    r'([A-Za-z0-9_-]+)?\s*(.*?)\s*¥([\d,]+)\s*在庫\s*(\d+)', re.S)


def parse_mirror(text):
    """Разбор страницы, полученной через r.jina.ai (формат markdown)."""
    items = {}
    for m in MIRROR_RE.finditer(text):
        uid, title, code, spec, price, stock = m.groups()
        title = html.unescape(title).strip()
        spec = html.unescape(spec or "").strip()
        # имя товара = заголовок без хвоста со спецификацией
        name = title
        if spec and spec in title:
            name = title.replace(spec, "").strip()
        items[uid] = {
            "name": name or title,
            "spec": spec,
            "code": (code or "").strip(),
            "price": int(price.replace(",", "")),
            "stock": int(stock),
        }
    return items


def parse_cards(page_html):
    items = {}
    for ch in page_html.split('<div class="main_card">')[1:]:
        uid = re.search(r'index\.php\?uid=([A-Za-z0-9_-]+)', ch)
        if not uid:
            continue
        uid = uid.group(1)

        title = re.search(r'title="(.*?)"', ch)
        title = html.unescape(title.group(1)).strip() if title else ""

        name = re.search(r'<div class="item_name">.*?>([^<]+)</a>', ch, re.S)
        name = html.unescape(name.group(1)).strip() if name else title

        spec = re.search(r'<div class="list_spec">(.*?)</div>', ch, re.S)
        spec = html.unescape(re.sub(r"<[^>]+>", "", spec.group(1))).strip() if spec else ""

        code = re.search(r'<small class="item_code">([^<]*)</small>', ch)
        code = code.group(1).strip() if code else ""

        price = re.search(r'main_price[^>]*>\s*(?:&yen;|¥)?\s*([\d,]+)', ch)
        price = int(price.group(1).replace(",", "")) if price else None

        stock = re.search(r'main_zaiko.*?badge[^>]*>([^<]*)<', ch, re.S)
        stock_txt = stock.group(1).strip() if stock else ""
        stock_n = int(stock_txt) if stock_txt.isdigit() else 0

        items[uid] = {"name": name, "spec": spec, "code": code,
                      "price": price, "stock": stock_n}
    return items


def scan_keyword(keyword, max_pages=40):
    found = {}
    for p in range(1, max_pages + 1):
        params = {"target_cat": TARGET_CAT, "m": "keyword", "fm": "y", "p": str(p)}
        if keyword:
            params["keyword"] = keyword
        url = BASE + "?" + urllib.parse.urlencode(params, encoding="utf-8")

        page = fetch(url)

        if USE_MIRROR:
            items = parse_mirror(page)
        else:
            if "</html>" not in page[-2000:] and "</body>" not in page[-2000:]:
                raise IOError("страница {} докачалась не полностью".format(p))
            items = parse_cards(page)
        if not items:
            break
        if set(items) <= set(found):
            break
        found.update(items)
        time.sleep(1.5)
    return found


def _post_one(chat_id, text, tries=4):
    """Шлёт одному получателю. Возвращает True/False."""
    url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN)
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode()
    delay = 5
    for n in range(1, tries + 1):
        try:
            urllib.request.urlopen(
                urllib.request.Request(url, data=payload), timeout=60).read()
            return True
        except Exception as e:
            # 400/403 = неверный id или не нажат Start. Повторять бессмысленно.
            if "403" in str(e) or "400" in str(e):
                log("Получатель {} недоступен (неверный id или не нажат "
                    "Start у бота) - пропускаю".format(chat_id))
                return False
            log("Telegram не ответил для {} ({}/{}): {}".format(chat_id, n, tries, e))
            if n < tries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
    return False


def tg_send(text):
    """Рассылает сообщение всем получателям из TG_CHAT."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATS:
        log("!!! Не заданы секреты TG_TOKEN / TG_CHAT")
        log(re.sub(r"<[^>]+>", "", text))
        return False
    ok_count = 0
    for chat_id in TELEGRAM_CHATS:
        if _post_one(chat_id, text):
            ok_count += 1
        time.sleep(0.5)          # не частим, лимит Telegram ~30 msg/sec
    if ok_count < len(TELEGRAM_CHATS):
        log("Доставлено {} из {} получателей".format(ok_count, len(TELEGRAM_CHATS)))
    return ok_count > 0


def describe(uid, it):
    price = "¥{:,}".format(it["price"]) if it["price"] else "цена не указана"
    parts = ["<b>{}</b>".format(html.escape(it["name"]))]
    if it["spec"]:
        parts.append(html.escape(it["spec"]))
    parts.append("{} · в наличии: {}".format(price, it["stock"]))
    parts.append(ITEM_URL.format(uid))
    return "\n".join(parts)


def send_chunked(header, blocks):
    chunk = [header]
    for b in blocks:
        if len("\n".join(chunk)) + len(b) > 3200:
            tg_send("\n".join(chunk))
            chunk = []
        chunk.append("")
        chunk.append(b)
    if chunk:
        tg_send("\n".join(chunk))


def main():
    all_items = {}
    for kw in KEYWORDS:
        got = scan_keyword(kw)
        log("Запрос «{}»: найдено {} позиций".format(kw or "ВСЕ", len(got)))
        all_items.update(got)

    if not all_items:
        log("Ничего не получено — выхожу, состояние не трогаю")
        return 0

    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            log("state.json повреждён, начинаю заново")

    known = state.get("items", {})
    first_run = not known

    if known and len(all_items) < len(known) * 0.65:
        log("Получено {} вместо {} — похоже на сбой. Базу не трогаю."
            .format(len(all_items), len(known)))
        return 0

    new_items, restocked, cheaper = [], [], []
    for uid, it in all_items.items():
        old = known.get(uid)
        if old is None:
            new_items.append((uid, it))
        else:
            if NOTIFY_RESTOCK and old.get("stock", 0) == 0 and it["stock"] > 0:
                restocked.append((uid, it))
            if (NOTIFY_PRICE_DROP and old.get("price") and it["price"]
                    and it["price"] < old["price"]):
                cheaper.append((uid, it, old["price"]))

    state = {"items": all_items,
             "last_check": datetime.now(MSK).isoformat(timespec="seconds")}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    if first_run:
        log("Первый запуск: запомнил {} товаров".format(len(all_items)))
        tg_send("☁️ Облачный монитор запущен!\n\n"
                "В базу записано {} товаров.\n"
                "Теперь я работаю на серверах GitHub — компьютер можно "
                "выключать, VPN не нужен.\n\n"
                "Проверка каждые 30 минут.".format(len(all_items)))
        return 0

    if new_items:
        log("НОВЫХ ТОВАРОВ: {}".format(len(new_items)))
        send_chunked("🆕 <b>Новые товары ({})</b>:".format(len(new_items)),
                     [describe(u, i) for u, i in new_items])

    if restocked:
        log("Вернулось в наличие: {}".format(len(restocked)))
        send_chunked("♻️ <b>Снова в наличии ({})</b>:".format(len(restocked)),
                     [describe(u, i) for u, i in restocked[:15]])

    if cheaper:
        log("Подешевело: {}".format(len(cheaper)))
        send_chunked("📉 <b>Снижение цены ({})</b>:".format(len(cheaper)),
                     ["{}\nбыло ¥{:,} → стало ¥{:,}".format(describe(u, i), o, i["price"])
                      for u, i, o in cheaper[:15]])

    if not (new_items or restocked or cheaper):
        log("Изменений нет ({} позиций)".format(len(all_items)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
