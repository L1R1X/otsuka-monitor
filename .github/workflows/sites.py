# -*- coding: utf-8 -*-
"""
Парсеры дополнительных магазинов:
  t-route.net       — Shopify, отдаёт products.json
  ority.shop-pro.jp — Colorme, обычный HTML (EUC-JP)
"""
import json
import re
import time
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


def _get_json(url, tries=3):
    """JSON по адресу. Если сайт не пускает напрямую (сервера GitHub
    иногда блокируются), пробуем через читающее зеркало r.jina.ai."""
    last = None
    for attempt in range(tries):
        try:
            return json.loads(_get(url).decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2 + attempt * 3)
    # прямой путь не сработал - идём через зеркало
    try:
        req = urllib.request.Request("https://r.jina.ai/" + url,
                                     headers={"Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=90) as r:
            text = r.read().decode("utf-8", "replace")
        i = text.find('{"products"')
        if i >= 0:
            return json.loads(text[i:])
    except Exception:
        pass
    raise last


def fetch_shopify(base_url, shop_name, prefix, max_pages=14):
    """Товары любого магазина на Shopify (у всех одинаковый products.json)."""
    items = []
    for page in range(1, max_pages + 1):
        url = f"{base_url}/products.json?limit=250&page={page}"
        data = _get_json(url)
        chunk = data.get("products", [])
        if not chunk:
            break
        for p in chunk:
            variants = p.get("variants") or [{}]
            price = variants[0].get("price") or "?"
            available = any(v.get("available") for v in variants)
            items.append({
                "uid": f"{prefix}{p['id']}",
                "title": p.get("title", "").strip(),
                "price": f"{int(float(price)):,}".replace(",", " ") + " ¥"
                         if price != "?" else "?",
                "link": f"{base_url}/products/{p['handle']}",
                "shop": shop_name,
                "extra": "" if available else "нет в наличии",
            })
        if len(chunk) < 250:
            break
    return items


def fetch_troute():
    return fetch_shopify(TROUTE_URL, TROUTE_NAME, "tr")


# ---------------------------------------------------------------- Velvet Arts
VELVET_NAME = "Velvet Arts"
VELVET_URL = "https://velvetarts.co.jp"


def fetch_velvet():
    return fetch_shopify(VELVET_URL, VELVET_NAME, "vl")


# ---------------------------------------------------------------- Zarky
ZARKY_NAME = "Zarky"
ZARKY_URL = "https://fishingshop-zarky.com"


def fetch_zarky():
    return fetch_shopify(ZARKY_URL, ZARKY_NAME, "zk")


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


# ---------------------------------------------------------------- Wild-1
# Магазин на движке EC-Orange: товары отдаёт не HTML, а внутренний API.
# Нужны кука сессии и заголовок X-XSRF-TOKEN, иначе ответ 400.
WILD1_NAME = "Wild-1"
WILD1_URL = "https://webshop.wild1.co.jp"
WILD1_PAGE = WILD1_URL + "/websitevue/onlinestore/search"
WILD1_API = WILD1_URL + "/websiteapi/product/list"

_WILD1_SEARCH = {
    "product_name": "",
    "product_category_id": "",
    "brand_id": "",
    "feature_id": "",
    "tag_name": "",
    "product_sku_price_selling_list_from": "",
    "product_sku_price_selling_list_to": "",
    "variation_size_ids": [],
    "variation_color_ids": [],
    "sale_only": False,
    "stock_only": False,
    "sort_parts": "1",
}


def fetch_wild1(page_row=500, max_pages=12):
    """Товары Wild-1 через внутренний API магазина."""
    import http.cookiejar
    import urllib.parse

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))

    # 1) заходим на страницу поиска - сервер выдаёт куки и XSRF-токен
    req = urllib.request.Request(WILD1_PAGE, headers={"User-Agent": UA})
    opener.open(req, timeout=60).read()

    token = ""
    for c in jar:
        if c.name == "XSRF-TOKEN":
            token = urllib.parse.unquote(c.value)
            break
    if not token:
        raise RuntimeError("Wild-1: не выдан XSRF-токен")

    # 2) постранично забираем каталог
    items = []
    seen = set()
    for page in range(max_pages):
        body = json.dumps({
            "search": _WILD1_SEARCH,
            "request": {},
            "page": page,
            "page_row": page_row,
        }).encode("utf-8")
        req = urllib.request.Request(WILD1_API, data=body, headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-XSRF-TOKEN": token,
            "Referer": WILD1_PAGE,
        })
        data = json.loads(opener.open(req, timeout=90).read().decode("utf-8"))
        if not data.get("success"):
            break
        chunk = (data.get("result") or {}).get("data") or []
        if not chunk:
            break
        for p in chunk:
            pid = p.get("product_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            lo = p.get("product_sku_price_selling_min_taxed")
            hi = p.get("product_sku_price_selling_max_taxed")
            def _yen(v):
                return f"{int(float(v)):,}".replace(",", " ") + " ¥"
            try:
                price_text = _yen(lo)
                if hi and float(hi) != float(lo):
                    price_text += "–" + _yen(hi)
            except (TypeError, ValueError):
                price_text = "—"
            brand = (p.get("brand_name") or "").strip()
            name = (p.get("product_name") or "").strip()
            items.append({
                "uid": f"w1{pid}",
                "title": (brand + " " + name).strip() if brand else name,
                "price": price_text,
                "link": f"{WILD1_URL}/websitevue/onlinestore/product/{pid}",
                "shop": WILD1_NAME,
                "extra": "",
            })
        if len(chunk) < page_row:
            break
    return items


# ---------------------------------------------------------------- общий вход
SOURCES = [
    ("t-Route", fetch_troute),
    ("Ority", fetch_ority),
    ("Velvet Arts", fetch_velvet),
    ("Zarky", fetch_zarky),
    ("Wild-1", fetch_wild1),
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
