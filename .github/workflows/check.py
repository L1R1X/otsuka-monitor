# -*- coding: utf-8 -*-
"""
Облачный монитор новинок Fishing Otsuka.
Запускается автоматически на серверах GitHub каждые 5 минут.
Магазины: Fishing Otsuka, t-Route, Ority.
Компьютер пользователя не нужен вообще.
"""

import concurrent.futures
import gzip
import hmac
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

# Дополнительные магазины (t-Route, Ority) живут в отдельном файле.
try:
    import sites
    EXTRA_SHOPS = sites.SOURCES
except Exception as _e:          # файла нет или он сломан - работаем без него
    EXTRA_SHOPS = []
    print("sites.py недоступен:", _e)

# Фильтр по типу снасти: блёсны, воблеры, крючки, оригинальные расцветки.
# Нужен, чтобы из больших магазинов не сыпались палатки и спальники.
try:
    import filters
except Exception as _e:
    filters = None
    print("filters.py недоступен:", _e)

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

# True  - присылать только блёсны, воблеры, крючки и оригинальные расцветки
# False - присылать вообще все новинки
ONLY_INTERESTING = True

# Сколько незнакомых позиций у ОДНОГО магазина считать перенастройкой,
# а не завозом. Выше порога - запоминаем молча, без спама в Telegram.
BULK_NEW_LIMIT = 50
PARALLEL_SHOPS = 6

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
STATE_FILE = os.path.join(HERE, "state.json")
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


def _post_one(chat_id, text):
    """Шлёт одному получателю. Возвращает True/False."""
    url = "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN)
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=payload), timeout=15
        ) as response:
            return json.load(response).get("ok") is True
    except urllib.error.HTTPError as error:
        log("Telegram отклонил сообщение (HTTP {})".format(error.code))
    except (OSError, ValueError) as error:
        log("Сбой отправки в Telegram ({})".format(type(error).__name__))
    return False


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def save_state(state):
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=1)
    os.replace(temporary, STATE_FILE)


def deliver_pending():
    state = load_state()
    pending = state.get("pending_notifications", [])
    if not pending:
        return 0
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATS:
        log("Задайте секреты TG_TOKEN и TG_CHAT. Очередь сохранена.")
        return 1

    # База публичная, поэтому идентификаторы чатов храним только в секретах.
    recipients = {
        hmac.digest(TELEGRAM_TOKEN.encode(), chat.encode(), "sha256").hex(): chat
        for chat in TELEGRAM_CHATS
    }
    remaining = []
    blocked = set()
    deadline = time.monotonic() + 45
    for entry in pending:
        delivered = set(entry["delivered"])
        for recipient, chat in recipients.items():
            if recipient in delivered or recipient in blocked or time.monotonic() >= deadline:
                continue
            if _post_one(chat, entry["text"]):
                delivered.add(recipient)
                time.sleep(0.5)
            else:
                blocked.add(recipient)
        if not recipients.keys() <= delivered:
            remaining.append({"text": entry["text"], "delivered": sorted(delivered)})

    state["pending_notifications"] = remaining
    save_state(state)
    if remaining:
        log("В очереди Telegram осталось сообщений: {}".format(len(remaining)))
    return int(bool(blocked))


def _interesting(it):
    """Проходит ли товар фильтр по типу снасти."""
    if not ONLY_INTERESTING or filters is None:
        return True
    # Магазины, где мы и так ищем только оригиналки, фильтровать по
    # словарю нельзя: «ロブルアー バベル [RP5]» - настоящая приманка,
    # просто без узнаваемого слова в названии. Отсекаем лишь
    # снаряжение: нобы, ручки, сачки, releaser'ы.
    if it.get("prefiltered"):
        return not filters.is_accessory(it.get("name", ""))
    return filters.is_interesting(it.get("name", ""))


def _tags(it):
    if filters is None:
        return ""
    t = filters.classify(it.get("name", ""))
    if not t and it.get("prefiltered"):
        # своя метка магазина, иначе - эксклюзивная расцветка
        t = [it.get("tag") or "🎨 расцветка"]
    return "  ".join(t)


def describe(uid, it):
    # товар из дополнительного магазина - у него другой набор полей
    if it.get("shop"):
        parts = ["<b>{}</b>".format(html.escape(it["name"]))]
        if _tags(it):
            parts.append(_tags(it))
        line = it.get("price_text") or "цена не указана"
        if it.get("extra"):
            line += " · " + it["extra"]
        parts.append(html.escape(line))
        parts.append("🏬 " + html.escape(it["shop"]))
        parts.append(html.escape(it["link"]))
        return "\n".join(parts)

    price = "¥{:,}".format(it["price"]) if it["price"] else "цена не указана"
    parts = ["<b>{}</b>".format(html.escape(it["name"]))]
    if _tags(it):
        parts.append(_tags(it))
    if it["spec"]:
        parts.append(html.escape(it["spec"]))
    parts.append("{} · в наличии: {}".format(price, it["stock"]))
    parts.append(ITEM_URL.format(uid))
    return "\n".join(parts)


def queue_chunked(pending, header, blocks):
    message = header
    for block in blocks:
        if len(message) + len(block) + 2 > 3200 and message != header:
            pending.append({"text": message, "delivered": []})
            message = header
        message += "\n\n" + block
    if message != header:
        pending.append({"text": message, "delivered": []})


def main():
    state = load_state()
    known = state.get("items", {})
    pending = state.get("pending_notifications", [])
    all_items = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(KEYWORDS) or 1) as pool:
            scanned = list(pool.map(scan_keyword, KEYWORDS))
        if scanned and all(scanned):
            for keyword, items in zip(KEYWORDS, scanned):
                log("Запрос «{}»: найдено {} позиций".format(keyword, len(items)))
                all_items.update(items)
        else:
            log("Otsuka: неполный ответ, сохраняю предыдущую выборку")
    except Exception as error:
        log("Otsuka: ошибка {}, сохраняю предыдущую выборку".format(error))


    # --- дополнительные магазины ---------------------------------
    # Магазины опрашиваем одновременно, а не по очереди. Все они на
    # разных сайтах, поэтому нагрузки это не создаёт, зато общее время
    # равно самому медленному магазину, а не сумме всех.
    def _fetch_one(pair):
        name, fn = pair
        t0 = time.time()
        try:
            return name, fn(), None, time.time() - t0
        except Exception as e:
            return name, None, e, time.time() - t0

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=PARALLEL_SHOPS) as pool:
        # ex.map сохраняет порядок, поэтому лог читается как раньше
        results = list(pool.map(_fetch_one, EXTRA_SHOPS))

    for shop_name, got, err, secs in results:
        if err is not None:
            log("{}: ошибка {} — магазин пропущен ({:.0f} с)"
                .format(shop_name, err, secs))
            continue
        if not got:
            log("{}: пусто — пропускаю ({:.0f} с)".format(shop_name, secs))
            continue
        log("{}: найдено {} позиций за {:.0f} с"
            .format(shop_name, len(got), secs))
        for it in got:
            all_items[it["uid"]] = {
                "name": it["title"],
                "price": None,
                "price_text": it["price"],
                "spec": "",
                "stock": 0,
                "shop": it["shop"],
                "link": it["link"],
                "extra": it.get("extra", ""),
                "prefiltered": it.get("prefiltered", False),
                "tag": it.get("tag", ""),
            }

    # Каждый магазин считаем отдельно: uid начинается с "tr"/"or",
    # у Otsuka - с цифр. Так падение одного магазина не роняет остальные.
    # Каждому магазину - свой префикс uid. Берём название прямо из
    # записи товара, чтобы при добавлении магазина ничего не правил.
    def group_of(uid):
        it = all_items.get(uid) or known.get(uid) or {}
        return it.get("shop") or "Otsuka"

    now_by_group, known_by_group = {}, {}
    for uid in all_items:
        now_by_group.setdefault(group_of(uid), set()).add(uid)
    for uid in known:
        known_by_group.setdefault(group_of(uid), set()).add(uid)

    # Магазин, которого ещё нет в базе, запоминаем молча -
    # иначе первый запуск завалит чат тысячами "новинок".
    silent_groups = set()
    for g, uids in now_by_group.items():
        if not known_by_group.get(g):
            silent_groups.add(g)
            log("{}: первое знакомство, запоминаю {} товаров без уведомлений"
                .format(g, len(uids)))

    if not all_items:
        log("Ни один магазин не вернул каталог")
        return 1

    broken_groups = set()
    for group, old_uids in known_by_group.items():
        now_uids = now_by_group.get(group, set())
        if len(now_uids) < len(old_uids) * 0.65:
            broken_groups.add(group)
            log("{}: получено {} вместо {}, сохраняю предыдущую выборку"
                .format(group, len(now_uids), len(old_uids)))
            for uid in now_uids:
                del all_items[uid]
            for uid in old_uids:
                all_items[uid] = known[uid]

    # Перенастройка магазина. Если у знакомого магазина вдруг оказалось
    # очень много незнакомых позиций - это не завоз, а смена способа
    # сбора (например, JH перевели с «480 новейших» на «только
    # оригиналки»). Молча запоминаем и шлём одну строчку вместо
    # двухсот сообщений.
    rebuilt = {}
    for uid in all_items:
        if uid not in known:
            g = group_of(uid)
            if g in broken_groups or g in silent_groups:
                continue
            rebuilt[g] = rebuilt.get(g, 0) + 1
    rebuilt = {g: n for g, n in rebuilt.items() if n > BULK_NEW_LIMIT}
    for g, n in rebuilt.items():
        silent_groups.add(g)
        log("{}: сразу {} незнакомых позиций - похоже на перенастройку, "
            "запоминаю без уведомлений.".format(g, n))

    new_items, restocked, cheaper = [], [], []
    for uid, it in all_items.items():
        old = known.get(uid)
        g = group_of(uid)
        if g in broken_groups:
            continue
        if old is None:
            if g in silent_groups:
                continue
            if not _interesting(it):
                continue
            new_items.append((uid, it))
        else:
            if NOTIFY_RESTOCK:
                if it.get("shop"):
                    # t-Route / Ority: наличие хранится словами в "extra"
                    # ("нет в наличии", "распродано"). Пусто = есть в продаже.
                    if old.get("extra") and not it.get("extra") \
                            and _interesting(it):
                        restocked.append((uid, it))
                elif old.get("stock", 0) == 0 and it["stock"] > 0 \
                        and _interesting(it):
                    restocked.append((uid, it))
            if (NOTIFY_PRICE_DROP and old.get("price") and it["price"]
                    and it["price"] < old["price"]):
                cheaper.append((uid, it, old["price"]))

    if not known:
        pending.append({
            "text": "☁️ Облачный монитор запущен. В базе {} товаров.".format(len(all_items)),
            "delivered": [],
        })

    if new_items:
        log("НОВЫХ ТОВАРОВ: {}".format(len(new_items)))
        queue_chunked(pending, "🆕 <b>Новые товары ({})</b>:".format(len(new_items)),
                     [describe(u, i) for u, i in new_items])

    if restocked:
        log("Вернулось в наличие: {}".format(len(restocked)))
        queue_chunked(pending, "♻️ <b>Снова в наличии ({})</b>:".format(len(restocked)),
                     [describe(u, i) for u, i in restocked[:15]])

    if cheaper:
        log("Подешевело: {}".format(len(cheaper)))
        queue_chunked(pending, "📉 <b>Снижение цены ({})</b>:".format(len(cheaper)),
                     ["{}\nбыло ¥{:,} → стало ¥{:,}".format(describe(u, i), o, i["price"])
                      for u, i, o in cheaper[:15]])

    if not (new_items or restocked or cheaper):
        log("Изменений нет ({} позиций)".format(len(all_items)))

    state.update(items=all_items,
                 pending_notifications=pending,
                 last_check=datetime.now(MSK).isoformat(timespec="seconds"))
    state.pop("shrink_strikes", None)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(deliver_pending() if sys.argv[1:] == ["--deliver"] else main())
