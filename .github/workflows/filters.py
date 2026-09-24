# -*- coding: utf-8 -*-
"""
Фильтр новинок по типу снасти.

Нужен, чтобы из больших магазинов (Wild-1, t-Route) в чат попадала
только рыболовная мелочь, а не палатки и спальники.

Чтобы выключить фильтр и получать вообще всё - поставь
ONLY_INTERESTING = False в check.py
"""

import re
import unicodedata

# ------------------------------------------------------------------
#  Что считаем интересным
# ------------------------------------------------------------------

BLESNY = [
    "スプーン", "spoon",
    "ドーナ", "ノア", "アキュラシー", "エクシード", "ハント",
    "シャース", "ティアロ", "ピット", "ファクター",
]

VOBLERY = [
    "プラグ", "plug", "ミノー", "minnow", "クランク", "crank",
    "シャッド", "shad", "ニョロ", "バイブ", "vib",
    "ペンシル", "pencil", "ポッパー", "popper",
    "クラピー", "モカ", "ミナモブレイカー", "ザッガー",
    "フローティング", "シンキング", "floating", "sinking",
]

KRYUCHKI = [
    "フック", "hook", "トレブル", "treble", "バーブレス", "barbless",
    "シングルフック", "針",
]

# «фук» встречается и в названиях воблеров, и у аксессуаров -
# такие товары крючками не считаем
NE_KRYUCHKI = [
    "フックケース", "hook case", "フックリリーサー", "hook releaser",
    "フックテープ", "hook tape", "フックシャープナー", "フックホルダー",
    "フックキーパー", "フックスタンド", "フックボックス",
    # части названий воблеров: "モカ DR2フックF", "ラトルDR 2フックSS"
    "2フック", "２フック", "dr2フック", "フック仕様",
]

# ...но если рядом стоят слова-признаки самого крючка,
# товар всё-таки крючок (напр. "シングルフックSS バーブレス")
VSE_TAKI_KRYUCHOK = [
    "バーブレス", "barbless", "シングルフック", "single hook",
    "エリアフック", "area hook", "エキスパートフック",
]

# Оригинальные и лимитированные расцветки - самое ценное
RASCVETKI = [
    "オリカラ", "オリジナルカラー", "別注", "限定カラー", "限定色",
    "特注", "シグネチャーカラー", "コラボカラー", "ショップカラー",
    "店舗限定", "スペシャルカラー", "テストカラー", "受注生産",
]

# Магазины помечают свои эксклюзивы квадратными скобками:
#   「ValkeIN シャインライド #ツブラッシュ【パワーショップ限定】」
#   「ラッキークラフト ワウ 37F #新月【HERO'S】」
# Любые 【...】 в названии приманки - почти всегда именно такая пометка.
SHOP_TAG_RE = re.compile(r"【[^】]{1,24}】")

# Приманки часто названы просто моделью и весом, без слов
# "スプーン"/"プラグ" - ловим по весу в граммах и номеру цвета.
WEIGHT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*g\b", re.I)
COLOR_RE = re.compile(r"#\s*[0-9A-Za-zぁ-んァ-ヶ一-龥ー]{1,16}")
# 限定○○カラー - лимитированная расцветка с названием посередине
LIMITED_COLOR_RE = re.compile(r"限定[^\s]{0,12}カラー")

# Леска: флюорокарбон, поводки, шок-лидеры, PE, эстер.
# Только явные слова - «ライン» само по себе слишком общее
# (ラインローラー, #ベリーライン - цвет, タフライン - оттяжка палатки).
LESKA = [
    "フロロ", "fluoro", "flouro", "フロロカーボン",
    "ショックリーダー", "shock leader", "ティペット", "tippet",
    "ハリス", "peライン", "pe line", "ナイロンライン",
    "エステルライン", "テーパーリーダー", "リーダー",
]

# «リーダー» и «ライン» встречаются в аксессуарах и названиях цветов
NE_LESKA = [
    "ローラー", "roller", "カッター", "cutter", "ハサミ", "scissors",
    "ポーチ", "pouch", "グリース", "ケース", "case", "マグ", "mug",
    "クリッパー", "clipper", "スナップ", "snap", "チェンジャー",
    "シール", "マーカー", "ホルダー", "スプール", "テンカラ",
    "#", "カラー", "color",
]

CATEGORIES = [
    ("🎨 расцветка", RASCVETKI),
    ("🥄 блесна", BLESNY),
    ("🐟 воблер", VOBLERY),
    ("🪝 крючки", KRYUCHKI),
    ("🧵 леска", LESKA),
]


def _norm(text):
    """Привести название к единому виду: полуширинная катакана -> обычная,
    латиница в нижний регистр. Иначе «ﾉｰﾁﾗｽ» не совпадёт с «ノーチラス»."""
    return unicodedata.normalize("NFKC", text or "").lower()


def _has(text, words):
    return any(w in text for w in words)


# Товары, которые весом/цветом не ловим: одежда, коробки, инструмент
NE_PRIMANKA = [
    "ワレット", "wallet", "ケース", "case", "ボックス", "box",
    "ロッド", "rod", "リール", "reel", "net",
    "ウェア", "ウェーダー", "バッグ", "bag", "キャップ", "cap",
    "ステッカー", "sticker", "tシャツ", "パーカー", "タオル",
    "プライヤー", "リリーサー", "ホルダー", "スタンド", "ハンガー",
    "テント", "チェア", "テーブル", "ランタン", "シュラフ", "寝袋",
    "リールケース", "ドラグ", "ハンドル", "ノブ", "スプール",
    "ティップ", "グリップ", "ポーチ",
    "帽子", "ソックス", "グローブ", "ベスト", "ジャケット",
    "snugpak", "スナグパック", "スノーピーク", "snow peak",
    "テンマクデザイン", "ストーブ", "焚火", "マグネット",
]

# Эти слова отсеивают товар только если стоят в НАЧАЛЕ названия
# или отдельным словом - иначе «マットレッド» (цвет приманки)
# спутается со «спальным матом».
# コット (раскладушка) сидит внутри チョコット, ネット (сачок) -
# внутри マグネット/プラネット. Только отдельным словом.
NE_PRIMANKA_STRICT = ["マット", "カバー", "コット", "ネット"]

# Катакана «слипается»: ネット сидит внутри マグネット, コット внутри
# チョコット. Считаем совпадением только если слева и справа от слова
# НЕ стоит другая катакана - то есть это отдельное слово, а не хвост.
_KATAKANA = "ァ-ヶー"


def _has_strict(text, words):
    """Как _has, но слово должно стоять отдельно, а не внутри другого."""
    for w in words:
        w = w.lower()
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            before = text[i - 1] if i > 0 else ""
            after = text[i + len(w)] if i + len(w) < len(text) else ""
            if not (_re_kata(before) or _re_kata(after)):
                return True
            start = i + 1
    return False


def _re_kata(ch):
    return bool(ch) and "ァ" <= ch <= "ヶ" or ch == "ー"


def classify(title):
    """Вернуть список меток товара. Пустой список = не интересен."""
    t = _norm(title)
    tags = []

    for label, words in CATEGORIES:
        if label == "🧵 леска":
            if _has(t, [w.lower() for w in LESKA]) and \
               not _has(t, [w.lower() for w in NE_LESKA]):
                tags.append(label)
            continue
        if label == "🪝 крючки":
            # крючки - только если это действительно крючки
            if _has(t, [w.lower() for w in KRYUCHKI]) and (
                    _has(t, [w.lower() for w in VSE_TAKI_KRYUCHOK])
                    or not _has(t, [w.lower() for w in NE_KRYUCHKI])):
                tags.append(label)
            continue
        if _has(t, [w.lower() for w in words]):
            tags.append(label)

    # аксессуары и снаряжение метками не награждаем
    if _has(t, NE_PRIMANKA) or _has_strict(t, NE_PRIMANKA_STRICT):
        return [x for x in tags if x in ("🪝 крючки", "🧵 леска")]

    # пометка магазина в 【скобках】 - эксклюзивная расцветка
    if "🎨 расцветка" not in tags and SHOP_TAG_RE.search(t):
        tags.append("🎨 расцветка")

    # «限定〜カラー» с текстом посередине: 限定ZAKカラー, 限定さんだ〜カラー
    if "🎨 расцветка" not in tags and LIMITED_COLOR_RE.search(t):
        tags.append("🎨 расцветка")

    # Приманка без явного слова в названии. Достаточно одного
    # из признаков: вес в граммах ИЛИ номер цвета (#12, #マットレッド).
    # Ищем в нормализованном тексте: в японских магазинах вес часто
    # записан полноширинными символами - ０，８ｇ вместо 0.8g.
    if not tags:
        if WEIGHT_RE.search(t) or COLOR_RE.search(t):
            tags.append("🎣 приманка")

    return tags


def is_interesting(title):
    return bool(classify(title))


if __name__ == "__main__":
    import json
    import sys
    from collections import Counter

    path = sys.argv[1] if len(sys.argv) > 1 else "state.json"
    data = json.load(open(path, encoding="utf-8"))["items"]

    cnt = Counter()
    kept = 0
    for v in data.values():
        tags = classify(v.get("name", ""))
        if tags:
            kept += 1
        for t in tags:
            cnt[t] += 1

    print("всего товаров :", len(data))
    print("под фильтр    :", kept)
    for t, c in cnt.most_common():
        print(f"  {t:<14} {c}")
