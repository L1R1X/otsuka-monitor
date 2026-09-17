# -*- coding: utf-8 -*-
"""
Фильтр новинок по типу снасти.

Нужен, чтобы из больших магазинов (Wild-1, t-Route) в чат попадала
только рыболовная мелочь, а не палатки и спальники.

Чтобы выключить фильтр и получать вообще всё - поставь
ONLY_INTERESTING = False в check.py
"""

import re

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
    "店舗限定", "スペシャルカラー", "テストカラー",
]

CATEGORIES = [
    ("🎨 расцветка", RASCVETKI),
    ("🥄 блесна", BLESNY),
    ("🐟 воблер", VOBLERY),
    ("🪝 крючки", KRYUCHKI),
]


def _has(text, words):
    return any(w in text for w in words)


def classify(title):
    """Вернуть список меток товара. Пустой список = не интересен."""
    t = (title or "").lower()
    tags = []

    for label, words in CATEGORIES:
        if label == "🪝 крючки":
            # крючки - только если это действительно крючки
            if _has(t, [w.lower() for w in KRYUCHKI]) and (
                    _has(t, [w.lower() for w in VSE_TAKI_KRYUCHOK])
                    or not _has(t, [w.lower() for w in NE_KRYUCHKI])):
                tags.append(label)
            continue
        if _has(t, [w.lower() for w in words]):
            tags.append(label)

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
