# 知识库与检索验收

本项目按设施番茄、黄瓜场景提供五类领域检索。公开语料、演示资料和自有文档共享索引，保留独立的 `source`、`kind`、`doc_id` 和出处，支持按来源限定查询。

## 语料覆盖

| source | 内容 | 本次构建文档数 | 上游 |
|---|---|---:|---|
| seed | 演示病害、土壤、设备、管理与规则 | 22 | 随项目分发，全部标为演示 |
| cropdp | 中文病虫害图谱，含中英实体与症状 | 2,578 | [CropDP-KG Dataset](https://github.com/dadadaray/CropDP-KG/tree/Dataset) |
| plantinquiry | 病害卡片，按症状、管理、鉴别、严重度分块 | 1,946 | [PlantInquiryVQA](https://github.com/syed-nazmus-sakib/PlantInquiryVQA) |
| qa_en | 英文农艺问答及带推导的数值题，过滤去重后 | 40,583 | [Agriculture-QA](https://huggingface.co/datasets/talhakk/agriculture-qa)、[KisanVaani](https://huggingface.co/datasets/KisanVaani/agriculture-qa-english-only)、[Verified Agronomy](https://huggingface.co/datasets/manifesta/verified-agronomy-17k) |
| qa_zh | 按作物及植保相关性筛选的中文问答 | 5,993 | [中文农林牧渔问答](https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery) |
| curated | 温室黄瓜定植及番茄/黄瓜珍珠岩水肥管理摘要 | 2 | [UAF Extension](https://www.uaf.edu/ces/publications/database/gardening/growing-cucumbers-greenhouses.php)、[UF/IFAS HS169](https://ask.ifas.ufl.edu/publication/HS169) |
| local | 用户导入的 JSONL 档案、手册、农艺与方案 | 按导入文件确定 | `meta.source_ref` 必填 |

本次六源共 **51,124 条文档**、2,550 个图谱实体；文档数与原始问答行数、疾病数不是同一指标。中文源默认接收前 6,000 条符合相关性筛选的记录，去重后入库 5,993 条，并非把 927,864 行全部塞进索引。问答入库前排除纯烹饪、食物营养与人体保健条目，作物标签优先从问题提取；这是规则筛选，仍需核对证据正文。`curated` 是对公开资料的简短释义，标有地区和栽培介质适用范围，未经过本项目专家审核，不移植国外定量处方。数值题中的上游 `verified_by=closed-form` 表示程序/公式验证，不代表本项目农艺专家人工审核。

## Agent 如何选库

诊断 Agent 实际绑定七个只读知识工具；工具节点从同一份 `TOOLS` 装配，避免提示词和可调用工具脱节。

| 任务 | 工具 | 匹配约束 |
|---|---|---|
| 查看已加载的库 | `list_knowledge_bases()` | 返回实际数量、类型、来源及演示数量 |
| 指定语料源交叉查证 | `search_knowledge_base(query, source, kind, top_k, crop, dataset)` | source/kind/dataset 精确限制；默认只返回查询相关的短命中片段；crop 通过元数据预过滤 |
| 按需读取知识全文 | `get_knowledge_document(doc_id)` | 仅在短片段不足以鉴别时，按完整 doc_id 精确读取一篇全文；不做相似文档回退 |
| 病虫害候选 | `query_pest_disease_library(crop, symptom)` | 作物预过滤；症状证据；未知症状不能只靠作物名命中 |
| 农艺问题 | `query_agronomy_knowledge(question)` | 先匹配作物、管理主题与生育期；通用资料标明适用性未验证；病害防治走专用方案匹配 |
| 病害处置 | `get_treatment_options(diagnosis, crop)` | 使用候选的明确 disease 名称；病害、宿主共同匹配 |
| 土壤本底 | `query_soil_reference(site_id)` | 精确地块编号；通用模板不能当作该地块检测结果 |
| 设备故障 | `query_equipment_manual(device_id, fault_code)` | 设备/型号/明确别名与故障码同时匹配 |

通用搜索返回的是查证材料，`applicability_validated=false`。土壤、设备、处置结果还必须经过相应专用工具验证适用对象，不能因为 `found=true` 就用于执行。RRF/BM25/向量分数只是排序依据，不是诊断概率。无匹配时返回明确未命中或不可用，不拼凑相邻对象的数据。

每次引用保留 `doc_id` 和出处。同正文跨数据集去重时，`doc_id` 保持索引主记录身份，`dataset_provenance` 与 `matched_dataset` 另给原始数据集、原始 ID 和出处；指定副数据集仍能检索。`seed`、`is_demo` 或 `demo` 记录显示演示身份。公开语料没有的药剂剂量、登记适用范围、安全间隔期和最大次数不会由代码或模型补造。

诊断 Agent 在输出报告前核对引用是否来自本轮成功的知识工具。发现缩写、拼接、未返回的 ID 或缺少引用时，最多要求模型重写一次；仍失败则输出证据不足并将置信度设为低，不保留未通过核验的报告。`diagnosis_state.citation_check` 记录结果。此检查验证引用身份，不代表每项农艺结论都已被原文支持，仍需核对具体语义与现场条件。

## 番茄、黄瓜演示档案

| 场景 | 地块 | 演示设备 | 可查询故障 |
|---|---|---|---|
| 设施番茄 | `FIELD-07` | `DEMO-VALVE-V1` / `VALVE-11` | `E04` |
| 设施黄瓜 | `GH-09` | `DEMO-FAN-V1` / `FAN-03` | `OFFLINE` |

土壤示例带 pH、EC、有机质、单位、模拟检测方法及记录时间；设备示例带型号、别名、故障与停止条件。全部为虚构测试资料。真实报告与厂商型号手册可通过 local 追加导入，与演示记录并存；专用查询优先非演示资料，仍保留来源身份。若需完全去掉演示资料，构建时显式选择不含 seed 的来源。

## 构建与验收

本次结果：182 项程序回归、132 项关键词检索、133 项混合检索全部通过；真实 Agent 的 7 种知识工具、17 次调用和最终完整引用检查通过。10 个固定自然问句全部在前 3 条命中、9 个排第 1。详见 [验收记录](../reports/kb/README.md)。

当前机器已在默认目录完成六源混合索引构建，可直接运行项目；更新语料或迁移到新机器时再构建。

在项目目录使用现有虚拟环境：

```powershell
# 需要安装时：核心RAG依赖和本地向量模型依赖
.\.venv\Scripts\python.exe -m pip install -e ".[dev,rag,rag-embeddings]"

# 默认六源，使用已下载的缓存；缺失部分才下载
.\.venv\Scripts\python.exe tools/build_rag_index.py --all --probe

# 无向量模型也可完整构建关键词检索
.\.venv\Scripts\python.exe tools/build_rag_index.py --all --no-embed --probe

# 快速演示库：明确指定，避免误以为已经加载全部知识
.\.venv\Scripts\python.exe tools/build_rag_index.py --sources seed --index-dir .\results\seed-kb --no-embed

.\.venv\Scripts\python.exe tools/build_rag_index.py --status
.\.venv\Scripts\python.exe tools/verify_kb_access.py --output-dir reports/kb/bm25
.\.venv\Scripts\python.exe tools/verify_kb_access.py --dense --output-dir reports/kb/dense
.\.venv\Scripts\python.exe -m pytest tests -q

# 显式使用已配置模型API，只运行诊断/知识工具，没有设备执行节点
.\.venv\Scripts\python.exe tools/verify_agent_knowledge.py --live
```

默认索引在 `%USERPROFILE%\.agriagents\rag`，可用 `AGRIAGENTS_RAG_INDEX_DIR` 修改。向量构建按分词长度分批并写回原文档顺序，减少填充计算；ONNX 默认使用 2 个线程，避免本地线程争用；可用 `AGRIAGENTS_RAG_EMBEDDING_THREADS` 调整。

构建先暂存，校验文档、ID、来源、图谱、术语、向量及文件哈希后再发布。任意必需语料源失败默认保留旧索引；`--allow-partial` 是显式降级，仍返回非零退出码。旧库保留为同级 `.rag.backup-*`。发布采用两次目录重命名，期间有短暂不可见窗口，请在没有新查询时重建。长驻进程随后会根据文件指纹刷新缓存。

离线验收包含 10 个有正文相关性标签的自然问句、来源覆盖、按来源/类型抽样的标题回查、元数据过滤、番茄/黄瓜与中英文案例，以及错作物、未知地块、设备、故障、症状的负例。JSON 保存查询、期望、实际结果与语料哈希；Markdown 方便阅读。真实 Agent 验收还要求七种工具全部调用、两作物/两地块/两设备参数覆盖、三个英文数据集分别查证，每次返回的文档 ID 必须确实存在于同一语料索引中，未命中或占位结果不能算通过。标题回查和固定回归测试不代表开放问题或现场诊断的准确率。

## 导入自己的档案与手册

一个 JSON 对象占一行。支持 `soil_reference`、`equipment_manual`、`treatment`、`agronomy`；示例字段如下（仍为演示，导入实测时替换内容与来源）：

```json
{"id":"demo-soil","kind":"soil_reference","title":"TEST-01土壤演示","text":"演示pH为6.5，不是实测。","meta":{"site_id":"TEST-01","crop":"番茄","source_ref":"本地演示fixture","is_demo":true}}
{"id":"demo-device","kind":"equipment_manual","title":"DEMO-PUMP-V1 E01","text":"演示故障码E01：无状态反馈，转人工核对。","meta":{"model":"DEMO-PUMP-V1","device_ids":["PUMP-01"],"fault_code":"E01","source_ref":"本地演示fixture","is_demo":true}}
```

```powershell
.\.venv\Scripts\python.exe tools/build_rag_index.py --all --local-path .\my_knowledge.jsonl --probe
```

`--local-path` 可以重复使用，也接受目录；只处理 JSONL。还可配置 `AGRIAGENTS_RAG_LOCAL_PATHS`（Windows 多路径用分号分隔）。导入文件缺少对象标识、故障码或出处会报出文件与行号，构建不会用无效记录覆盖旧库。治疗方案必须同时提供明确的 `meta.disease` 和 `meta.crop`；不能只用药剂名或泛化病原名建立适用关系。
