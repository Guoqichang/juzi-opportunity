# 算力支持发现与评估系统：架构设计 v0.1

设计日期：2026-09-23。本文保留目标架构；2026-09-24 已部署服务器 v0.2，加入主动发现、定期报告、知识问答和申报准备。r4 增加 OpenCode 公开来源研究服务。实际功能、启动方式与尚未实现的部分以 [README.md](README.md) 为准，验证记录见 [IMPLEMENTATION_VALIDATION.md](IMPLEMENTATION_VALIDATION.md)。

## 设计结论

采用“有界探索 + 可重放的数据管道 + 基于证据的资格评估”。系统交付的是某家公司、某个申报批次、某类费用在某一时刻的评估，以及可执行的下一步。每次评估绑定政策版本、企业事实版本与证据。

第一版按句子互动单公司、北京/海淀优先、全国及商业算力支持作补充来设计。数据库保留 company_id，但暂不建设多租户管理平台。可在一台持续运行的机器上部署应用、Worker、PostgreSQL 和原文存储；本地机器适合开发，睡眠会影响定时任务。

现有资料作为种子和待复核线索：151 项分析不等于 151 个不同且可申请的项目；9 月 10 日扫描不代表今天仍有效；供需对接活动报名、补贴申请、商业云额度分别建模。

## 1. 替换 Archify 的表达方案

本次采用定制交互讲解图，切换首次处理、政策更新和画像补齐，观察哪些模块需要重新运行。详细设计保存在本文件。交互图只是设计演示，不连接实际政策或企业数据。

| 方案 | 适用场景 | 本次取舍 |
|---|---|---|
| 定制交互讲解图 | 解释特定系统的数据流、变化影响和设计取舍 | 本次使用；布局与交互针对这套管道编排 |
| React Flow | 拖拽节点、编辑流程、节点内部有复杂交互 | 若后续确需可编辑工作台再采用；当前架构讨论不需要流程编辑器 |
| D2 | 文本维护、版本比较、静态技术图及导出 | 适合作为之后的工程文档补充 |
| Excalidraw | 同步讨论、自由批注、快速草图 | 适合白板讨论；结构化评估仍放在应用里 |

本次未改动或卸载本地 Archify 技能。

## 2. 模块边界与数据交接

主线：来源发现 → 网页与附件采集 → 不可变证据仓 → 解析与条款提取 → 政策批次库 → 企业匹配 → 人工复核 → 工作清单。

r4 在普通采集旁增加 OpenCode 研究队列：读取失败原因，使用搜索、HTTP 或浏览器工具寻找可验证原文，再由应用校验工具保存的字节并入库。模型不直接改业务数据；替代网址保持独立来源与未核实的关联。普通采集承担批量任务，研究服务默认每天 8 次、每次最多 6 个工具调用。详见 [OpenCode 实现](OPENCODE_RESEARCH.md)。

| 模块 | 输入 | 输出与职责 |
|---|---|---|
| 来源发现 | 既有链接、官方栏目、受控搜索、人工补充 | CandidateURL；记录来源、发现路径、关联政策，不在此判断最终资格 |
| 调度器 | 到期扫描、内容变化、画像更新、人工重跑 | 持久化 Job；控制并发、预算、重试、租约及依赖 |
| 采集器 | URL 与站点适配器 | HTML/附件原字节、请求时间、状态、最终 URL、内容 hash |
| 证据仓 | 原始抓取结果 | Snapshot、附件清单和版本；旧证据可回放 |
| 文档解析器 | HTML/PDF/Office/图片 | DocumentBlock；保留页码、表格行列或段落定位及解析版本 |
| 条款提取器 | 文档块与目标字段 schema | 带引用的 RuleSet 草稿、费用窗口、申报窗口、材料清单、冲突项 |
| 政策归并器 | RuleSet 草稿、已有政策与批次 | Program → Batch → Direction；关联补充通知、延期、更正和废止证据 |
| 企业画像 | 人工确认资料、财务材料、已有申报记录 | 带生效时间和证据的 CompanyFact、Expense、Application；估算单独标记 |
| 资格评估器 | 已审核/可计算规则、企业事实、as_of 时间 | 每条条件的 true/false/unknown、阻塞项、结论与推导轨迹 |
| 复核工作台 | 冲突、低质量解析、关键未知条件 | 带依据的修订或确认；新版本重评，不直接覆盖历史结果 |
| 清单发布器 | 最新评估与人工进度 | 飞书文档内嵌表格草稿/后续授权同步、差异摘要、待办建议 |

“读取资料”“生成工作清单”和“实际提交申请”是不同业务操作。当前设计到工作清单，提交状态必须有实际回执；本次不会自动发送消息或填写外部申请。

## 3. 两种采集循环

### 已知来源的定时监测

对可信栏目与已发现项目定期检查。HTML 正文和每个附件分别做版本记录；网页正文没变也要按频率复查附件。条件请求可减少传输，但 URL 或页面更新时间相同不能证明附件内容相同。

可配置初始频率：临近截止且已匹配的项目每日复核；普通官方栏目每日；历史政策每周。频率是产品配置，依据试跑后的变更率和成本调整。

### 有界探索

根据地区、费用类型、缺失条款和新链接进行搜索，追踪正文、附件、往年指南和受理入口。每轮限制域名、页数、深度、浏览器时间和模型预算。例如先设 30 个新 URL、深度 2；这是起步配置，不是吞吐保证。

模型可以提出查询词和下一条候选链接，调度器验证并入队。新域名先登记来源角色；政府源也要按发布机关、事项、批次和地区判断适用性。搜索摘要只作为发现线索。

公众号或受限页面不能稳定读取时，记录 access_blocked，并允许用户提供正文/附件。403、验证码或附件失败不能解释成“没有政策”。来源健康与申报状态分开记录。

爬取策略同时保留少量低优先级来源抽样，人工抽查被过滤的线索；不能因为早期评分低就永久停止发现。

## 4. 证据模型与政策版本

原文存储与业务数据库分开：原文/附件放对象存储或持久卷，PostgreSQL 放 metadata、结构化事实和引用。原文不可变；文档清洗、OCR 和模型抽取都可重跑。

每条关键字段必须有 EvidenceRef：snapshot_id、block_id、locator、quote、extraction_version。HTML 用段落/DOM 定位；PDF 用页码与区域；表格用 sheet/行列；Office 文档保留段落或表格坐标。OCR 得出的关键金额、日期和否定词设复核标记。

归并分两步：相同字节/近似正文识别转载，同一发布机关与文号/事项识别政策关联。不同批次、地区、方向和费用期间不能因为标题近似而合并。

当前实现（2026-09-24 修正）：尚未落地上面的跨来源事项关系模型。已取消报告中的标题相似度去重，不同 URL 独立保留；相同标题、正文模板或附件均不构成自动合并依据。问答按来源保存引用，不跨来源消除相同文字。后续归并必须记录可核对的事项身份、批次和关系依据，不能恢复成单纯调高相似度阈值。

规则优先级需要显式关系：明确更正/延期通知覆盖它指明的范围；批次指南补充该批次条件；渠道规则只约束对应渠道。没有可验证的覆盖关系时保留冲突，不按“日期更新”盲目覆盖。

同一项目至少分别保存：政策有效期、申报窗口、费用发生窗口、履约/验收期限、抓取时间。只有日期没有时刻时保存粒度和时区，不能擅自补成当天 23:59:59。窗口边界不明则标记待核实。

## 5. 核心数据对象

| 对象 | 关键字段 |
|---|---|
| Source | id, url, publisher, source_role, region, adapter, crawl_interval, last_success_at, access_status |
| Snapshot | id, source_id, fetched_at, final_url, status, content_hash, storage_uri, attachment_manifest |
| DocumentBlock | id, snapshot_id, parent_id, type, locator, text, parser_version, quality_flags |
| Program | id, official_name, issuer, policy_number, support_kind |
| Batch | id, program_id, version, application_window, expense_window, supersedes, evidence_refs |
| Direction | id, batch_id, expense_types, rule_set_version, benefit_formula, material_requirements |
| CompanyFact | company_id, key, value, status, valid_from/to, observed_at, evidence_ref, confirmed_by |
| Expense | company_id, supplier, category, currency, amount, tax_basis, contract/payment/invoice_dates, evidence_refs |
| Evaluation | id, company_id, direction_id, rule_set_version, facts_version, evaluator_version, as_of, inputs_hash, decisions |
| ReviewCase | id, evaluation_id, reason, owner, evidence, resolution, resolved_at, revision |
| Application | id, company_id, direction_id, stage, receipt_ref, claimed_expense_ids, submitted_at |
| Job / Outbox | id, stage, input_refs, idempotency_key, state, attempts, lease_until, payload_version |

support_kind 至少区分财政补贴、服务券、平台折扣、商业云额度和对接活动。折扣或额度不当作现金补贴累加。相同支出可能关联多个候选支持，重复资助条款决定是否互斥。

数据库模型体现三层内容：原始证据、提取候选、经确认事实。公司历史报名文案中的估算保留为 estimate，不升级为 confirmed expense。

## 6. 评估规则与结论

规则采用受限表达式，不执行网页或模型生成的任意代码。支持 all/any/not、相等、集合包含、日期区间、数值门槛和人工判定占位。复杂复合条款不能被展开成一组无条件 AND。

三值逻辑：all 中任一 false 即 false，全部 true 才是 true，否则 unknown；any 中任一 true 即 true，全部 false 才是 false，否则 unknown；not unknown 仍为 unknown。每个叶子保留政策证据和企业证据。

条件还需区分适用方向、资格条件、材料条件和申报前可补救要求。例如不符合注册地是资格问题；缺一份可补交的扫描件是材料准备问题。先过方向选择，再评资格与材料。

推荐使用四个独立状态维度：

- window_status：open / upcoming / closed / unknown。
- eligibility_status：matched / not_matched / unknown。
- evidence_status：complete / partial / conflict / stale。
- application_stage：unstarted / preparing / submitted / approved / rejected / paid。

工作表展示由这些字段推导：

| 展示状态 | 推导条件 |
|---|---|
| 可推进 | 窗口 open、资格 matched、无未解决关键冲突且关键证据新鲜；材料缺口另外列出 |
| 待核实 | 窗口/资格 unknown，或关键证据缺失、冲突、过期 |
| 当前不适用 | 相关方向存在已经证实的不满足条件；保留原因 |
| 等待新批次 | 旧批次已关闭或新批次 upcoming；资格另列，不能暗示一定会有下一批 |

matched 仅表示依据当前已知规则通过系统筛选，不表示主管机构已审核通过。Application 的 approved/paid 只能来自人工确认和对应回执。

模型负责条款抽取、候选归并、非结构化解释和调查问题；规则代码负责可重复的匹配与金额计算。模型不能用自报置信度代替证据，也不能把第二个模型同意当成事实核验。

## 7. 排序与收益

按状态分桶后，桶内按截止紧迫度、现有费用匹配、材料准备成本与可核算收益排序。未知项目单独列“核实收益较高”的任务，避免硬塞到一个总分。

收益按合格费用基数、比例、上限、税额口径和其他限制计算；缺关键费用数据时显示“待测算”。有上下界依据时才出区间。单列现金补贴、抵扣/折扣和资源额度；冲突支出不能叠加。

第一版保留排序因素，不显示伪精确的成功概率或综合分。待累积实际办理记录后，再讨论预测模型。

## 8. 三条重跑路径

**首次发现：** 新候选 URL → 抓取及附件 → 保存快照 → 解析 → 条款候选 → 归并批次 → 企业评估 → 复核 → 工作表草稿。

**政策变化：** 同 URL 附件 hash 改变 → 保存新快照 → 重解析该附件 → 比较受影响条款 → 建新规则版本 → 只重评依赖这些规则的公司/项目 → 提示结论差异。关键事实改变后，旧评估立即标记 stale；新结果未完成前不继续显示无条件“可推进”。

**企业补齐信息：** 财务确认费用类别和付款日期 → 新 CompanyFact/Expense 版本 → 找到依赖这些字段的评估 → 重评 → 更新材料缺口及待办。无需重抓和重读所有政策。

此外，时钟本身触发到期重评：即使网站没有改动，窗口也会过期。ReviewCase 解决后写入事实/规则的新版本，再走相同的评估流程。

人工受理口径作为带联系人角色、日期、批次和范围的证据保存；不能自动提升为跨批次通用规则，和官方文件冲突时保留冲突待澄清。

## 9. 运行机制与模块接口

逻辑模块可以在同一代码库、少数进程里运行。第一版建议应用/API 一个进程；网络/浏览器 Worker 与解析/模型 Worker 分开限并发；定时调度写数据库任务。

任务语义为至少一次处理。stage + input_hash + stage_version 形成幂等键；租约过期可接管；重试采用退避和上限。数据库提交业务状态及后续 outbox 同一事务，避免处理成功但下游任务丢失。模型调用可能被重试，依靠缓存和唯一键去重已保存结果，不承诺外部调用恰好一次。

最小阶段接口：

```text
fetch(candidate_id) -> snapshot_id
parse(snapshot_id, parser_version) -> document_version
extract(document_version, extractor_version) -> rule_set_draft
reconcile(rule_set_draft) -> batch_version, changed_fields
evaluate(company_id, direction_id, rules_version, facts_version, as_of) -> evaluation_id
review(evaluation_id, resolution, expected_revision) -> new_fact_or_rule_version
project(evaluation_id, projection_version) -> worklist_draft
```

抓取失败/附件缺失进入可恢复队列；持续失败产生 ReviewCase。原文不可读时保持 unknown，不用搜索摘要填补关键资格条件。

采集 Worker 只拿公开主题和 URL；财务、联系人等公司信息留在匹配端。网页内容当作数据，不能改变任务目标或工具权限。抓取 URL 限定公网 HTTP(S)、验证跳转目的地并限制附件大小与解析时长。

## 10. 技术选型建议

| 部分 | 第一版建议 | 为什么 |
|---|---|---|
| 应用与 Worker | Python，同一代码库、独立进程入口 | 抽取、解析与评估逻辑放在一起，接口按阶段隔离 |
| 采集 | Crawlee for Python；HTTP 优先，必要时 Playwright | 官方提供 HTTP/浏览器自适应路线；仍需配置各站点质量检查、限额和失败处理 |
| 文档解析 | Docling 为候选；保留专用解析器接口 | 官方支持 PDF/Office/HTML/图片及 OCR；必须用真实中文通知与复杂附件验证日期、表格和定位质量 |
| 规则与契约 | JSON Schema 校验 + 受限规则解释器 | 字段清楚、拒绝缺证据关键条款、便于重放；模型输出结构正确不等于事实正确 |
| 状态与索引 | PostgreSQL | 关系数据、JSON、版本依赖和任务状态统一；第一版无需向量数据库 |
| 任务领取 | 数据库任务表、短事务领取与租约 | 可使用 SKIP LOCKED 避免多个 Worker 争抢；它只用于任务领取，不用于一般业务一致性读 |
| 原文存储 | 开发用持久目录；持续运行用对象存储/持久卷并备份 | 保留原字节、附件版本与证据定位 |
| 人工复核 | 轻量表单 + 证据查看；飞书文档作为阅读出口 | 人员进度与生成内容分区，避免自动同步覆盖人工备注 |

模型供应商、预算和部署位置保留可配置。第一版先量出每个新增项目的浏览器耗时、OCR 页数、模型 token 和人工复核时间，再设预算；不在未试跑前承诺准确率和成本。

工作表同步使用稳定业务 ID 与 block 映射；先生成差异和草稿。后续若获准持续同步，幂等更新系统维护区域；人工负责人、备注和进度独立保存，使用 revision 检查处理并发编辑。当前无远端写入。

## 11. 第一版落地与验收

阶段 A：选择约 15 个有代表性的候选项目，包含过期、延期、属地不符、缺附件、商业券、活动报名和重复转载。先整理一个确认过的公司最小画像，建立少量可信来源适配器。

阶段 B：跑通证据 → 条款 → 评估 → 工作表草稿。人工标注约 30 个场景作为初始验证集；包含符合、不符合、未知及复合条件。开发样本与保留样本分开，防止只记住样例。

阶段 C：验证三条重跑路径和到期变化，再开定时扫描。初始范围跑稳后，逐步导入其余种子、扩大来源和支持类型。

验收建议（目标，不是已测结果）：

- 金额、比例、时限、主体条件必须能回到具体原文位置；关键附件失败不得被悄悄忽略。
- 验证集中已明确过期或不符合硬条件的项目，不能被输出为“可推进”；unknown 不提升为 true。
- 同 URL 替换附件、延期更正、日期跨越截止点、画像更新都能触发正确重评。
- Worker 中断可恢复；重复任务不会重复创建业务记录；历史评估能用固定版本回放。
- 人工能从结论打开证据、看到缺口并指定下一步；原始估算不会被当成确认支出。

运行指标分开统计发现覆盖、采集可用性、关键字段正确率、错误“可推进”率、变化检测延迟、人工复核耗时和单位项目成本。开放互联网的绝对召回率无法直接测量；用固定来源集和人工抽查估计遗漏。

## 12. 一个具体案例（虚构规则，用于解释架构）

通知要求本区法人、指定期间内向非关联方购买推理服务。现有公司资料确认属地，但供应商关联关系和付款日期未知。

系统输出：窗口 open；资格 unknown；两条 ReviewCase；下一步向财务核对供应商与付款凭证。随后确认符合，重评只消耗已存条款与新事实，不重爬全网。若后来发布延期公告，只更新该批次窗口及其依赖评估；若截止时仍未提交，时钟重评把窗口改成 closed。

这个案例验证了系统价值：结论能解释、信息补齐能更新、时间变化不会被漏掉。

## 13. 主要设计取舍

1. Agent 探索有用，但控制在发现链接、补证和提问；每个关键中间结果都持久化，后续用明确接口接续。
2. 原文、批次、公司事实和评估是核心资产。扩大爬取量前先证明评估结论可靠。
3. 采用模块化单体和少数 Worker，待瓶颈出现后拆服务；暂不增加独立消息集群、知识图谱数据库和向量库。
4. 飞书工作表是面向业务负责人的交付视图，底层数据库负责历史与重算。已有资料可以复用，现有文档无需立即被替换。
5. 最重要的待确认输入是公司实际支出结构：买卡、租算力、API 各占多少。这会改变优先采集的来源和评估方向。

## 参考依据

业务资料来自此前已读取的私聊、会议逐字稿和调研文档；本轮仅设计架构，没有重新核实补贴有效性。

- 9 月 20 日会议文字记录（内部飞书文档，未公开）
- 已有专项扫描（内部飞书文档，未公开）
- 151 项政策分析（内部飞书文档，未公开）
- [Crawlee Python 自适应采集示例](https://crawlee.dev/python/docs/examples/adaptive-playwright-crawler)：HTTP 与浏览器路线。
- [Docling 官方文档](https://docling-project.github.io/docling/)与[格式支持](https://github.com/docling-project/docling/blob/main/docs/usage/supported_formats.md)：文档解析与 OCR 候选能力。
- [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html)：SKIP LOCKED 的队列使用场景及一致性限制。
- [React Flow 自定义节点](https://reactflow.dev/learn/customization/custom-nodes)：交互节点与编辑器能力。
- [D2 导出](https://d2lang.com/tour/exports/)：静态图交付。
- [Excalidraw 官方仓库](https://github.com/excalidraw/excalidraw)：白板与草图。

## 行动与协作层（r5）

发现任务在静态栏目无链接时调用私有浏览器桥，成功策略写入 RuntimeState；抓取失败按错误类型冷却。有限研究预算按线索时效和相关性分配，额度耗尽不覆盖上次研究结果。公开证据进入引用校验的资格提取，再进入确定性评估与最多五项行动计划。

Followup 和 SourceRelation 是独立、带乐观版本的协作记录。来源之间可以关联和说明批次，但不会合并归档证据。模型输出仍为 draft；企业资料确认通过 Fact 校验。

独立 Base 同步进程读取五张表的真实结构和全部分页记录，以稳定业务键定位，按数据所有权更新。人工字段通过上次同步基线比较两端变化；双端冲突暂停该条并在 Web 展示，人工选择绑定冲突内容摘要。公司事实及知识编辑进入既有版本历史，报告保留可下载完整稿。
