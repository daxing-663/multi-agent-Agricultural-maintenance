"""问答的生产用途过滤与作物标签推断；标签不是农艺事实核验。

问题中的明确作物优先于回答里的举例。生产操作/植物病害意图优先，
避免把涉及人体防护的农药作业问答、食用菌栽培误删为饮食健康内容。
"""

from __future__ import annotations

import re


# (中文规范名, 英文规范名, 其他别名)。较长别名优先，避免 cherry tomato
# 被拆成 cherry，或 Chinese cabbage 被误标成普通 cabbage。
_CROPS = (
    ("番茄", "tomato", ("西红柿", "圣女果", "小番茄", "cherry tomato", "cherry tomatoes", "tomatoes")),
    ("黄瓜", "cucumber", ("cucumbers",)),
    ("辣椒", "pepper", ("甜椒", "青椒", "彩椒", "bell pepper", "bell peppers", "chili", "chilli", "peppers")),
    ("茄子", "eggplant", ("aubergine", "eggplants")),
    ("草莓", "strawberry", ("strawberries",)),
    ("生菜", "lettuce", ()),
    ("白菜", "chinese cabbage", ("大白菜", "小白菜", "bok choy", "pak choi", "napa cabbage")),
    ("甘蓝", "cabbage", ("卷心菜", "圆白菜", "cabbages")),
    ("西兰花", "broccoli", ()),
    ("花椰菜", "cauliflower", ("花菜",)),
    ("芹菜", "celery", ()),
    ("菠菜", "spinach", ()),
    ("油菜", "rapeseed", ("canola",)),
    ("西瓜", "watermelon", ("watermelons",)),
    ("甜瓜", "melon", ("哈密瓜", "cantaloupe", "muskmelon", "melons")),
    ("南瓜", "pumpkin", ("pumpkins",)),
    ("丝瓜", "loofah", ("luffa", "sponge gourd")),
    ("苦瓜", "bitter melon", ("bitter gourd",)),
    ("冬瓜", "winter melon", ("wax gourd", "waxgourd")),
    ("豆角", "cowpea", ("豇豆", "yardlong bean", "cowpeas")),
    ("菜豆", "common bean", ("四季豆", "green bean", "green beans", "french bean")),
    ("豌豆", "pea", ("peas",)),
    ("马铃薯", "potato", ("土豆", "potatoes")),
    ("洋葱", "onion", ("onions",)),
    ("大葱", "scallion", ("spring onion", "green onion", "scallions")),
    ("大蒜", "garlic", ()),
    ("韭菜", "chive", ("chives",)),
    ("萝卜", "radish", ("radishes", "daikon")),
    ("胡萝卜", "carrot", ("carrots",)),
    ("生姜", "ginger", ()),
    ("葡萄", "grape", ("grapes",)),
    ("柑橘", "citrus", ("橘子", "桔子",)),
    ("橙子", "orange", ("oranges",)),
    ("苹果", "apple", ("apples",)),
    ("梨树", "pear", ("梨", "pears")),
    ("桃树", "peach", ("桃", "peaches")),
    ("樱桃", "cherry", ("cherries",)),
    ("香蕉", "banana", ("bananas",)),
    ("荔枝", "lychee", ("litchi",)),
    ("龙眼", "longan", ()),
    ("芒果", "mango", ("mangoes", "mangos")),
    ("茶树", "tea", ()),
    ("烟草", "tobacco", ("烟叶",)),
    ("食用菌", "mushroom", ("蘑菇", "香菇", "平菇", "mushrooms", "shiitake")),
    ("月季", "rose", ("玫瑰", "roses")),
    ("菊花", "chrysanthemum", ()),
    ("兰花", "orchid", ("orchids",)),
    ("水稻", "rice", ()),
    ("小麦", "wheat", ()),
    ("玉米", "maize", ("corn", "sweet corn")),
    ("大豆", "soybean", ("黄豆", "soybeans", "soya bean")),
    ("花生", "peanut", ("peanuts", "groundnut", "groundnuts")),
    ("棉花", "cotton", ()),
    ("高粱", "sorghum", ()),
    ("甘蔗", "sugarcane", ("sugar cane",)),
)

_ALIASES = sorted(
    [(alias.casefold(), zh, en) for zh, en, extras in _CROPS for alias in (zh, en, *extras)],
    key=lambda row: len(row[0]), reverse=True,
)
_CROP_NAMES = {alias: (zh, en) for alias, zh, en in _ALIASES}
_CROP_PATTERN = re.compile("|".join(
    (r"(?<![a-z0-9])" if alias[0].isascii() else "") + re.escape(alias)
    + (r"(?![a-z0-9])" if alias[-1].isascii() else "")
    for alias, _, _ in _ALIASES
))

_PRODUCTION_ZH = (
    "栽培", "种植", "栽种", "栽植", "播种", "育苗", "定植", "移栽", "嫁接", "连作", "轮作",
    "施肥", "肥料", "追肥", "基肥", "灌溉", "浇水", "灌水", "土壤", "墒情", "植保",
    "病虫害", "虫害", "病害", "病原", "农药", "杀菌剂", "杀虫剂", "除草剂", "温室", "大棚",
    "棚内", "作物", "植株", "苗期", "幼苗", "根系", "叶片", "生育期", "花期", "采收",
    "果园", "田间", "农田", "农机", "扦插", "育种", "农业", "农艺", "农场", "产量",
    "授粉", "坐果", "结瓜", "抽薹", "抽穗", "滴灌", "喷灌", "疏花", "病斑", "霜霉",
    "晚疫", "缺素", "黄化", "药害", "肥害", "长势", "生长期间", "植物生长", "无土栽培",
)
_FOOD_HEALTH_ZH = (
    "人体", "心脏病", "高血压", "糖尿病", "关节炎", "痛风", "尿酸", "减肥", "抗癌", "癌症",
    "美容", "养颜", "美白", "免疫力", "保健", "养生", "食疗", "饮食", "膳食", "饮用",
    "食用", "食物", "食材", "烹饪", "烹调", "烹制", "凉拌", "菜谱", "营养价值", "营养成分",
    "维生素", "番茄红素", "减脂", "热量", "卡路里", "消化", "胆固醇", "血糖", "血脂",
    "汁同食", "熟吃", "生吃", "煮熟", "炒菜", "煲汤", "健康益处", "营养素", "搭配可以",
)
_PRODUCTION_EN = re.compile(
    r"\b(?:cultivat\w*|planting|sow\w*|seedling\w*|transplant\w*|propagat\w*|prun\w*|harvest\w*|"
    r"irrigat\w*|fertili[sz]\w*|soil\w*|crop\w*|greenhouse\w*|glasshouse\w*|pest\w*|fungicid\w*|"
    r"insecticid\w*|herbicid\w*|pesticid\w*|blight|mildew|wilt\w*|pollinat\w*|yield\w*|farm\w*|"
    r"agricultur\w*|agronom\w*|weed\w*|mulch\w*|compost\w*|germinat\w*|waterlog\w*|salinity|seedbed\w*)\b"
    r"|\b(?:plant (?:disease|growth|health)|nutrient deficiency|water stress|fruit cracking|fruit rot|blossom end)\b"
)
_FOOD_HEALTH_EN = re.compile(
    r"\b(?:human\w*|heart disease|blood pressure|diabet\w*|cholesterol|cancer|nutriti\w*|vitamin\w*|"
    r"diet\w*|eat\w*|edible|cook\w*|recipe\w*|calori\w*|weight loss|digesti\w*|immune|"
    r"health benefits?|culinary|salad\w*|juice\w*|roast\w*|boil\w*|bake\w*|soup\w*)\b"
)


def _production(text: str) -> bool:
    return any(term in text for term in _PRODUCTION_ZH) or bool(_PRODUCTION_EN.search(text.casefold()))


def _food_health(text: str) -> bool:
    return any(term in text for term in _FOOD_HEALTH_ZH) or bool(_FOOD_HEALTH_EN.search(text.casefold()))


def is_food_health_qa(question: str, answer: str = "") -> bool:
    """排除纯食品/人体健康问答，保留问题中明确的农业生产意图。"""
    if _production(question):
        return False
    if _food_health(question):
        return True
    return _food_health(answer) and not _production(answer)


def crops_in(text: str, *, lang: str) -> list[str]:
    text = text.casefold()
    found = []
    for match in _CROP_PATTERN.finditer(text):
        zh, en = _CROP_NAMES[match.group(0)]
        canonical = zh if lang == "zh" else en
        if canonical not in found:
            found.append(canonical)
    return found


def crop_metadata(question: str, answer: str, *, lang: str) -> dict:
    explicit = crops_in(question, lang=lang)
    inferred = [] if explicit else crops_in(answer, lang=lang)
    return {
        "crops": explicit or inferred,
        "crop_scope": "explicit" if explicit else "inferred" if inferred else "general",
        "crop_metadata_basis": "question" if explicit else "answer" if inferred else "none",
        "crop_metadata_inferred": True,
        "crop_metadata_method": "crop_alias_dictionary_v1",
    }
