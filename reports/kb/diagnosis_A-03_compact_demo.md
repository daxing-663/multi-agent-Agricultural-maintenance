# A-03 诊断 Agent 短证据模式复跑结果

## 运行摘要

- Provider / model: `deepseek / deepseek-flash`
- 工具调用次数: 17
- 工具输出总字符数: 48174
- 按需全文调用次数: 2
- 引用校验: `passed`
- 严重度 / 置信度: `medium / medium`

## 异常初筛

**异常等级:** medium
**是否需跟进:** 是
**异常通道:** sensor, vision

初筛判断"有事，需进入诊断"。sensor 通道出现多点偏离且存在互相矛盾：S-001 墒情 0.12（低于下限 0.20）、S-004 空气湿度 86%（超上限 85）为明确越限；S-002 读数 0.34 虽在阈值内但与 S-001 差值 0.22 超过一致性阈值 0.15，且超 12 小时无更新，数据可疑。vision 通道 IMG-20260926-1001 检出 12 处褐色同心轮纹病斑、边缘黄化、覆盖叶面积约 18%，属结果期需处置的明确异常（无历史基线，仅能确认"存在病斑"，不能确认发展速度）。设备 PLC-001/V-003/P-003 状态为 online/closed/idle、无故障码，仅电量 18% 低于 20% 阈值，属需关注的辅助偏离。综合为明确异常但不构成紧急（当前无故障码、无紧急告警），故定为 medium，建议进入诊断。

**证据:** 1) S-001：0.12 volumetric @09-26 09:58，阈值下限 0.20，7天均值 0.28 —— 越限偏低。2) S-002：0.34 volumetric @09-25 22:00，阈值 [0.20,0.45] 内，但 7天均值 0.28，且超 12 小时无更新。3) S-001 与 S-002 差值 0.22 > 一致性阈值 0.15，报告明确不能同时作为同一地块墒情依据。4) S-004：86% @09-26 09:58，阈值上限 85，7天均值 72 —— 越限偏高。5) S-003：28.0°C @09-26 09:58，阈值 [15,32]，正常。6) IMG-20260926-1001：12 处褐色圆形病斑、同心轮纹、边缘黄化、覆盖叶面积约 18% @09-26 09:45，无历史影像基线。7) 设备：PLC-001 online、V-003 closed、P-003 idle、电量 18%（阈值 20）、无故障码。8) 天气：南宁东南风 8.5m/s，未来6小时 7-9m/s，降水概率 20%。9) 遥感 A-03 返回 [stub] 未接入；水质 S-005 未接入。
**待确认:** 1) 墒情真伪未定：S-001 与 S-002 矛盾（差值 0.22），无法判定哪个通道可信，需现场复核或补采；墒情缺 7 天连续窗口，趋势无法建立。2) S-002 超 12 小时无更新，原因未知（传感器故障 / 通信中断 / 采集窗口未覆盖），未确认前 0.34 不可采信。3) 病斑成因、病害种类、发展阶段未知；影像为单次采集且无历史基线，无法判断扩散速度与是否为突发。4) 遥感 A-03 [stub] 与水质 S-005 未接入，这两个通道本轮无任何数据，不能默认正常，需补采以评估诊断完整性。5) 高风速影响未闭合：东南风 8.5m/s、未来 6 小时 7-9m/s，是否覆盖未来喷洒（P-003）作业时段、是否超出作业风速限值，报告未给出限值，未确认。6) 电量 18% 的续航预期与充电/更换安排未说明。7) 未覆盖通道：遥感指数、水质，以及报告中未提及的其他通道。

## 工具调用

### 1. `list_knowledge_bases`

参数：`{}`

状态：`success`；返回字符数：1638

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "available": true, "documents": 51124, "sources": [{"source": "cropdp", "documents": 2578, "kinds": {"kg_entity": 2578}, "datasets": ["CropDP-KG"], "demo_documents": 0}, {"source": "curated", "documents": 2, "kinds": {"agronomy": 2}, "datasets": [], "demo_documents": 0}, {"source": "plantinquiry", "documents": 1946, "kinds": {"diagnosis": 186, "disease_card": 203, "environment": 193, "management": 203, "severity": 203, "symptom": 958}, "datasets": ["PlantInquiryVQA"], "demo_documents": 0}, {"source": "qa_en", "documents": 40583, "kinds": {"qa_pair": 40583}, "datasets": ["KisanVaani/agriculture-qa-english-only", "manifesta/verified-agronomy-17k", "talhakk/agriculture-qa"], "demo_documents": 0}, {"source": "qa_zh", "documents": 5993, "kinds": {"qa_pair": 5993}, "datasets": ["Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery"], "demo_documents": 0}, {"source": "seed", "documents": 22, "kinds": {"diagnosis": 1, "disease_card": 3, "equipment_manual": 4, "management": 3, "qa_pair": 3, "safety_rule": 1, "severity": 1, "soil_reference": 3, "symptom": 3}, "datasets": [], "demo_documents": 22}], "graph": {"entities": 2550, "symptoms": 21061, "with_crops": 1197, "with_symptoms": 2027}, "tools": {"病虫害": "query_pest_disease_library(crop, symptom)", "农艺问答": "query_agronomy_knowledge(question)", "处置方案": "get_treatment_options(diagnosis, crop)", "土壤档案": "query_soil_reference(site_id)", "设备手册": "query_equipment_manual(device_id, fault_code)", "指定语料源查证": "search_knowledge_base(query, source, kind)", "按文档读取全文": "get_knowledge_document(doc_id)"}, "note": "目录仅反映已加载内容；seed/is_demo 为演示资料，不是实测证据。"}
```
</details>

### 2. `query_pest_disease_library`

参数：`{"crop": "番茄", "symptom": "叶片褐色圆形病斑、具同心轮纹、边缘黄化，结果期"}`

状态：`success`；返回字符数：12594

<details><summary>查看工具返回</summary>

```text
[local_rag] 病虫害查询：作物=番茄，症状=叶片褐色圆形病斑、具同心轮纹、边缘黄化，结果期

■ 候选病害（按证据检索排序，共 5 条）

1. 番茄早疫病（Alternaria solani）  检索排序分 0.0318  [已关联英文防治卡片]
   source=cropdp；doc_id=cropdp:番茄早疫病:0；出处=https://github.com/dadadaray/CropDP-KG/tree/Dataset
   交叉证据：source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.early_blight:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   命中的症状：同心轮纹；圆形或椭圆形暗褐色病斑；病斑椭圆形
   危害部位：叶片、叶柄、茎部、果实
   危害作物：番茄
   适宜发生条件：基肥不足；灌水多；低洼积水
   英文名/别名：Tomato early blight

2. 番茄灰斑病（Septoria lycopersici）  检索排序分 0.0296  [已关联英文防治卡片]
   source=cropdp；doc_id=cropdp:番茄灰斑病:0；出处=https://github.com/dadadaray/CropDP-KG/tree/Dataset
   交叉证据：source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.septoria_leaf_spot:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   命中的症状：边缘褐色；边缘暗色；近圆形大病斑
   危害部位：叶部、茎部、果实
   危害作物：番茄
   英文名/别名：Tomato grey spot

3. 番茄晚疫病（Phytophthora infestans）  检索排序分 0.0268  [已关联英文防治卡片]
   source=cropdp；doc_id=cropdp:番茄晚疫病:0；出处=https://github.com/dadadaray/CropDP-KG/tree/Dataset
   交叉证据：source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.late_blight:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   交叉证据：【演示/模拟数据，非现场实测，不可直接用于生产】 source=seed；doc_id=seed:tomato-late-blight:symptom-fruit；出处=agriagents/rag/seed/knowledge_seed.jsonl
   命中的症状：病斑呈绿褐色；边缘不变红；暗绿色水浸状不整形病斑
   危害部位：叶片、茎部、果实
   危害作物：番茄
   适宜发生条件：以上，空气湿度；地势低洼；排水不良
   适宜温度：24℃
   英文名/别名：Tomato late blight

4. 番茄枯萎病（Fusarium oxysporum f. sp. lycopersici）  检索排序分 0.0248  [已关联英文防治卡片]
   source=cropdp；doc_id=cropdp:番茄枯萎病:0；出处=https://github.com/dadadaray/CropDP-KG/tree/Dataset
   交叉证据：source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.fusarium_wilt:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   命中的症状：叶片开始发黄；叶片萎蔫发黄；呈褐色萎蔫
   危害作物：番茄
   适宜发生条件：土壤湿度过低
   英文名/别名：Tomato Fusarium wilt

5. 番茄芝麻斑病（Corynespora cassiicola）  检索排序分 0.0245  [已关联英文防治卡片]
   source=cropdp；doc_id=cropdp:番茄芝麻斑病:0；出处=https://github.com/dadadaray/CropDP-KG/tree/Dataset
   交叉证据：source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.target_spot:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   命中的症状：病班近圆形或椭圆形；灰褐色；病斑多条状
   危害作物：番茄
   适宜发生条件：高温高湿；多雨高温；田间潮湿；通风透光差；施肥不足
   英文名/别名：Tomato Helminthosporium leafspot

■ 检索到的补充证据（20 条）
 - source=plantinquiry；doc_id=plantinquiry:tomato.physiological_symptom.yellowing_symptom:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Yellowing Symptom：tomato / Yellowing Symptom（） 叶片症状： - Uniform yellowing of older, lower leaves, while new growth remains green (classic nitrogen deficiency). - Yellowing between the veins (interveinal chlorosis) on older, lower leaves, sometimes with a 'Christmas tree' pattern (magnesium deficiency). - Interveinal chlorosis primarily …
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_viral.yellow_leaf_curl_virus:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·yellow leaf curl virus：tomato / yellow leaf curl virus（Tomato yellow leaf curl virus） 叶片症状： - Upward cupping or curling of leaf margins, resembling a cup shape. - Interveinal yellowing (chlorosis), starting on younger leaves. - Leaf margins turn a distinct bright yellow while the rest of the leaf may remain green. - Affected leaves become s…
 - source=plantinquiry；doc_id=plantinquiry:tomato.healthy.healthy:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·healthy：tomato / healthy（） 叶片症状： - Uniformly green color, consistent with the cultivar. - Leaves are fully expanded and turgid. - No spots, lesions, discoloration, or necrosis. - No yellowing (chlorosis) or browning. - No curling, puckering, or distortion of leaf shape. - No visible stippling, webbing, or insect trails.
 - source=plantinquiry；doc_id=plantinquiry:tomato.physiological_symptom.yellowing_symptom:card；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Yellowing Symptom：tomato / Yellowing Symptom（） 别名：chlorosis；tomato chlorosis；nutrient deficiency yellowing 病害类型：physiological_symptom
 - source=plantinquiry；doc_id=plantinquiry:tomato.healthy.healthy:lookalikes；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·healthy：tomato / healthy（） 鉴别诊断要点： 易混淆：Early Blight - Healthy leaves lack the characteristic dark, circular lesions with a 'target' or 'bullseye' pattern. - Healthy plants do not show yellowing halos around spots. - Healthy lower leaves remain green and attached, unlike the premature yellowing and dropping seen in early bligh…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_viral.leaf_curl_virus:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Leaf Curl Virus：tomato / Leaf Curl Virus（Tomato leaf curl virus） 叶片症状： - Distinct upward curling or cupping of leaf margins. - Leaves become thickened, leathery, and brittle to the touch. - General yellowing (chlorosis) of foliage, sometimes with green veins. - Reduction in leaf size (microphylla). - Veins on the underside of leaves …
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.early_blight:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·early blight：tomato / early blight（Alternaria solani） 叶片症状： - Starts as small, dark brown to black spots on lower, older leaves. - Lesions enlarge to 0.5-1.5 cm in diameter. - Lesions develop characteristic concentric rings, creating a 'target' or 'bull's-eye' appearance. - A distinct yellow halo often surrounds the dark lesion. -…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.late_blight:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·late blight：tomato / late blight（Phytophthora infestans） 叶片症状： - Large, water-soaked, gray-green to dark brown lesions, often appearing on leaf edges or tips. - Lesions expand rapidly and have an irregular, blotchy shape. - A pale green or yellow halo may surround the necrotic lesion. - Under humid conditions, a fuzzy, white mold…
 - source=plantinquiry；doc_id=plantinquiry:tomato.physiological_symptom.yellowing_symptom:lookalikes；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Yellowing Symptom：tomato / Yellowing Symptom（） 鉴别诊断要点： 易混淆：Fusarium Wilt - Fusarium often causes yellowing and wilting on only one side of the plant or even one side of a leaf. - A key diagnostic is the brown vascular discoloration visible when the lower stem is cut open. - Wilting is a primary symptom, often severe and preceding wides…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.cercospora_leaf_spot:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·cercospora leaf spot：tomato / cercospora leaf spot（Cercospora spp.） 叶片症状： - Initial symptoms are small, circular, water-soaked spots, primarily on older, lower leaves. - Lesions enlarge to 2-6 mm in diameter. - Mature lesions have a distinct tan to grayish-white center. - A dark brown to black border surrounds the necrotic center. - The c…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.fusarium_wilt:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   Tomato·Fusarium wilt：Tomato / Fusarium wilt（Fusarium oxysporum f. sp. lycopersici） 叶片症状： - Yellowing of lower, older leaves, often starting on one side of a leaf or branch (unilateral). - Affected leaves wilt during the day and may recover slightly at night initially. - Petioles (leaf stalks) bend downwards, creating a drooping appearance…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.septoria_leaf_spot:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·septoria leaf spot：tomato / septoria leaf spot（Septoria lycopersici） 叶片症状： - Starts on lower, older leaves and progresses upwards. - Initial symptoms are small, water-soaked spots (1-2 mm). - Lesions develop into circular spots (3-6 mm) with dark brown or purplish-brown borders. - Mature lesion centers are typically tan, gray, or white.…
 - source=plantinquiry；doc_id=plantinquiry:tomato.pest_mite.spider_mites:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Spider Mites：tomato / Spider Mites（Tetranychus urticae） 叶片症状： - Fine, pale yellow or white stippling (tiny dots) on the upper leaf surface. - Leaves may appear dusty or dirty, particularly on the underside. - With increasing damage, stippled areas coalesce, causing leaves to turn yellow, then bronze. - Affected leaves become dry, …
 - source=plantinquiry；doc_id=plantinquiry:tomato.unknown.pest_damage:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Pest Damage：tomato / Pest Damage（） 叶片症状： - Irregularly shaped holes chewed in leaves, or skeletonization leaving only veins. - Fine, pale yellow or white speckles (stippling) on the upper leaf surface. - Winding, discolored trails or 'mines' within the leaf tissue. - Leaves are distorted, curled, or puckered. - Presence of sticky…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.leaf_mold:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·leaf mold：tomato / leaf mold（Passalora fulva） 叶片症状： - Initial symptoms are pale green or yellowish spots on the upper surface of older, lower leaves. - Spots have indefinite, diffuse borders, unlike the sharp borders of other leaf spots. - As spots enlarge, the upper surface becomes a brighter yellow, but typically does not bec…
 - source=plantinquiry；doc_id=plantinquiry:tomato.physiological_symptom.yellowing_symptom:symptom_stems；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Yellowing Symptom：tomato / Yellowing Symptom（） 茎秆症状： - Stems may appear thin, spindly, or stunted.
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.target_spot:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   Tomato·Target Spot：Tomato / Target Spot（Corynespora cassiicola） 叶片症状： - Starts as small, water-soaked spots on older, lower leaves. - Lesions expand into circular spots, 1-10 mm in diameter, with a 'target' or 'bulls-eye' appearance. - Mature lesions exhibit distinct concentric rings. - Lesion centers are typically tan or light gray wit…
 - source=plantinquiry；doc_id=plantinquiry:tomato.disease_bacterial.bacterial_spot:symptom_leaves；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·bacterial spot：tomato / bacterial spot（Xanthomonas spp. (e.g., X. perforans, X. euvesicatoria, X. vesicatoria, X. gardneri)） 叶片症状： - Initial symptoms are small (<3 mm), dark green to black, water-soaked circular spots. - Lesions become angular as their expansion is limited by small leaf veins. - A greasy or oily appearance is often …
 - source=plantinquiry；doc_id=plantinquiry:tomato.physiological_symptom.yellowing_symptom:symptom_whole_plant；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl
   tomato·Yellowing Symptom：tomato / Yellowing Symptom（） 整株症状： - Overall stunted growth. - Reduced plant vigor and productivity.
 - 【演示/模拟数据，非现场实测，不可直接用于生产】 source=seed；doc_id=seed:tomato-late-blight:symptom-fruit；出处=agriagents/rag/seed/knowledge_seed.jsonl
   番茄晚疫病·果实症状：番茄晚疫病 果实症状 - 青果期最易受害，果面出现油浸状暗绿至褐色不规则斑块。 - 病斑边缘不清晰，质地硬，后期可扩展至整个果面。 - 与早疫病的区别：早疫病病斑有明显的同心轮纹，晚疫病没有。

置信提示：检索排序分用于融合图谱和文本证据的名次，不是诊断概率或诊断结论。必须结合现场影像、环境条件与鉴别要点复核后再定级。
```
</details>

### 3. `query_soil_reference`

参数：`{"site_id": "A-03"}`

状态：`success`；返回字符数：44

<details><summary>查看工具返回</summary>

```text
[stub_knowledge] 土壤档案 @ A-03
未接入真实数据源（框架占位）。
```
</details>

### 4. `query_equipment_manual`

参数：`{"device_id": "PLC-001", "fault_code": "无故障码；喷洒机P-003 idle、电量18%"}`

状态：`success`；返回字符数：72

<details><summary>查看工具返回</summary>

```text
[stub_knowledge] 设备手册：PLC-001 / 无故障码；喷洒机P-003 idle、电量18%
未接入真实数据源（框架占位）。
```
</details>

### 5. `query_agronomy_knowledge`

参数：`{"question": "番茄结果期大棚空气湿度偏高、土壤墒情偏低时的水肥与通风管理策略"}`

状态：`success`；返回字符数：2550

<details><summary>查看工具返回</summary>

```text
[local_rag] 农艺知识检索：番茄结果期大棚空气湿度偏高、土壤墒情偏低时的水肥与通风管理策略
命中 5 条：

1. 设施番茄如何做水肥一体化管理
   【演示/模拟数据，非现场实测，不可直接用于生产】 source=seed；doc_id=seed:qa:tomato-fertigation；出处=agriagents/rag/seed/knowledge_seed.jsonl（命中方式 bm25#1+dense#1）
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：设施番茄如何做水肥一体化管理？ 答：按生育期分阶段控制。定植至缓苗期保持土壤相对含水量 70%–80%，少施或不施肥；开花坐果期适当控水蹲苗，防止徒长；果实膨大期加大水肥供应，每次灌水随水追施高钾水溶肥；采收后期减少氮肥，避免植株贪青。 注意： - 每次灌溉后要检查根区 EC 值，过高说明盐分累积，需要加大淋洗量。 - 阴天不灌或少灌，避免棚内湿度升高诱发灰霉与晚疫。

2. 春季蔬菜种植中，如何预防和治疗番茄灰霉病？
   source=qa_zh；doc_id=qa_zh:p6H9mGXky01b；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#10+dense#11）
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：春季蔬菜种植中，如何预防和治疗番茄灰霉病？ 答：春季蔬菜种植中，番茄灰霉病的预防和治疗可以通过以下方式进行： 1. 在番茄花期和坐果初期，喷施含酮喹啉的药剂，以保护花果，减少病害发生。 2. 所有蔬菜作物应定期喷施磷酸二氢钾溶液，提升植株抗病力。 3. 对于特定的病害，如番茄灰霉病，可以使用针对性的杀菌剂和生物农药，如多抗霉素、嘧霉胺等进行防治。 4. 确保土壤通风良好，避免高湿度环境，以减少病害的发生几率。 5. 在病虫害防治的同时，注意及时清理田间杂草和病叶，减少病害传播的途径。

3. 冲施肥在特殊环境中有哪些特殊效果？
   source=qa_zh；doc_id=qa_zh:WVCO2D9M08Rp；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#2+dense#2）
   适用范围：通用农艺资料，尚未验证对所问作物的适用性。
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：冲施肥在特殊环境中有哪些特殊效果？ 答：冲施肥在特殊环境中的效果主要体现在以下几个方面：1) 冲施肥具有操作简便，肥效迅速等特点，适合作为作物生长期中的追肥使用；2) 特别适用于经济作物如各种蔬菜、果树等速长或大量结果期，可以迅速补充作物生长所需的养分；3) 在冬季大棚栽培作物时，冲施肥可以解决因低温、日照不足等不利条件导致养分释放转化慢、肥效迟缓的问题，确保作物的产量和质量。

4. 如何在收获期追肥以提高西红柿的产量和品质？
   source=qa_zh；doc_id=qa_zh:94Tkoyd781En；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#3+dense#7）
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：如何在收获期追肥以提高西红柿的产量和品质？ 答：在西红柿结果期，需要施入充足的钾肥来满足果实膨大期的需要，以延缓植株衰老并显著提高产量。采收前施入硫酸钾，能有效提高果实品色。

5. What's the water requirement for guava trees, and how often should I irrigate them?
   source=qa_en；doc_id=qa_en:talhakk/agriculture-qa:48387d4199858ae2；出处=https://huggingface.co/datasets/talhakk/agriculture-qa（命中方式 bm25#9+dense#5）
   适用范围：通用农艺资料，尚未验证对所问作物的适用性。
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问题：What's the water requirement for guava trees, and how often should I irrigate them? 解答：Guava trees require consistent watering, particularly during the dry season and fruiting phase. Young trees demand more frequent irrigation, while mature ones can withstand periods of drought. Adjust irrigation frequency and volume according to soil moisture levels and climatic factors for effective water management and optimal tree health.

说明：以上为知识库原文或标有来源的摘要。「上游标注已验证」不代表人工或本项目专家审核。条目冲突时须标注分歧并核对适用范围与原始来源。
```
</details>

### 6. `query_agronomy_knowledge`

参数：`{"question": "番茄结果期土壤干旱缺水与土壤渍水涝害的症状区别，根系受损表现"}`

状态：`success`；返回字符数：2124

<details><summary>查看工具返回</summary>

```text
[local_rag] 农艺知识检索：番茄结果期土壤干旱缺水与土壤渍水涝害的症状区别，根系受损表现
命中 5 条：

1. 中微量元素水溶肥在哪些作物生长阶段有明显表现？
   source=qa_zh；doc_id=qa_zh:hJnxnWsrm4VX；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#1+dense#2）
   适用范围：通用农艺资料，尚未验证对所问作物的适用性。
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：中微量元素水溶肥在哪些作物生长阶段有明显表现？ 答：中微量元素水溶肥在早春减少黄叶和小叶病的发生；花期提高坐果率，减少落花落果；膨果期增加果实表光，减少裂果，日灼果等；成熟后期可延长叶片功能期及果实的储存期，减轻果树病害。

2. 设施番茄如何做水肥一体化管理
   【演示/模拟数据，非现场实测，不可直接用于生产】 source=seed；doc_id=seed:qa:tomato-fertigation；出处=agriagents/rag/seed/knowledge_seed.jsonl（命中方式 bm25#3+dense#1）
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：设施番茄如何做水肥一体化管理？ 答：按生育期分阶段控制。定植至缓苗期保持土壤相对含水量 70%–80%，少施或不施肥；开花坐果期适当控水蹲苗，防止徒长；果实膨大期加大水肥供应，每次灌水随水追施高钾水溶肥；采收后期减少氮肥，避免植株贪青。 注意： - 每次灌溉后要检查根区 EC 值，过高说明盐分累积，需要加大淋洗量。 - 阴天不灌或少灌，避免棚内湿度升高诱发灰霉与晚疫。

3. 西红柿种植过程中如何保证土壤的肥力？
   source=qa_zh；doc_id=qa_zh:RAMvg7FPXJB7；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#2+dense#6）
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：西红柿种植过程中如何保证土壤的肥力？ 答：西红柿是可以连续结果的植物，所以要保证土壤的肥力充足，浇灌还得比较方便，以便以后的浇水工作，土壤差不多的土质都可以种植，选择疏松的土壤是最好的。

4. 冲施肥在特殊环境中有哪些特殊效果？
   source=qa_zh；doc_id=qa_zh:WVCO2D9M08Rp；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#6+dense#3）
   适用范围：通用农艺资料，尚未验证对所问作物的适用性。
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：冲施肥在特殊环境中有哪些特殊效果？ 答：冲施肥在特殊环境中的效果主要体现在以下几个方面：1) 冲施肥具有操作简便，肥效迅速等特点，适合作为作物生长期中的追肥使用；2) 特别适用于经济作物如各种蔬菜、果树等速长或大量结果期，可以迅速补充作物生长所需的养分；3) 在冬季大棚栽培作物时，冲施肥可以解决因低温、日照不足等不利条件导致养分释放转化慢、肥效迟缓的问题，确保作物的产量和质量。

5. 靓果安在作物敏感时期如花期、幼果期、膨果期、抽梢期使用时有什么特点？
   source=qa_zh；doc_id=qa_zh:Xu5DfLf1fLZM；出处=https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery（命中方式 bm25#4+dense#10）
   适用范围：通用农艺资料，尚未验证对所问作物的适用性。
   使用约束：主题相关原文；通用资料未验证具体作物适用性，不替代处置方案、实测档案或型号手册。
   问：靓果安在作物敏感时期如花期、幼果期、膨果期、抽梢期使用时有什么特点？ 答：靓果安是中草药制剂，安全性高，在作物敏感时期如花期、幼果期、膨果期、抽梢期用药没有伤害，不但可以杀菌防治病害，而且里面含有多糖、多肽、氨基酸等高能量物质及功能性物质，可被作物直接吸收利用，提高作物的抗逆性。

说明：以上为知识库原文或标有来源的摘要。「上游标注已验证」不代表人工或本项目专家审核。条目冲突时须标注分歧并核对适用范围与原始来源。
```
</details>

### 7. `query_soil_reference`

参数：`{"site_id": "S-001"}`

状态：`success`；返回字符数：45

<details><summary>查看工具返回</summary>

```text
[stub_knowledge] 土壤档案 @ S-001
未接入真实数据源（框架占位）。
```
</details>

### 8. `query_equipment_manual`

参数：`{"device_id": "P-003", "fault_code": "喷洒机 idle，电量低于阈值，无故障码"}`

状态：`success`；返回字符数：66

<details><summary>查看工具返回</summary>

```text
[stub_knowledge] 设备手册：P-003 / 喷洒机 idle，电量低于阈值，无故障码
未接入真实数据源（框架占位）。
```
</details>

### 9. `query_equipment_manual`

参数：`{"device_id": "V-003", "fault_code": "阀门 closed，灌溉未执行"}`

状态：`success`；返回字符数：61

<details><summary>查看工具返回</summary>

```text
[stub_knowledge] 设备手册：V-003 / 阀门 closed，灌溉未执行
未接入真实数据源（框架占位）。
```
</details>

### 10. `search_knowledge_base`

参数：`{"query": "早疫病 与 晚疫病 靶斑病 鉴别 同心轮纹 空气湿度", "source": "plantinquiry", "kind": "diagnosis", "top_k": 5, "crop": "tomato"}`

状态：`success`；返回字符数：5389

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "query": "早疫病 与 晚疫病 靶斑病 鉴别 同心轮纹 空气湿度", "source_filter": "plantinquiry", "kind_filter": "diagnosis", "crop_filter": "tomato", "dataset_filter": "", "found": true, "reason": "", "evidence": [{"doc_id": "plantinquiry:tomato.disease_fungal.late_blight:lookalikes", "source": "plantinquiry", "kind": "diagnosis", "title": "tomato·late blight", "snippets": ["tomato / late blight（Phytophthora infestans）鉴别诊断要点：易混淆：early blight- Early blight lesions have distinct concentric rings ('target spots'), which are absent in late blight.- Late blight lesions are larger, more water-soaked and irregular."], "full_text_chars": 1007, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "late blight", "disease_id": "tomato.disease_fungal.late_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#1+dense#1"}, {"doc_id": "plantinquiry:tomato.disease_fungal.leaf_mold:lookalikes", "source": "plantinquiry", "kind": "diagnosis", "title": "tomato·leaf mold", "snippets": ["tomato / leaf mold（Passalora fulva）鉴别诊断要点：易混淆：late blight- Leaf mold has olive-green/grayish, velvety mold on the leaf *underside*;late blight has white, fuzzy/downy mold, often at the lesion edge.- Leaf mold lesions are pale yellow on top;"], "full_text_chars": 859, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "leaf mold", "disease_id": "tomato.disease_fungal.leaf_mold", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#3+dense#3"}, {"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:lookalikes", "source": "plantinquiry", "kind": "diagnosis", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani）鉴别诊断要点：易混淆：septoria_leaf_spot- Septoria lesions are smaller (1-3 mm) and more numerous than early blight lesions.- Septoria lesions have a tan or gray center with a dark brown border, but lack the distinct concentric rings of early blight."], "full_text_chars": 1323, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#2+dense#5"}, {"doc_id": "plantinquiry:tomato.disease_fungal.fusarium_wilt:lookalikes", "source": "plantinquiry", "kind": "diagnosis", "title": "Tomato·Fusarium wilt", "snippets": ["Tomato / Fusarium wilt（Fusarium oxysporum f.sp.lycopersici）鉴别诊断要点：易混淆：Late blight- Late blight causes large, dark, water-soaked lesions on leaves and dark brown lesions on stems, which are absent in Fusarium wilt.- Late blight can show fuzzy white mold on the underside of leaf lesions in humid conditions;"], "full_text_chars": 1295, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "Tomato", "disease": "Fusarium wilt", "disease_id": "tomato.disease_fungal.fusarium_wilt", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#6+dense#2"}, {"doc_id": "plantinquiry:tomato.disease_fungal.septoria_leaf_spot:lookalikes", "source": "plantinquiry", "kind": "diagnosis", "title": "tomato·septoria leaf spot", "snippets": ["tomato / septoria leaf spot（Septoria lycopersici）鉴别诊断要点：易混淆：early blight- Lesions are larger (>6 mm) and often have a distinct 'target-like' appearance with concentric rings.- A yellow halo is often prominent around early blight lesions."], "full_text_chars": 743, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "septoria leaf spot", "disease_id": "tomato.disease_fungal.septoria_leaf_spot", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#4+dense#4"}], "full_text_tool": "get_knowledge_document(doc_id)", "usage": "snippets 是待核对的命中片段，不是全文。仅在片段不足以完成鉴别时，才用 get_knowledge_document(doc_id) 按需读取全文。处置方案、地块档案和设备故障须再用专用工具按作物/对象核对；不得把相似度作为诊断置信度。"}
```
</details>

### 11. `search_knowledge_base`

参数：`{"query": "番茄早疫病 发生条件 空气湿度 高温高湿 严重度 发展", "source": "plantinquiry", "kind": "environment", "top_k": 5}`

状态：`success`；返回字符数：5638

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "query": "番茄早疫病 发生条件 空气湿度 高温高湿 严重度 发展", "source_filter": "plantinquiry", "kind_filter": "environment", "crop_filter": "", "dataset_filter": "", "found": true, "reason": "", "evidence": [{"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:environment", "source": "plantinquiry", "kind": "environment", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani）风险因子：high humidity；prolonged leaf wetness；warm temperatures；plant stress (e.g., nutrient deficiency, heavy fruit load)适宜温度（昼）：24–29适宜相对湿度%：85–100叶面湿润时长阈值：9 小时传播途径：wind；rain splash；irrigation water；contaminated equipment；infected seed越冬场所：infected plant debris；soil；infected seed；"], "full_text_chars": 342, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#1+dense#1"}, {"doc_id": "plantinquiry:tomato.disease_fungal.late_blight:environment", "source": "plantinquiry", "kind": "environment", "title": "tomato·late blight", "snippets": ["tomato / late blight（Phytophthora infestans）风险因子：cool, moist conditions；prolonged leaf wetness；high humidity；dense plant canopy适宜温度（昼）：15–21适宜温度（夜）：10–15适宜相对湿度%：90–100叶面湿润时长阈值：10 小时传播途径：wind-blown sporangia；rain splash；infected transplants；contaminated equipment越冬场所：infected potato tubers；infected tomato volunteers；"], "full_text_chars": 344, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "late blight", "disease_id": "tomato.disease_fungal.late_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#3+dense#2"}, {"doc_id": "plantinquiry:potato.disease_fungal.early_blight:environment", "source": "plantinquiry", "kind": "environment", "title": "potato·early blight", "snippets": ["potato / early blight（Alternaria solani）风险因子：high humidity；frequent rainfall or overhead irrigation；plant stress (e.g., nutrient deficiency, insect damage)；older, senescing leaves适宜温度（昼）：24–29适宜相对湿度%：90–100叶面湿润时长阈值：9 小时传播途径：wind；rain splash；irrigation water；infected equipment越冬场所：infected plant debris；soil；"], "full_text_chars": 347, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "potato", "disease": "early blight", "disease_id": "potato.disease_fungal.early_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#2+dense#7"}, {"doc_id": "plantinquiry:tomato.disease_fungal.target_spot:environment", "source": "plantinquiry", "kind": "environment", "title": "Tomato·Target Spot", "snippets": ["Tomato / Target Spot（Corynespora cassiicola）风险因子：High humidity；Poor air circulation；Overhead irrigation；Warm temperatures；Extended periods of leaf wetness适宜温度（昼）：24–32适宜温度（夜）：20–26适宜相对湿度%：80–100叶面湿润时长阈值：8 小时传播途径：Wind；Water splash (rain, irrigation)；Contaminated tools；Infected seed越冬场所：Infected plant debris；"], "full_text_chars": 348, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "Tomato", "disease": "Target Spot", "disease_id": "tomato.disease_fungal.target_spot", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#13+dense#3"}, {"doc_id": "plantinquiry:tomato.disease_fungal.leaf_mold:environment", "source": "plantinquiry", "kind": "environment", "title": "tomato·leaf mold", "snippets": ["tomato / leaf mold（Passalora fulva）风险因子：high relative humidity；poor air circulation；greenhouse or high tunnel production；extended periods of leaf wetness；dense plant canopy适宜温度（昼）：22–24适宜温度（夜）：15–21适宜相对湿度%：85–100叶面湿润时长阈值：10 小时传播途径：wind；rain splash；contaminated tools；clothing；infected seeds越冬场所：infected plant debris；"], "full_text_chars": 365, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "leaf mold", "disease_id": "tomato.disease_fungal.leaf_mold", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#15+dense#4"}], "full_text_tool": "get_knowledge_document(doc_id)", "usage": "snippets 是待核对的命中片段，不是全文。仅在片段不足以完成鉴别时，才用 get_knowledge_document(doc_id) 按需读取全文。处置方案、地块档案和设备故障须再用专用工具按作物/对象核对；不得把相似度作为诊断置信度。"}
```
</details>

### 12. `get_knowledge_document`

参数：`{"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:lookalikes"}`

状态：`success`；返回字符数：2350

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "doc_id": "plantinquiry:tomato.disease_fungal.early_blight:lookalikes", "found": true, "reason": "", "document": {"source": "plantinquiry", "kind": "diagnosis", "title": "tomato·early blight", "text": "tomato / early blight（Alternaria solani）\n鉴别诊断要点：\n易混淆：septoria_leaf_spot\n- Septoria lesions are smaller (1-3 mm) and more numerous than early blight lesions.\n- Septoria lesions have a tan or gray center with a dark brown border, but lack the distinct concentric rings of early blight.\n- Tiny black specks (pycnidia) are often visible in the center of Septoria lesions.\n易混淆：late_blight\n- Late blight lesions are larger, water-soaked, and irregularly shaped with a greasy appearance.\n- Late blight lesions lack the 'target' or 'bull's-eye' rings of early blight.\n- A white, fuzzy mold (sporangia) often develops on the underside of late blight lesions in cool, moist conditions.\n- Late blight progresses much more rapidly, capable of destroying a plant in days.\n易混淆：target_spot\n- Target spot lesions often have a lighter tan center with more defined, darker concentric rings than early blight.\n- Lesions may have a 'shot-hole' appearance as the center falls out.\n- Target spot lesions tend to be smaller and more numerous than classic early blight lesions.\n易混淆：bacterial_spot\n- Bacterial spot lesions are typically small, angular (limited by leaf veins), and initially water-soaked.\n- Lesions lack the concentric rings of early blight and often have a greasy appearance.\n- Bacterial spots may not have a prominent yellow halo.", "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "dataset_provenance": [{"dataset": "PlantInquiryVQA", "original_doc_id": "plantinquiry:tomato.disease_fungal.early_blight:lookalikes", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl"}], "is_demo": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}}, "usage": "这是按精确 doc_id 读取的全文；仅用于命中片段不足时的鉴别和引用核对。"}
```
</details>

### 13. `get_knowledge_document`

参数：`{"doc_id": "plantinquiry:tomato.disease_fungal.target_spot:environment"}`

状态：`success`；返回字符数：1363

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "doc_id": "plantinquiry:tomato.disease_fungal.target_spot:environment", "found": true, "reason": "", "document": {"source": "plantinquiry", "kind": "environment", "title": "Tomato·Target Spot", "text": "Tomato / Target Spot（Corynespora cassiicola）\n风险因子：High humidity；Poor air circulation；Overhead irrigation；Warm temperatures；Extended periods of leaf wetness\n适宜温度（昼）：24–32\n适宜温度（夜）：20–26\n适宜相对湿度%：80–100\n叶面湿润时长阈值：8 小时\n传播途径：Wind；Water splash (rain, irrigation)；Contaminated tools；Infected seed\n越冬场所：Infected plant debris；On alternative weed hosts；In soil", "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "dataset_provenance": [{"dataset": "PlantInquiryVQA", "original_doc_id": "plantinquiry:tomato.disease_fungal.target_spot:environment", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl"}], "is_demo": false, "metadata": {"crop": "Tomato", "disease": "Target Spot", "disease_id": "tomato.disease_fungal.target_spot", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}}, "usage": "这是按精确 doc_id 读取的全文；仅用于命中片段不足时的鉴别和引用核对。"}
```
</details>

### 14. `search_knowledge_base`

参数：`{"query": "番茄早疫病 黄瓜 靶斑病 芝麻斑病 同心轮纹 鉴别 病斑直径", "source": "plantinquiry", "kind": "symptom", "top_k": 5, "crop": "tomato"}`

状态：`success`；返回字符数：5381

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "query": "番茄早疫病 黄瓜 靶斑病 芝麻斑病 同心轮纹 鉴别 病斑直径", "source_filter": "plantinquiry", "kind_filter": "symptom", "crop_filter": "tomato", "dataset_filter": "", "found": true, "reason": "", "evidence": [{"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:symptom_whole_plant", "source": "plantinquiry", "kind": "symptom", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani） 整株症状： - Progressive defoliation from the bottom of the plant upwards. - Reduced plant vigor and yield."], "full_text_chars": 143, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "part": "whole_plant", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#2+dense#3"}, {"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:symptom_fruit", "source": "plantinquiry", "kind": "symptom", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani） 果实症状： - Dark, leathery, sunken lesions form on the fruit, typically at the stem end (calyx). - Fruit lesions also show concentric rings. - In humid conditions, a velvety black mass of fungal spores may cover fruit lesions."], "full_text_chars": 263, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "part": "fruit", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#4+dense#2"}, {"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:symptom_stems", "source": "plantinquiry", "kind": "symptom", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani） 茎秆症状： - Dark, slightly sunken, oval-shaped lesions can form on stems, often near the soil line (collar rot). - Stem lesions also exhibit the characteristic concentric rings."], "full_text_chars": 214, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "part": "stems", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#3+dense#4"}, {"doc_id": "plantinquiry:tomato.disease_fungal.late_blight:symptom_signs_microscopic_or_visible", "source": "plantinquiry", "kind": "symptom", "title": "tomato·late blight", "snippets": ["tomato / late blight（Phytophthora infestans） 病征（镜检/可见）症状： - White, downy or fuzzy growth (sporangiophores and sporangia) on the underside of leaves and on stem/fruit lesions, especially in the morning or during humid weather."], "full_text_chars": 225, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "late blight", "disease_id": "tomato.disease_fungal.late_blight", "part": "signs_microscopic_or_visible", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#6+dense#9"}, {"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:symptom_leaves", "source": "plantinquiry", "kind": "symptom", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani）叶片症状：- Starts as small, dark brown to black spots on lower, older leaves.- Lesions enlarge to 0.5-1.5 cm in diameter.- Lesions develop characteristic concentric rings, creating a 'target' or 'bull's-eye' appearance.- A distinct yellow halo often surrounds the dark lesion."], "full_text_chars": 475, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "part": "leaves", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#5+dense#11"}], "full_text_tool": "get_knowledge_document(doc_id)", "usage": "snippets 是待核对的命中片段，不是全文。仅在片段不足以完成鉴别时，才用 get_knowledge_document(doc_id) 按需读取全文。处置方案、地块档案和设备故障须再用专用工具按作物/对象核对；不得把相似度作为诊断置信度。"}
```
</details>

### 15. `search_knowledge_base`

参数：`{"query": "番茄早疫病 防治 管理 病残体清除 湿度控制 通风", "source": "plantinquiry", "kind": "management", "top_k": 3, "crop": "tomato"}`

状态：`success`；返回字符数：3681

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "query": "番茄早疫病 防治 管理 病残体清除 湿度控制 通风", "source_filter": "plantinquiry", "kind_filter": "management", "crop_filter": "tomato", "dataset_filter": "", "found": true, "reason": "", "evidence": [{"doc_id": "plantinquiry:tomato.disease_fungal.early_blight:management", "source": "plantinquiry", "kind": "management", "title": "tomato·early blight", "snippets": ["tomato / early blight（Alternaria solani）栽培措施：- Use certified disease-free seed and transplants.- Rotate crops, avoiding solanaceous plants for 2-3 years.- Maintain proper plant spacing for good air circulation.- Use mulch to reduce soil splash onto lower leaves.- Prune and remove lower, infected leaves."], "full_text_chars": 935, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "early blight", "disease_id": "tomato.disease_fungal.early_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#1+dense#2", "required_tool": "get_treatment_options(diagnosis, crop)"}, {"doc_id": "plantinquiry:tomato.disease_fungal.late_blight:management", "source": "plantinquiry", "kind": "management", "title": "tomato·late blight", "snippets": ["tomato / late blight（Phytophthora infestans）栽培措施：- Use certified disease-free seeds and transplants.- Ensure good air circulation through proper plant spacing and pruning.- Avoid overhead irrigation;use drip irrigation to keep foliage dry.- Remove and destroy infected plants and debris promptly."], "full_text_chars": 922, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "late blight", "disease_id": "tomato.disease_fungal.late_blight", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#3+dense#1", "required_tool": "get_treatment_options(diagnosis, crop)"}, {"doc_id": "plantinquiry:tomato.pest_insect.leaf_miner:management", "source": "plantinquiry", "kind": "management", "title": "tomato·Leaf Miner", "snippets": ["tomato / Leaf Miner（Liriomyza spp.）栽培措施：- Remove and destroy infested leaves or entire plants early in the infestation.- Control broadleaf weed hosts in and around the growing area.- Use reflective mulches to deter adult flies from landing on plants.- In greenhouses, use insect screening on vents and doors.生物防治："], "full_text_chars": 895, "citation": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "is_demo": false, "applicability_validated": false, "metadata": {"crop": "tomato", "disease": "Leaf Miner", "disease_id": "tomato.pest_insect.leaf_miner", "dataset": "PlantInquiryVQA", "source_ref": "https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl", "project_human_verified": false, "evidence_status": "public_disease_card"}, "match_method": "bm25#2+dense#11", "required_tool": "get_treatment_options(diagnosis, crop)"}], "full_text_tool": "get_knowledge_document(doc_id)", "usage": "snippets 是待核对的命中片段，不是全文。仅在片段不足以完成鉴别时，才用 get_knowledge_document(doc_id) 按需读取全文。处置方案、地块档案和设备故障须再用专用工具按作物/对象核对；不得把相似度作为诊断置信度。"}
```
</details>

### 16. `get_treatment_options`

参数：`{"diagnosis": "番茄早疫病", "crop": "番茄"}`

状态：`success`；返回字符数：1400

<details><summary>查看工具返回</summary>

```text
[local_rag] 处置方案查询：番茄早疫病，作物=番茄

■ 方案 1：early blight（作物 tomato）
  source=plantinquiry；doc_id=plantinquiry:tomato.disease_fungal.early_blight:management；出处=https://github.com/syed-nazmus-sakib/PlantInquiryVQA/blob/main/diseases_knowledge_base/all_cards.jsonl（linked-card）
  tomato / early blight（Alternaria solani）
  栽培措施：
  - Use certified disease-free seed and transplants.
  - Rotate crops, avoiding solanaceous plants for 2-3 years.
  - Maintain proper plant spacing for good air circulation.
  - Use mulch to reduce soil splash onto lower leaves.
  - Prune and remove lower, infected leaves.
  - Avoid overhead irrigation; use drip irrigation instead.
  - Ensure balanced plant nutrition.
  生物防治：
  - Application of bio-fungicides containing Bacillus subtilis or Trichoderma species.
  化学防治：
  - Application of preventative or curative fungicides containing active ingredients like chlorothalonil, mancozeb, or copper-based compounds.
  - Follow label instructions and alternate fungicide classes to prevent resistance.
  注意事项：Integrated pest management (IPM) combining cultural, biological, and chemical strategies is most effective. Fungicide applications are most effective when applied preventatively based on weather forecasts.

■ 关联卡片摘要
  病害类型：disease_fungal
  作物：tomato
  病原学名：Alternaria solani

⚠ 字段完整性：知识库收录的是防治**方向**（栽培/生物/化学措施），通常**不含**具体用药量、安全间隔期与禁用情形。这些字段缺失时不得由模型补全；涉及化学药剂须转人工确认后方可执行。
```
</details>

### 17. `search_knowledge_base`

参数：`{"query": "土壤墒情传感器 两路读数不一致 数据可疑 故障排查", "source": "seed", "kind": "equipment_manual", "top_k": 4}`

状态：`success`；返回字符数：3778

<details><summary>查看工具返回</summary>

```text
{"vendor": "local_rag", "query": "土壤墒情传感器 两路读数不一致 数据可疑 故障排查", "source_filter": "seed", "kind_filter": "equipment_manual", "crop_filter": "", "dataset_filter": "", "found": true, "reason": "", "evidence": [{"doc_id": "seed:equipment:demo-fan-v1:offline", "source": "seed", "kind": "equipment_manual", "title": "DEMO-FAN-V1 / FAN-03 OFFLINE（演示手册）", "snippets": ["【演示手册：虚构型号与故障约定，不是厂商技术资料，不可据此操作真实设备】 型号：DEMO-FAN-V1；设备标识：FAN-03；故障码：OFFLINE；演示场景：GH-09 设施黄瓜。 模拟故障：风机控制器离线。演示排查流程：检查模拟网关心跳和供电状态字段，核对设备地址，记录最后在线时间后转人工复核。演示停止条件：没有可靠状态反馈时不发送重启或转速指令。真实电气和机械检查必须遵守对应厂商型号手册与隔离规定。"], "full_text_chars": 205, "citation": "AgriAgents fictional device fixture; not a manufacturer manual", "is_demo": true, "applicability_validated": false, "metadata": {"crop": "黄瓜", "site_id": "GH-09", "device_id": "FAN-03", "device_ids": ["FAN-03"], "device_aliases": ["DEMO-FAN-V1", "FAN-03"], "model": "DEMO-FAN-V1", "fault_code": "OFFLINE", "source_ref": "AgriAgents fictional device fixture; not a manufacturer manual", "project_human_verified": false, "evidence_status": "demo"}, "match_method": "bm25#1+dense#3", "required_tool": "query_equipment_manual(device_id, fault_code)"}, {"doc_id": "seed:equipment:demo-valve-v1:e04", "source": "seed", "kind": "equipment_manual", "title": "DEMO-VALVE-V1 / VALVE-11 E04（演示手册）", "snippets": ["【演示手册：虚构型号与故障约定，不是厂商技术资料，不可据此操作真实设备】 型号：DEMO-VALVE-V1；设备标识：VALVE-11；故障码：E04；演示场景：FIELD-07 设施番茄。 模拟故障：开阀后没有流量反馈。演示排查流程：核对模拟控制器与流量记录时间，检查模拟通信状态，记录异常并转人工复核。演示停止条件：指令状态与流量反馈矛盾时停止后续指令。真实检修必须查阅对应厂商型号手册，由授权人员断电隔离后进行。"], "full_text_chars": 209, "citation": "AgriAgents fictional device fixture; not a manufacturer manual", "is_demo": true, "applicability_validated": false, "metadata": {"crop": "番茄", "site_id": "FIELD-07", "device_id": "VALVE-11", "device_ids": ["VALVE-11"], "device_aliases": ["DEMO-VALVE-V1", "VALVE-11"], "model": "DEMO-VALVE-V1", "fault_code": "E04", "source_ref": "AgriAgents fictional device fixture; not a manufacturer manual", "project_human_verified": false, "evidence_status": "demo"}, "match_method": "bm25#3+dense#1", "required_tool": "query_equipment_manual(device_id, fault_code)"}, {"doc_id": "seed:equipment:irrigation-valve-e04", "source": "seed", "kind": "equipment_manual", "title": "演示灌溉电磁阀 E04 故障处置", "snippets": ["【演示手册：不是实际厂商型号资料，不可据此操作真实设备】 灌溉电磁阀 故障码 E04（开阀无流量反馈） 可能原因，按排查顺序： 1. 电磁阀线圈断路或短路——用万用表测线圈阻值，正常范围见该型号铭牌。 2. 阀芯被杂质卡死——拆开清洗，检查前置过滤器是否堵塞。 3. 管压不足——检查主管压力是否达到阀的开启压力。 4. 控制线路接触不良——检查接线端子与控制柜输出。 处置：确认原因后再下发开阀指令；未确认前**不要重复下发**，避免线圈过热烧毁。 安全：检修前必须关闭该支路阀门并泄压。"], "full_text_chars": 245, "citation": "agriagents/rag/seed/knowledge_seed.jsonl", "is_demo": true, "applicability_validated": false, "metadata": {"device_id": "irrigation_valve", "model": "DEMO-VALVE-V1", "fault_code": "E04", "source_ref": "agriagents/rag/seed/knowledge_seed.jsonl", "project_human_verified": false, "evidence_status": "demo"}, "match_method": "bm25#2+dense#2", "required_tool": "query_equipment_manual(device_id, fault_code)"}, {"doc_id": "seed:equipment:greenhouse-roller", "source": "seed", "kind": "equipment_manual", "title": "温室卷帘机常见故障", "snippets": ["温室卷帘机 常见故障 - 运行中异响：多为链条松动或轴承缺油，停机后检查张紧度。 - 单侧不动：两侧电机不同步，检查行程开关与限位。 - 过载停机：检查保温被是否被冰雪冻住或被卡住，**不要强行重启**。 安全：卷帘机作业时严禁站在帘下；检修必须断电挂牌。"], "full_text_chars": 128, "citation": "agriagents/rag/seed/knowledge_seed.jsonl", "is_demo": true, "applicability_validated": false, "metadata": {"fault_code": "", "source_ref": "agriagents/rag/seed/knowledge_seed.jsonl", "project_human_verified": false, "evidence_status": "demo"}, "match_method": "bm25#4+dense#4", "required_tool": "query_equipment_manual(device_id, fault_code)"}], "full_text_tool": "get_knowledge_document(doc_id)", "usage": "snippets 是待核对的命中片段，不是全文。仅在片段不足以完成鉴别时，才用 get_knowledge_document(doc_id) 按需读取全文。处置方案、地块档案和设备故障须再用专用工具按作物/对象核对；不得把相似度作为诊断置信度。"}
```
</details>

## 诊断报告

# 诊断报告

**对象**：对象A-03（南宁设施大棚番茄区，番茄结果期）｜**基准日期**：2026-09-26｜**诊断范围**：IMG-20260926-1001 病斑成因 + 本轮 sensor 通道偏离

---

## 一、候选假设与排除

### 生物因素（病虫害）

**H1｜番茄早疫病（Alternaria solani）— 主因候选**
- 支持证据：
  - 影像 IMG-20260926-1001 @09-26 09:45：12 处**褐色圆形病斑、具同心轮纹、边缘黄化**，覆盖叶面积约 18%。
  - 知识库症状卡逐条吻合：「Lesions develop characteristic concentric rings, creating a 'target' or 'bull's-eye' appearance」「A distinct yellow halo often surrounds the dark lesion」「Lesions enlarge to 0.5-1.5 cm in diameter」（`plantinquiry:tomato.disease_fungal.early_blight:symptom_leaves`）。
  - 环境互证：S-004 空气湿度 86%（阈值上限 85，7天均值 72）@09-26 09:58；S-003 28.0°C @09-26 09:58。知识库风险因子为「high humidity；prolonged leaf wetness；warm temperatures；plant stress」，适温 24–29℃、适宜相对湿度 85–100%（`plantinquiry:tomato.disease_fungal.early_blight:environment`）。**温度落于适温区间、湿度已越过其适宜下限**，两条环境条件同时满足。
  - 生境互证：V-003 closed、P-003 idle 说明灌溉/喷洒均未执行；知识库记载灌水多、低洼积水为适宜发生条件，并记载传播途径含 irrigation water（`cropdp:番茄早疫病:0`、`plantinquiry:tomato.disease_fungal.early_blight:environment`）。
- 反证/缺失：无历史影像基线，**无法判断扩散速度**；未镜检/未做病斑分离，无病原学确认；未查茎部（collar rot）、果实（蒂部同心轮纹）与下部老叶（自下向上落叶）以完成部位级互证。
- 判定：**证据支持的判断（主因）**。

**H2｜番茄靶斑病（Corynespora cassiicola）— 未排除的竞争假设**
- 支持证据：知识库靶斑病同样为圆形、具明显同心轮纹、呈"bulls-eye"（`plantinquiry:tomato.disease_fungal.target_spot:symptom_leaves`）；环境条件 80–100% RH、昼温 24–32℃（`plantinquiry:tomato.disease_fungal.target_spot:environment`）与本轮 86%RH/28.0℃ 同样相容；且记载「通风不良、高湿」为风险因子，与本轮大棚状态一致。
- 反证/差异点：早疫病鉴别要点明确「Target spot lesions often have a lighter tan center with more defined, darker concentric rings than early blight」「Lesions may have a 'shot-hole' appearance as the center falls out」「tend to be smaller and more numerous」（`plantinquiry:tomato.disease_fungal.early_blight:lookalikes`）。本轮影像**未描述斑心颜色（tan/灰白）、未描述中心脱落成孔洞、未描述病斑直径与数量密度**，故无法从现有描述区分二者。
- 判定：**尚未排除的可能**（同一同心轮纹症状族，需补充形态细节方能收敛）。

**H3｜番茄灰斑病（Septoria lycopersici）— 已排除（依据症状形态）**
- 理由：Septoria 病斑更小（1–3 mm）且数量更多，斑心 tan/灰色配深褐色边缘，**缺乏早疫病那样清晰的同心轮纹**，中心常可见小黑点（pycnidia）（`plantinquiry:tomato.disease_fungal.early_blight:lookalikes`、`plantinquiry:tomato.disease_fungal.septoria_leaf_spot:symptom_leaves`）。影像明确描述「同心轮纹」，与之矛盾。
- 但保留：若后续近距离镜检发现中心小黑点，应重新评估。

**H4｜番茄晚疫病（Phytophthora infestans）— 已排除**
- 理由：晚疫病病斑大、水浸状、形状不规则、油腻感，**无同心轮纹**，冷凉高湿条件（昼 15–21℃、夜 10–15℃、RH 90–100%）下叶背出现白色霉层（`plantinquiry:tomato.disease_fungal.late_blight:lookalikes`、`plantinquiry:tomato.disease_fungal.late_blight:environment`）。本轮 S-003 28.0℃ 明显高于其适温区间，且影像有同心轮纹、无边缘性不规则水浸斑，故排除。
- 注：知识库中晚疫病相关条目有一条来自 `seed:tomato-late-blight:symptom-fruit`，**属演示/模拟数据，非现场实测，仅作背景，不构成本轮证据**。

**H5｜番茄枯萎病（Fusarium oxysporum f. sp. lycopersici）— 已排除**
- 理由：枯萎病以单侧黄化萎蔫、维管束褐变为特征，不产生同心轮纹斑（`plantinquiry:tomato.disease_fungal.fusarium_wilt:symptom_leaves`、`plantinquiry:tomato.physiological_symptom.yellowing_symptom:lookalikes`）。本轮无萎蔫、无单侧黄化描述。
- 注：其适宜发生条件记载为「土壤湿度过低」（`cropdp:番茄枯萎病:0`），与 S-001 偏低读数方向一致，但症状不符，故排除。

**H6｜虫害（螨类）/细菌性病害 — 已排除（现有证据）**
- 螨害为白色/浅黄细小点状刺吸斑、叶背结网，非同心轮纹（`plantinquiry:tomato.pest_mite.spider_mites:symptom_leaves`）；细菌性斑疹病病斑**小而角状、受叶脉限制、无同心轮纹**、油腻感、常无显著黄晕（`plantinquiry:tomato.disease_bacterial.bacterial_spot:symptom_leaves`、`plantinquiry:tomato.disease_fungal.early_blight:lookalikes`）。均与影像不符。

### 非生物因素（土壤与水肥）

**H7｜土壤干旱缺水（墒情不足）— 疑似存在，但不能判定为病斑主因**
- 支持证据：S-001 = 0.12 volumetric @09-26 09:58，低于阈值下限 0.20，且远低于 7 天均值 0.28（感知报告）。
- 反证/缺失：S-002 = 0.34 @09-25 22:00 在同一地块给出相反方向读数；两路差值 0.22 已超一致性阈值 0.15，感知报告明确**不能同时作为同一地块墒情依据**，且 S-002 超 12 小时无更新、原因未知。墒情缺 7 天连续窗口，趋势无法建立。
- **土壤档案缺失**：`query_soil_reference(A-03)` 返回 `[stub_knowledge] 未接入真实数据源（框架占位）`；S-001 同样未命中。**无本底质地/pH/有机质/盐分/历史施肥记录**，因此无法评估盐渍化、缺素或肥害，**不得用其他地块档案替代**。
- 判定：**合理怀疑，未能评估**（墒情真伪未定）。目前**不构成病斑的解释**——早疫病为真菌性病害，症状与单纯缺水斑不符；干旱更可能是**加剧因素**（知识库记载 plant stress（如 nutrient deficiency、heavy fruit load）为早疫病风险因子，`plantinquiry:tomato.disease_fungal.early_blight:environment`）。

**H8｜渍水/涝害、盐渍化、药害 — 无法评估**
- 理由：需土壤本底与灌溉记录支撑，A-03 土壤档案未接入（stub），无法判定。V-003 closed、P-003 idle 提示本轮未灌溉，可部分降低"新近渍水"可能性，但不能替代本底数据。

**H9｜环控层面的棚内高湿 — 疑似并发次要因素**
- 支持证据：S-004 = 86% @09-26 09:58 超上限 85（7天均值 72）；农艺资料提示设施内应避免阴天灌水、通风不良致棚内湿度升高（`seed:qa:tomato-fertigation`，**演示数据，仅作方向参考**）。知识库记载早疫病与靶斑病均以"poor air circulation / high humidity"为风险因子。
- 判定：**合理怀疑**，属**并发的次要环境因素**（利于病害发展），非初始成因。

### 设备与环境因素

- **PLC-001 / V-003 / P-003 手册查询全部未命中**：`query_equipment_manual(PLC-001, ...)`、`(P-003, ...)`、`(V-003, ...)` 均返回 `[stub_knowledge] 未接入真实数据源（框架占位）`。**无该型号/编号的任何故障处置条目，不得用相邻设备或其他型号替代。**
- 可确证的事实（来自感知报告，非手册）：PLC-001 online、V-003 closed、P-003 idle、**无故障码**；电量 18%（阈值 20）。故设备侧**无故障证据**，但**"未命中"≠"正常"**——无法排除控制/执行链路隐患。
- 检索到的演示设备手册（如 `seed:equipment:irrigation-valve-e04`）为**虚构型号演示资料，且对应 VALVE-11/E04，与本对象编号不符，不构成本轮证据**，仅说明"若未来出现开阀无流量反馈"类故障的一般排查逻辑，不作引用结论。

### 观测系统自身（数据链路）

**H10｜采集链路与传感器可信度问题 — 确认存在**
- 支持证据：S-001/S-002 互相矛盾（差值 0.22 > 0.15 一致性阈值）；S-002 超 12 小时无更新；遥感 A-03 返回 `[stub]` 未接入；水质 S-005 未接入；影像无历史基线。
- 判定：**确认存在**。S-002 的 0.34 在原因查明前**不可采信**；S-001 的 0.12 亦仅可作为"偏干信号"，不足以定量。

---

## 二、主因判断

**结论**：主因诊断为**番茄早疫病（Alternaria solani）倾向**，同时**保留番茄靶斑病（Corynespora cassiicola）为未排除的竞争假设**（同为"同心轮纹"症状族）。

**置信度：medium**
- 达到 high 的条件是"症状 + 环境条件 + 知识库鉴别要点三者互证且关键通道齐全"。本轮症状与环境两条已互证，但**靶斑病未能从现有影像描述中排除**（缺斑心颜色/孔洞/直径/密度），且**土壤档案与设备手册两条通道缺失**（均为 stub），关键通道不齐，故不能定 high；也非 low，因症状与环境证据方向一致、非间接。

**成立条件**（同时满足则维持早疫病主判）：
1. 病斑自**下部老叶**起始、直径约 0.5–1.5 cm，斑心暗褐、同心轮纹清晰、外围**明显黄晕**；
2. 棚内维持高湿（RH ≥85%）与结露/叶面湿润，昼温 24–29℃；
3. 茎部近土面出现暗色略凹陷椭圆形轮纹斑（collar rot），或果实蒂部出现暗色革质凹陷轮纹斑。

**证伪条件**（观察到即改判）：
- 斑心呈**浅褐（tan）且轮纹更深更规整**、中心脱落成"shot-hole"孔洞、病斑**更小更多** → 改判倾向**靶斑病**（`plantinquiry:tomato.disease_fungal.early_blight:lookalikes`）；
- 病斑更小（1–3 mm）、数量极多、中心可见**小黑点（pycnidia）**、无明显同心轮纹 → 改判**灰斑病/Septoria**；
- 叶背出现**白色绒毛状霉层**且病斑大而不规则水浸状、扩展极快 → 改判**晚疫病**，并升级紧急度；
- 病斑局限于叶脉间角状、油腻、无黄晕 → 改判**细菌性病害**；
- 现场复核证实 S-002 的 0.34 为真且 S-001 探头漂移/失效 → **非生物（墒情）结论需重写**。

---

## 三、鉴别依据（知识库出处）

| 用途 | 引用 |
| --- | --- |
| 早疫病症状（同心轮纹、黄晕、0.5–1.5 cm） | `plantinquiry:tomato.disease_fungal.early_blight:symptom_leaves` |
| 早疫病鉴别要点（区别 Septoria/晚疫/靶斑/细菌性） | `plantinquiry:tomato.disease_fungal.early_blight:lookalikes` |
| 早疫病环境风险因子（高湿、叶面湿润、24–29℃、85–100%RH、叶湿 9h） | `plantinquiry:tomato.disease_fungal.early_blight:environment` |
| 早疫病部位扩展（整株/茎/果） | `plantinquiry:tomato.disease_fungal.early_blight:symptom_whole_plant`、`...:symptom_stems`、`...:symptom_fruit` |
| 早疫病栽培与防治方向 | `plantinquiry:tomato.disease_fungal.early_blight:management` |
| 图谱库候选（含"同心轮纹"命中） | `cropdp:番茄早疫病:0` |
| 靶斑病症状与环境（竞争假设） | `plantinquiry:tomato.disease_fungal.target_spot:symptom_leaves`、`plantinquiry:tomato.disease_fungal.target_spot:environment` |
| 晚疫病鉴别与环境（排除依据） | `plantinquiry:tomato.disease_fungal.late_blight:lookalikes`、`plantinquiry:tomato.disease_fungal.late_blight:environment` |
| 灰斑病症状（排除依据） | `plantinquiry:tomato.disease_fungal.septoria_leaf_spot:symptom_leaves` |
| 枯萎病症状（排除依据） | `plantinquiry:tomato.disease_fungal.fusarium_wilt:symptom_leaves`、`plantinquiry:tomato.physiological_symptom.yellowing_symptom:lookalikes` |
| 细菌性斑疹病症状（排除依据） | `plantinquiry:tomato.disease_bacterial.bacterial_spot:symptom_leaves` |
| 螨害症状（排除依据） | `plantinquiry:tomato.pest_mite.spider_mites:symptom_leaves` |
| 通用水肥方向（仅中文/演示，非处置方案） | `seed:qa:tomato-fertigation`（演示数据，非实测） |

**证据边界声明**：A-03 土壤档案（S-001/S-002 均未命中）、PLC-001/P-003/V-003 设备手册**均未收录**，本轮对非生物与设备两类因素**只能给出"无法评估"**。`seed` 源全部为演示资料，不是实测，不作诊断依据。

---

## 四、严重度倾向与影响范围

- **严重度倾向：中（medium）**，与初筛一致。依据：影像已明确"存在病斑"且覆盖叶面积约 **18%**（口径：单次影像，无基线）；无紧急告警、无故障码、无植株死亡/萎蔫报告。
- **无法判断发展速度**：影像为单次采集、无历史基线，**不能区分"新发突发"与"已持续存在"**，故不排除后续升级可能，需补充时序证据。
- **影响范围（量化口径）**：
  - 病斑：约 **18% 叶面积**（IMG-20260926-1001，单次）；
  - 株数/面积：**无法量化**，原因是影像未给出采样株数、棚内总株数或病株分布比例，遥感 A-03 又未接入（`[stub]`）无法提供区级覆盖度；
  - 环境偏离通道：3 处（S-001 偏低、S-004 偏高、S-002 可疑），另 PLC 电量 1 项低于阈值。

---

## 五、建议补检（供中枢决定是否补诊断）

1. **叶部形态学复核（关键，用于早疫病 vs 靶斑病收敛）**：同一批叶片近距离定株拍照，记录①斑心颜色（暗褐 vs 浅褐 tan）②是否中心脱落成孔洞③病斑直径实测（cm）④每叶病斑数量⑤黄晕有无与宽度。→ 中枢可据此二选一收敛。
2. **部位扩展检查**：重点查**下部老叶**（是否自下而上落叶）、**茎部近土面**（collar rot 轮纹斑）、**果实蒂部**（暗色革质凹陷轮纹斑）；另查叶背有无白色绒毛霉层（排晚疫）、有无小黑点（排 Septoria）。
3. **病斑显微/分离**：挑取病斑做镜检或病菌分离培养，取病原学确证。
4. **墒情通道核实（关键，用于非生物因素定评）**：现场复核 S-001 与 S-002 探头位置、埋深、土壤接触状况，取土烘干法标定；确认 S-002 超 12 小时无更新的原因（传感器故障/通信中断/采集窗口未覆盖）。**在确认可信通道前，两路读数均不作定量依据。**
5. **补充土壤本底**：补录 A-03 土壤档案（质地、pH、有机质、EC/盐分、历史施肥），否则盐渍化/缺素/肥害三类假设持续无法评估。
6. **环境时序**：补采连续温湿度与叶面湿润时长（知识库对早疫病给出叶湿阈值 9 小时），建立 7 天窗口，用于判断是否满足病害发展条件。
7. **观测链路修复**：接入遥感 A-03 与水质 S-005，建立影像历史基线。
8. **设备侧**：核查 PLC-001/V-003/P-003 实际型号与厂商手册并归档，补足"未命中"的空缺；确认 P-003 电量 18% 的续航与充电安排。

---

## 六、处置方向

知识库对明确诊断名「番茄早疫病 / early blight（作物 tomato）」返回方案，**方向如下（逐字引自知识库，不含剂量/稀释倍数/安全间隔期）**：

- **栽培措施**（`plantinquiry:tomato.disease_fungal.early_blight:management`）：Use certified disease-free seed and transplants; Rotate crops, avoiding solanaceous plants for 2-3 years; Maintain proper plant spacing for good air circulation; Use mulch to reduce soil splash onto lower leaves; Prune and remove lower, infected leaves; Avoid overhead irrigation; use drip irrigation instead; Ensure balanced plant nutrition。
- **生物防治**（同上）：Application of bio-fungicides containing Bacillus subtilis or Trichoderma species。
- **化学防治**（同上）：Application of preventative or curative fungicides containing active ingredients like chlorothalonil, mancozeb, or copper-based compounds; Follow label instructions and alternate fungicide classes to prevent resistance。
- **注意事项**（同上）：IPM combining cultural, biological, and chemical strategies is most effective; Fungicide applications are most effective when applied preventatively based on weather forecasts。
- **防治方向出处**：`plantinquiry:tomato.disease_fungal.early_blight:management`（工具返回 disease 原值：early blight；作物：tomato）。

**约束与转人工声明**：
1. 工具明确提示：知识库收录的是防治**方向**，**不含**具体用药量、稀释倍数、禁用情形与安全间隔期；**这些字段缺失，不得由模型补全**。本轮工具返回并未给出安全间隔期 → **涉及化学药剂的具体用量/倍数/安全间隔期须转人工确认后方可执行**。
2. **靶斑病未排除前，不宜按早疫病方案直接施药**：知识库仅对"番茄早疫病"锚点返回方案，**未对靶斑病返回本方作物方案**；在病斑形态收敛前，若需用药属跨假设决策，**须转人工**。
3. 农艺资料中出现的其他药剂名（如灰霉病相关药剂）来自通用/演示问答，**未验证对番茄早疫病适用，不得转用**。
4. 化学药剂中含 chlorothalonil / mancozeb / copper-based 等成分，属植保用药范畴，**须由人工核对禁限用与标签要求后执行**。
5. 设备侧任何检修/复电指令：本轮**无该型号手册支撑**，不得下发操作指令，**须转人工**。

## 严重度评估

- 严重度：`medium`
- 置信度：`medium`
- 依据：1) 已确认存在实质病变：影像 12 处褐色同心轮纹病斑、边缘黄化，覆盖叶面积约 18%，且症状与环境（S-004 86%RH 越限、S-003 28.0℃ 落于早疫病适温 24-29℃）两条互证，指向番茄早疫病倾向（知识库症状卡逐条吻合）。2) 但未构成 high：无紧急告警、无故障码、无植株死亡或萎蔫报告；单次影像无历史基线，无法判断扩散速度，不能区分"新发突发"与"已持续"，故紧急度不可判定为"需尽快处置"。3) 亦不止 low：病斑已覆盖约 18% 叶面积且处于结果期，属需安排处置的明确异常。4) 靶斑病（Corynespora cassiicola）为未排除的竞争假设，两者同属"同心轮纹"症状族，现有影像未描述斑心颜色、中心脱落孔洞、直径与数量密度，无法收敛。5) 非生物侧：S-001（0.12，越限偏低）与 S-002（0.34，阈值内）差值 0.22 超一致性阈值 0.15，且 S-002 超 12 小时无更新，两路均不可作为定量依据；土壤档案 query_soil_reference(A-03) 返回 stub，无本底质地/pH/有机质/盐分/施肥记录，盐渍化、缺素、肥害、渍水四类假设均无法评估。6) 设备侧：PLC-001/V-003/P-003 手册查询全部未命中（stub），无该型号任何故障处置条目；"未命中"≠"正常"，无法排除控制/执行链路隐患，但当前确证事实（online/closed/idle、无故障码）不支持设备故障判级。7) 通道缺失使置信度不能定 high：土壤档案、设备手册两条关键通道缺位，遥感 A-03 与水质 S-005 未接入，影像无基线；但症状与环境证据方向一致且非间接，故亦不定 low。
- 建议补检：① 叶部形态学复核（收敛早疫病 vs 靶斑病的关键）：同批叶片近距离定株拍照，记录斑心颜色（暗褐 vs 浅褐 tan）、是否中心脱落成 shot-hole 孔洞、病斑直径实测（cm）、每叶病斑数量、黄晕有无与宽度。② 部位扩展检查：下部老叶（是否自下而上落叶）、茎部近土面（collar rot 轮纹斑）、果实蒂部（暗色革质凹陷轮纹斑）；叶背查白色绒毛状霉层（排晚疫）、中心小黑点 pycnidia（排 Septoria）。③ 病斑显微/分离培养取病原学确证。④ 墒情通道核实（非生物因素定评的关键）：现场复核 S-001 与 S-002 探头位置、埋深、土壤接触状况，取土烘干法标定；查明 S-002 超 12h 无更新原因（传感器故障/通信中断/采集窗口未覆盖）；确认可信通道前两路读数均不作定量依据。⑤ 补录 A-03 土壤本底（质地、pH、有机质、EC/盐分、历史施肥）。⑥ 环境时序：补采连续温湿度与叶面湿润时长，建立 7 天窗口（早疫病叶湿阈值 9h）。⑦ 接入遥感 A-03 与水质 S-005，建立影像历史基线。⑧ 设备侧：核查 PLC-001/V-003/P-003 实际型号与厂商手册并归档，确认 P-003 电量 18% 的续航与充电安排。⑨ 高风速影响未闭合：东南风 8.5m/s、未来 6h 7-9m/s，报告未给出作业风速限值，需确认是否覆盖 P-003 喷洒时段及是否超限。