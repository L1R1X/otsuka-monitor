# -*- coding: utf-8 -*-
"""
Парсеры дополнительных магазинов:
  t-route.net       — Shopify, отдаёт products.json
  ority.shop-pro.jp — Colorme, обычный HTML (EUC-JP)
"""
import json
import re
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "ja,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- t-route
TROUTE_NAME = "t-Route"
TROUTE_URL = "https://t-route.net"


def fetch_troute():
    """Список товаров магазина t-route.net через Shopify JSON."""
    items = []
    for page in range(1, 12):           # до 2750 товаров, с запасом
        url = f"{TROUTE_URL}/products.json?limit=250&page={page}"
        data = json.loads(_get(url).decode("utf-8"))
        chunk = data.get("products", [])
        if not chunk:
            break
        for p in chunk:
            variants = p.get("variants") or [{}]
            price = variants[0].get("price") or "?"
            available = any(v.get("available") for v in variants)
            items.append({
                "uid": f"tr{p['id']}",
                "title": p.get("title", "").strip(),
                "price": f"{int(float(price)):,}".replace(",", " ") + " ¥"
                         if price != "?" else "?",
                "link": f"{TROUTE_URL}/products/{p['handle']}",
                "shop": TROUTE_NAME,
                "extra": "" if available else "нет в наличии",
            })
        if len(chunk) < 250:
            break
    return items


# ---------------------------------------------------------------- ority
ORITY_NAME = "Ority"
ORITY_URL = "https://ority.shop-pro.jp"
ORITY_SEARCH = ORITY_URL + "/?mode=srh&sort=n&keyword="

# у распроданных вместо class="price" стоит class="soldout",
# а у части карточек блока с ценой нет вовсе — он необязателен
_LI_RE = re.compile(
    r'<li>\s*<a href="\?pid=(\d+)">(.*?)</a>\s*'
    r'(?:<span class="(price|soldout)">([^<]*)</span>)?',
    re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _ority_page(page):
    """Одна страница выдачи: список товаров."""
    url = ORITY_SEARCH if page == 1 else f"{ORITY_SEARCH}&page={page}"
    html = _get(url).decode("euc_jp", "replace")

    # товары лежат в блоке <div class="product"> ... </div>,
    # внутри которого много рядов <ul class="product"> по 4 карточки
    start = html.find('<div class="product">')
    if start == -1:
        return []
    end = html.find('<div id="footer"', start)
    if end == -1:
        end = len(html)
    block = html[start:end]

    items = []
    seen_pid = set()
    for pid, inner, kind, price in _LI_RE.findall(block):
        if pid in seen_pid:          # карточка повторилась в блоке
            continue
        seen_pid.add(pid)
        title = _TAG_RE.sub("", inner)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        sold = (kind == "soldout")
        items.append({
            "uid": f"or{pid}",
            "title": title,
            "price": ("—" if sold or not price
                      else price.strip().replace("円", " ¥")),
            "link": f"{ORITY_URL}/?pid={pid}",
            "shop": ORITY_NAME,
            "extra": "распродано" if sold else "",
        })
    return items


def fetch_ority(max_pages=60):
    """Все товары магазина ority.shop-pro.jp (Colorme, EUC-JP).

    На странице 24 карточки, но часть из них — повтор блока
    «рекомендуем», поэтому новых обычно 12. Пустая страница
    (совсем без карточек) означает конец списка.
    """
    items = []
    seen = set()
    empty_in_row = 0
    for page in range(1, max_pages + 1):
        try:
            chunk = _ority_page(page)
        except Exception:
            break
        if not chunk:                    # страницы кончились
            break
        fresh = [c for c in chunk if c["uid"] not in seen]
        if fresh:
            empty_in_row = 0
            seen.update(c["uid"] for c in fresh)
            items.extend(fresh)
        else:
            empty_in_row += 1
            if empty_in_row >= 3:        # три страницы подряд без новинок
                break
    return items


# ---------------------------------------------------------------- общий вход
SOURCES = [
    ("t-Route", fetch_troute),
    ("Ority", fetch_ority),
]


if __name__ == "__main__":
    for name, fn in SOURCES:
        try:
            got = fn()
            print(f"{name}: {len(got)} товаров")
            for it in got[:3]:
                print("   ", it["title"][:55], "|", it["price"])
        except Exception as e:
            print(f"{name}: ОШИБКА {type(e).__name__}: {e}")
