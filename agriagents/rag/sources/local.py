"""导入用户自己的 JSONL 知识文档；不把模板或模拟值伪装成现场资料。

每行包含 ``id``（可选）、``kind``、``title``、``text``、``meta``。
meta.source_ref 必须注明原文出处或内部文档标识。精确匹配字段：

* soil_reference: site_id 或 site_ids。
* equipment_manual: model/model_id/device_id/device_ids/device_aliases 至少一个，
  并提供 fault_code 或 fault_codes。
* treatment: disease 与 crop。
* agronomy: crop 或 topic。

可以通过 --local-path、rag_local_paths 配置或 AGRIAGENTS_RAG_LOCAL_PATHS
指定多个 JSONL 文件/目录。目录递归读取 *.jsonl；不会创建现场资料。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agriagents.rag.index import Doc
from agriagents.rag.sources.base import IngestContext, SourceResult

NAME = "local"
DESCRIPTION = "自有 JSONL 知识库（精确地块/型号/故障/病害绑定，逐条校验）"
HOMEPAGE = "用户指定的本地 JSONL 文件或目录"
LICENSE = "由导入者确认原始资料的使用权限"
KINDS = frozenset({"soil_reference", "equipment_manual", "agronomy", "treatment"})


def configured_paths(config: dict) -> list[str]:
    paths = config.get("rag_local_paths") or []
    if isinstance(paths, (str, os.PathLike)):
        paths = [str(paths)]
    env_paths = [p for p in os.getenv("AGRIAGENTS_RAG_LOCAL_PATHS", "").split(os.pathsep) if p.strip()]
    return list(dict.fromkeys([str(p) for p in paths] + env_paths))


def _identifiers(meta: dict, singular: tuple[str, ...], plural: tuple[str, ...] = ()) -> list[str]:
    values = []
    for key in singular:
        if key not in meta:
            continue
        value = meta[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"meta.{key} 必须为非空字符串")
        meta[key] = value.strip()
        values.append(meta[key])
    for key in plural:
        if key not in meta:
            continue
        value = meta[key]
        if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
            raise ValueError(f"meta.{key} 必须为非空字符串列表")
        meta[key] = [v.strip() for v in value]
        values.extend(meta[key])
    for value in values:
        if any(ch in value for ch in "*?[]") or value.casefold() in {"all", "any", "generic", "全部", "通用"}:
            raise ValueError(f"精确标识不得使用通配符或通用值：{value!r}")
    return values


def row_to_doc(row: dict, *, path: Path, line: int) -> Doc:
    if not isinstance(row, dict):
        raise ValueError("每行必须是 JSON 对象")
    kind = row.get("kind")
    if kind not in KINDS:
        raise ValueError(f"不支持 kind={kind!r}；可选 {', '.join(sorted(KINDS))}")
    for key in ("title", "text"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"{key} 必须为非空字符串")
    if not isinstance(row.get("meta"), dict):
        raise ValueError("meta 必须为对象")
    meta = dict(row["meta"])
    if not isinstance(meta.get("source_ref"), str) or not meta["source_ref"].strip():
        raise ValueError("meta.source_ref 必须注明原始文档出处或标识")
    if kind == "soil_reference":
        if not _identifiers(meta, ("site_id",), ("site_ids",)):
            raise ValueError("soil_reference 必须提供 meta.site_id 或 site_ids")
    elif kind == "equipment_manual":
        if not _identifiers(meta, ("model", "model_id", "device_id"), ("device_ids", "device_aliases")):
            raise ValueError("equipment_manual 必须明确型号或设备标识，device_type 不能替代型号")
        if not _identifiers(meta, ("fault_code",), ("fault_codes",)):
            raise ValueError("equipment_manual 必须提供 meta.fault_code 或 fault_codes")
    elif kind == "treatment":
        if not _identifiers(meta, ("disease",)) or not _identifiers(meta, ("crop",)):
            raise ValueError("treatment 必须提供 meta.disease 和 crop")
    elif not _identifiers(meta, ("crop", "topic")):
        raise ValueError("agronomy 必须提供 meta.crop 或 topic")
    for flag in ("is_demo", "demo", "is_template", "project_human_verified"):
        if flag in meta and not isinstance(meta[flag], bool):
            raise ValueError(f"meta.{flag} 必须为布尔值")
    if meta.get("is_demo") or meta.get("demo"):
        meta["evidence_status"] = "demo"
    else:
        meta.setdefault("evidence_status", "user_provided")
    meta.setdefault("project_human_verified", False)
    meta.setdefault("citation", meta["source_ref"])
    meta["import_file"] = str(path.resolve())
    meta["import_line"] = line
    raw_id = row.get("id") or row.get("doc_id")
    if raw_id is None:
        raw_id = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("id 必须为非空字符串")
    lang = row.get("lang", "zh")
    if not isinstance(lang, str) or not lang.strip():
        raise ValueError("lang 必须为非空字符串")
    return Doc(
        doc_id=raw_id if raw_id.startswith("local:") else f"local:{raw_id}",
        source=NAME, kind=kind, title=row["title"].strip(), text=row["text"].strip(),
        lang=lang, meta=meta,
    )


def build(ctx: IngestContext) -> SourceResult:
    paths = configured_paths(ctx.config)
    if not paths:
        raise ValueError("local 未配置资料：请提供 --local-path 文件/目录")
    files: set[Path] = set()
    for value in paths:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            matches = {p for p in path.rglob("*.jsonl") if p.is_file()}
            if not matches:
                raise ValueError(f"目录没有 JSONL 文件：{path}")
            files.update(matches)
        elif path.is_file() and path.suffix.lower() == ".jsonl":
            files.add(path)
        else:
            raise ValueError(f"资料路径不存在或不是 JSONL：{path}")
    docs = []
    seen = set()
    counts = {}
    for path in sorted(files):
        count = 0
        with path.open(encoding="utf-8-sig") as handle:
            for line_no, text in enumerate(handle, 1):
                if not text.strip():
                    continue
                try:
                    doc = row_to_doc(json.loads(text), path=path, line=line_no)
                    if doc.doc_id in seen:
                        raise ValueError(f"重复 id：{doc.doc_id}")
                except (TypeError, ValueError, KeyError) as exc:
                    raise ValueError(f"{path}:{line_no}: {exc}") from exc
                seen.add(doc.doc_id)
                docs.append(doc)
                count += 1
        counts[str(path)] = count
    if not docs:
        raise ValueError("local 文件均为空，未导入任何知识")
    # 即使调试设 limit，也先校验全部行，不能因截断而隐藏错误记录。
    if ctx.limit is not None:
        docs = docs[:ctx.limit]
    return SourceResult(docs=docs, details={"files": counts}, notes=[f"校验 {sum(counts.values())} 条，入库 {len(docs)} 条"])
