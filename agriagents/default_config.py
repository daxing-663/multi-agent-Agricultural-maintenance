"""全局配置：唯一的配置事实来源。

仿 TradingAgents 的 ``default_config.py``，承担三件事：

1. 所有可调项集中于此，不在代码里散落常量；
2. ``AGRIAGENTS_*`` 环境变量可覆盖任意一项，换模型 / 换数据源 / 改轮次不用动代码；
3. 环境变量按默认值的类型做强制转换（见 ``_coerce``），写错就启动即报错，
   绝不静默兜底——一次无人值守的巡检不该因为拼错的布尔值而悄悄换了策略。
"""

import os

# 先加载 .env，再读取任何环境变量：否则 .env 里的配置对下面的默认值不生效。
# 密钥只从这里来，不写进代码——代码会进版本库，密钥不能。
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - python-dotenv 是声明过的依赖
    pass

_AGRIAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".agriagents")

# 环境变量 → 配置键 的单向映射表。要新增可被环境覆盖的配置项，只加一行即可。
_ENV_OVERRIDES = {
    "AGRIAGENTS_LLM_PROVIDER":              "llm_provider",
    "AGRIAGENTS_DEEP_THINK_LLM":            "deep_think_llm",
    "AGRIAGENTS_QUICK_THINK_LLM":           "quick_think_llm",
    "AGRIAGENTS_LLM_BACKEND_URL":           "backend_url",
    "AGRIAGENTS_OUTPUT_LANGUAGE":           "output_language",
    "AGRIAGENTS_MAX_PERCEPTION_ROUNDS":     "max_perception_rounds",
    "AGRIAGENTS_MAX_DIAGNOSIS_ROUNDS":      "max_diagnosis_rounds",
    "AGRIAGENTS_MAX_COORDINATION_ROUNDS":   "max_coordination_rounds",
    "AGRIAGENTS_MAX_SAFETY_ROUNDS":         "max_safety_rounds",
    "AGRIAGENTS_MAX_ROLLBACK_ROUNDS":       "max_rollback_rounds",
    "AGRIAGENTS_REQUIRE_HUMAN_APPROVAL":    "require_human_approval",
    "AGRIAGENTS_DRY_RUN":                   "dry_run",
    "AGRIAGENTS_CHECKPOINT_ENABLED":        "checkpoint_enabled",
    "AGRIAGENTS_TEMPERATURE":               "temperature",
    # DeepSeek 专属：结构化输出那次调用是否关闭 thinking。
    # 关掉是实测结论（thinking 模式与强制 tool_choice 互斥），除非你要改用
    # json_mode + 自行归一化枚举值，否则不要动这一项。
    "AGRIAGENTS_DEEPSEEK_QUIET_STRUCTURED": "deepseek_quiet_structured",
    "AGRIAGENTS_DEEPSEEK_THINKING":         "deepseek_thinking",
    # RAG 本地知识库
    "AGRIAGENTS_RAG_INDEX_DIR":             "rag_index_dir",
    "AGRIAGENTS_RAG_EMBEDDING_BACKEND":     "rag_embedding_backend",
    "AGRIAGENTS_RAG_EMBEDDING_MODEL":       "rag_embedding_model",
    "AGRIAGENTS_RAG_HF_ENDPOINT":           "rag_hf_endpoint",
    "AGRIAGENTS_RAG_TOP_K":                 "rag_top_k",
    "AGRIAGENTS_RAG_EMBEDDING_THREADS":     "rag_embedding_threads",
}

_BOOL_TRUE = ("true", "1", "yes", "on")
_BOOL_FALSE = ("false", "0", "no", "off")


def _coerce(value: str, reference):
    """把环境变量字符串转换成默认值所属的类型。"""
    if isinstance(reference, bool):
        normalized = value.strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(_BOOL_TRUE + _BOOL_FALSE)}), got {value!r}"
        )
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """就地应用 ``AGRIAGENTS_*`` 环境变量。"""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        try:
            config[key] = _coerce(raw, config.get(key))
        except ValueError as exc:
            raise ValueError(f"Invalid value for {env_var}: {exc}") from exc
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    # ---- 存储位置 ----
    "results_dir": os.getenv("AGRIAGENTS_RESULTS_DIR") or os.path.join(_AGRIAGENTS_HOME, "logs"),
    "data_cache_dir": os.getenv("AGRIAGENTS_CACHE_DIR") or os.path.join(_AGRIAGENTS_HOME, "cache"),
    "memory_log_path": os.getenv("AGRIAGENTS_MEMORY_LOG_PATH")
        or os.path.join(_AGRIAGENTS_HOME, "memory", "maintenance_log.md"),
    "memory_log_max_entries": None,   # 已回填条目的上限；None 表示不轮转

    # ---- LLM 设置（双模型分级，与 TradingAgents 一致）----
    # 可选供应商：deepseek（默认） / openai / openai_compatible / stub（离线占位）
    # 两个模型都是同一个「AI」，只是分工不同：
    #   deep  用于协调决策、工单规划、回滚预案这类高价值判断
    #   quick 用于感知、诊断、执行、初筛、安检这类高频调用
    # 想让四个 Agent 完全用同一个模型，把两个键设成同一个模型名即可。
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",
    "quick_think_llm": "deepseek-flash",
    "backend_url": None,               # None 表示各供应商用自家默认端点
    "temperature": None,
    "llm_max_retries": None,
    "max_tokens": None,
    # DeepSeek 的 thinking（思维链）模式开关。默认关闭，原因是硬约束而非偏好：
    # thinking 模式要求把上一轮的 reasoning_content 原样回传，而 langchain-openai
    # 不解析也不保留该字段，工具型 Agent（感知/诊断/执行）会直接报 400。
    # 详见 agriagents/llm.py 的模块文档。
    "deepseek_thinking": False,
    # 结构化输出那次调用是否强制关闭 thinking（与强制 tool_choice 互斥）
    "deepseek_quiet_structured": True,

    # ---- 输出与轮次预算（每个预算都是防死循环的硬闸门）----
    "output_language": "Chinese",   # 报告与决策单语言
    "max_perception_rounds": 1,     # 感知 Agent 补充采集的次数上限
    "max_diagnosis_rounds": 1,      # 诊断 Agent 补充诊断的次数上限
    "max_coordination_rounds": 3,   # 协调决策中枢重新规划的轮次上限
    "max_safety_rounds": 2,         # 安全检查打回重做工单的次数上限
    "max_rollback_rounds": 1,       # 执行失败后回滚重试的上限
    "max_recur_limit": 100,         # LangGraph 全局递归上限，最后的保险丝

    # ---- 执行安全（农业与交易最大的不同：执行会动物理世界）----
    # 默认必须人工审批；只有显式关闭才会自动放行。
    "require_human_approval": True,
    # 干跑：执行 Agent 只记录指令、不真正下发到设备。接入真实设备前保持 True。
    "dry_run": True,
    # 无异常时直接关单，不进入诊断与工单流程（省算力，也避免过度处置）。
    "auto_close_when_no_anomaly": True,

    # ---- 断点续跑：崩溃/中断后可从最后一个成功节点恢复 ----
    "checkpoint_enabled": False,

    # ---- RAG 本地知识库（感知/诊断 Agent 的查证来源）----
    # 知识库由 tools/build_rag_index.py 离线构建，运行期只读加载，不联网。
    # 数据来源：CropDP-KG（中文图谱）+ PlantInquiryVQA（英文卡片）
    #           + Agriculture-QA / 中文农林牧渔问答（文本块）+ AGROVOC（术语对齐）
    "rag_index_dir": os.getenv("AGRIAGENTS_RAG_INDEX_DIR")
        or os.path.join(_AGRIAGENTS_HOME, "rag"),
    "rag_data_dir": os.getenv("AGRIAGENTS_RAG_DATA_DIR")
        or os.path.join(_AGRIAGENTS_HOME, "rag", "raw"),   # 原始语料，可增量重建
    # 本地审核文档/现场档案 JSONL，多个路径用 os.pathsep 分隔（Windows 为分号）。
    "rag_local_paths": os.getenv("AGRIAGENTS_RAG_LOCAL_PATHS", ""),
    # 嵌入后端：auto（有 fastembed 就用神经嵌入，否则哈希兜底）
    #           / fastembed / hashing / none（纯 BM25 关键词检索）
    "rag_embedding_backend": "auto",
    "rag_embedding_threads": 2,           # 限制 ONNX 并发，避免多核环境线程争用
    # 多语言模型，中英混检。语料若确定只有中文，可换 BAAI/bge-small-zh-v1.5（92MB）。
    "rag_embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "rag_model_cache_dir": os.path.join(_AGRIAGENTS_HOME, "models"),
    # 国内直连 huggingface.co 常超时，默认走镜像；换回官方改这一项即可。
    "rag_hf_endpoint": "https://hf-mirror.com",
    "rag_top_k": 5,                 # 返回给 Agent 的知识块条数
    "rag_candidate_k": 30,          # 融合前的候选数，决定召回上限
    # 中文问答集有 92.7 万条，按设施作物相关性过滤后只取前若干条；
    # 扫描上限 None 表示全量扫描（走 parquet 直连时几十秒）。
    "rag_ingest_limit_qa_zh": 6000,
    "rag_ingest_scan_zh": None,

    # ---- 数据源配置：类别级默认 + 工具级覆盖 ----
    # 值为确切的供应商链，工具按顺序回退，不做隐式兜底。
    "data_vendors": {
        # 默认接内置「合成演示源」把全流程跑通（返回值自带 [synthetic_demo] 标识，
        # 不冒充实测数据）；接入真实来源后把该项换成自家供应商即可，例如
        #   "sensor_data": "real_sensors,synthetic_demo"
        "sensor_data": "synthetic_demo,stub_sensors",      # 土壤墒情 / 气象站 / 水质等传感器
        "weather_data": "synthetic_demo,stub_weather",     # 天气预报与历史实况
        "imagery_data": "synthetic_demo,stub_imagery",     # 摄像头 / 无人机 / 卫星遥感
        "device_telemetry": "synthetic_demo,stub_sensors", # 设备运行状态与故障码
                # 先查本地 RAG 知识库，未构建索引时自动回退到占位实现
        "agronomy_knowledge": "local_rag,stub_knowledge",
        "resource_data": "synthetic_demo,stub_ops",        # 人力 / 机具 / 农资库存
        "safety_policy": "synthetic_demo,stub_ops",        # 安全与合规规则库（带版本号）
        "actuator_gateway": "synthetic_demo,stub_actuators",  # 执行机构网关（会改变物理世界）
    },
    "tool_vendors": {
        # 例：把单个工具固定到某个供应商，优先级高于类别默认
        # "get_remote_sensing_index": "sentinel_hub",
    },
})
