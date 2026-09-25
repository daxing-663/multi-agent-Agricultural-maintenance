"""技能包（Skill）加载层。

每个 Agent 除角色提示词之外，还配一份**作业手册**（``SKILL.md``）：
角色提示词回答"你是谁、边界在哪"，技能包回答"具体怎么做"——
取数配方、对照口径、请求模板、升级条件、常见坑。

为什么单独放文件而不是写进提示词：

- 提示词保持精简（角色与硬约束），手册可以随时改，不需要动代码；
- 手册是给人看的运维文档，与"喂给模型的文本"是同一份，避免两处描述漂移；
- 加载器按文件 mtime 缓存，改完立刻生效，不必重启进程。

约定：``SKILL.md`` 以 YAML 风格的前置元信息开头（``---`` 包裹），
正文标题自带版本号；文件缺失直接报错——少一份手册意味着某个 Agent
在没有作业规程的情况下上岗，属于打包缺陷，必须显式暴露。
"""

from __future__ import annotations

from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent
SKILL_NAMES: tuple[str, ...] = ("perception", "diagnosis", "coordinator", "execution")

_cache: dict[str, tuple[float, int, dict, str]] = {}


def skill_path(name: str) -> Path:
    """技能包文件路径；名称非法时抛 ValueError。"""
    key = str(name).strip().lower()
    if key not in SKILL_NAMES:
        raise ValueError(f"未知技能包 {name!r}；可用：{', '.join(SKILL_NAMES)}")
    return SKILL_ROOT / key / "SKILL.md"


def _parse(text: str) -> tuple[dict, str]:
    """切分前置元信息与正文；没有元信息则整篇当正文。"""
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text.strip()
    _, _, rest = text.partition("---")
    meta_text, sep, body = rest.partition("\n---")
    if not sep:
        return {}, text.strip()
    meta: dict = {}
    for line in meta_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        field, _, value = stripped.partition(":")
        meta[field.strip()] = value.strip()
    return meta, body.strip()


def load_skill(name: str) -> str:
    """返回技能包正文（去掉前置元信息），按文件 mtime + 大小缓存。"""
    path = skill_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"缺少技能包文件 {path}；每个 Agent 都必须带自己的 SKILL.md 上岗。"
        )
    stat = path.stat()
    cached = _cache.get(name)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[3]
    meta, body = _parse(path.read_text(encoding="utf-8"))
    _cache[name] = (stat.st_mtime, stat.st_size, meta, body)
    return body


def skill_metadata(name: str) -> dict:
    """返回技能包的前置元信息（name / description / version / applies_to）。"""
    load_skill(name)
    return dict(_cache[name][2])


def skill_digest() -> str:
    """全部技能包的摘要，参与 checkpoint 签名：手册改了就不该复用旧断点。"""
    parts = []
    for name in SKILL_NAMES:
        path = skill_path(name)
        stat = path.stat() if path.exists() else None
        parts.append(f"{name}:{int(stat.st_mtime)}:{stat.st_size}" if stat else f"{name}:missing")
    return "|".join(parts)

