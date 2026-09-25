"""处置等级词表与确定性解析器。

五档处置等级是全系统的共同词汇，被以下位置共用：
协调决策中枢（方案优先级）、安全检查、执行 Agent（是否放行）、
决策台账（条目标签）、报告渲染。集中在此避免各处定义漂移。

``extract_disposition`` 解析不出结果时返回 ``None``，调用方一律转成 ``REVIEW``：
一份没人读得懂的决策单不等于「无需处置」，把 REVIEW 记成 NoAction
会在下一轮复盘时被当成一个从未做出的结论引用。

词表另外收录「REVIEW / 待人工复核」这类**复核标记**：它是标签行里的合法取值，
但默认不参与全文兜底扫描——正文里出现一个英文 review（例如字段名）不足以
推翻已经写明的等级，而强制收口模板里内嵌的原话（例如「应尽快处置」）也不该
盖过标签行里明写的 REVIEW。
"""

from __future__ import annotations

import re

# 规范化的五档等级（从最紧急到最不紧急）
DISPOSITIONS_5_TIER: tuple[str, ...] = (
    "Immediate", "Soon", "Scheduled", "Monitor", "NoAction",
)

# 中文标签：仅用于展示，代码内部一律用上面的规范值
DISPOSITION_LABELS: dict[str, str] = {
    "Immediate": "立即处置",
    "Soon": "尽快处置",
    "Scheduled": "计划处置",
    "Monitor": "观察",
    "NoAction": "无需处置",
}

# 无法解析出的信号。它不是一档处置结论，而是「需要人看 / 需要重跑」的标记。
RATING_REVIEW = "REVIEW"

# 复核标记的写法。与等级别名分开存放：「review」在正文里太常见（字段名、
# 英文摘要），因此只允许在两个受控位置出现——标签行的取值，或下面的中文短语。
_REVIEW_ALIASES: tuple[str, ...] = ("待人工复核", "需人工复核", "review")

# 全文兜底扫描时认的复核标记：必须是明确到不会误伤的中文短语。
_REVIEW_MARKERS: tuple[str, ...] = ("待人工复核", "需人工复核")

# 别名表：注意"长词在前"，否则「无需」会先于「无需处置」被匹配上
_ALIASES: dict[str, str] = {
    "immediate": "Immediate", "urgent": "Immediate", "立即处置": "Immediate", "紧急": "Immediate",
    "soon": "Soon", "尽快处置": "Soon", "尽快": "Soon",
    "scheduled": "Scheduled", "计划处置": "Scheduled", "计划": "Scheduled",
    "monitor": "Monitor", "observe": "Monitor", "观察": "Monitor",
    "noaction": "NoAction", "no action": "NoAction",
    "无需处置": "NoAction", "无需": "NoAction", "none": "NoAction",
}

# 分隔符：半角/全角冒号、各类破折号
_SEP = r"[:\-\u2010-\u2015\uff1a]"

# 匹配「处置等级: X」/「**处置等级：** X」/「Disposition - **X**」，容错 markdown 加粗
_LABEL_RE = re.compile(
    r"(处置等级|处置预判|处置建议|disposition|rating)\s*[*\s]*" + _SEP + r"[\s*]*([^\s*|，,。;；]+)",
    re.IGNORECASE,
)

# 一行在展示等级表而不是下结论（「处置等级说明：立即处置 / 尽快处置 ...」）
_SCALE_RE = re.compile(r"(处置等级|disposition)\s*(说明|对照|表|scale|options|legend)", re.IGNORECASE)


def normalize_disposition(word: str) -> str | None:
    """把模型写出的任意写法归一到规范等级，认不出返回 None。"""
    if not word:
        return None
    key = word.strip().strip("*_`\"'。，,.；;：: ").lower()
    for alias in _REVIEW_ALIASES:
        if alias in key:
            return RATING_REVIEW
    if key in _ALIASES:
        return _ALIASES[key]
    for alias, canonical in _ALIASES.items():
        if alias and alias in key:
            return canonical
    return None


def extract_disposition(text: str) -> str | None:
    """从决策单正文中解析处置等级，解析不出返回 None。"""
    if not text:
        return None
    for line in text.splitlines():
        if _SCALE_RE.search(line):
            continue
        match = _LABEL_RE.search(line)
        if match:
            found = normalize_disposition(match.group(2))
            if found:
                return found
    # 退路一：全文明写的复核标记比散落的等级词更可信，优先判定
    for marker in _REVIEW_MARKERS:
        if marker in text:
            return RATING_REVIEW
    # 退路二：全文任何位置出现规范词
    for alias, canonical in _ALIASES.items():
        if re.search(re.escape(alias), text, re.IGNORECASE):
            return canonical
    return None


def parse_disposition(text: str) -> str:
    """解析等级，失败时返回 ``REVIEW``（不可执行的占位等级）。"""
    return extract_disposition(text) or RATING_REVIEW


def is_review(value: str | None) -> bool:
    """该值是否为「待人工复核」标记。"""
    return value == RATING_REVIEW


def label(disposition: str) -> str:
    """规范等级的中文展示名。"""
    return DISPOSITION_LABELS.get(disposition, disposition)
