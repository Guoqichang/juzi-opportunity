# 句子机会研究台 v0.2

支持本机或服务器运行的持续研究 pipeline：**主动搜索 → 官网栏目/正文/附件采集 → 证据留档 → 公司匹配与人工复核 → 定期报告 → 带引用问答 → 申报准备**。搜索不要求用户提供具体通知 URL；范围、搜索词和预算可调整。不依赖 Archify。

正式工作台运行在服务器上，采集、定期报告和备份均已迁移到服务器，本机旧服务已停用。上线记录见 [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md)（公开版已隐去域名与服务器地址）。以下本机命令用于开发或独立试用，不能与服务器数据自动同步。

**网站本身没有登录与角色权限**，知道地址的人都能查看和修改工作台。公网部署时请在网关加认证，或只在内网开放。

## 仓库不包含的内容

仓库只有代码、测试、文档和部署模板。以下内容留在服务器上，部署新环境时需自行准备：

| 内容 | 位置 | 准备方式 |
| --- | --- | --- |
| 运行数据：数据库、原文证据、报告、研究结果、备份 | `deploy/data/`、`deploy/research/`、`deploy/backups/`、`runtime/` | 新环境从空库开始；迁移用 `scripts/backup_runtime.py` 导出与恢复，见 [deploy/README.md](deploy/README.md) |
| 服务器环境变量 | `deploy/.env` | 复制 `deploy/.env.example` 后修改 |
| 飞书 Base 资源标识 | `deploy/base-config.json` | 复制 `deploy/base-config.example.json`，填入 Base token，再用已登录的 lark-cli 运行 `uv run python scripts/configure_base.py` 建表 |
| 飞书机器人凭证 | `deploy/secrets/lark-app.json` | `{"app_id": "…", "app_secret": "…"}` |
| 条款提取模型 | `deploy/secrets/extraction-model.json` | `{"base_url": "…", "api_key": "…", "model": "…"}` |
| OpenCode 模型提供方 | `deploy/secrets/opencode-provider.json` | OpenCode 配置文件，定义 `deploy/opencode/opencode.json` 引用的 `aiping` 提供方及其密钥 |
| 研究服务私网口令 | `deploy/secrets/opencode-password` | 一行随机字符串 |

## 本机启动

```bash
git clone https://github.com/Guoqichang/juzi-opportunity.git
cd juzi-opportunity
uv sync --python 3.12
uv run policy demo
uv run policy serve --port 8766
```

打开 <http://127.0.0.1:8766>。`demo` 建立四条合成政策、一个演示企业，以及所有关键事实都待确认的句子互动档案。演示政策只出现在“演示企业”，不会混入句子互动正式清单；重复执行不会覆盖已编辑的档案。

另开终端运行后台任务；初始化主动研究配置后，worker 会按时间表发现新来源、刷新证据并生成报告：

```bash
cd juzi-opportunity
uv run policy research juzi
uv run policy worker
```

当前机器的代理 DNS 返回 `198.18.*` 虚拟 IP。此环境使用：

```bash
POLICY_DNS_MODE=doh uv run policy worker
```

`doh` 只向固定的 Google Public DNS 服务发送待解析的公开域名。实际连接地址仍须为公网 IP，重定向仍检查域名白名单，TLS 证书验证保持开启。普通网络默认 `system`，不用配置这一项。不改系统网络设置。

## 持续运行与报告

这台 Mac 保留当前项目专用的两个用户级 LaunchAgent 配置，分别运行网页和 worker；迁移服务器后均已停用。需要独立本机开发时才重新安装启动，网页仅监听 `127.0.0.1:8766`。正式服务器状态可在页面右上角查看。

```bash
uv run python scripts/local_service.py status
# 重启/安装；普通网络去掉 --dns-mode doh
uv run python scripts/local_service.py install --dns-mode doh
# 停止两个服务并禁用自动启动
uv run python scripts/local_service.py stop
```

默认每 **6 小时**发现一次；**北京时间每日 09:00 简报，周五 09:00 额外周报**。发现设置可改为每天变化简报或只出周报。报告保存在工作台、SQLite 和 `runtime/reports/`，支持 Markdown 导出；目前没有飞书推送。时间表按日幂等，不因轮询重复出报告。

本机运行时，电脑必须登录、联网且保持唤醒；休眠期间不会执行。正式服务器不依赖这台 Mac 在线。恢复运行后会检查当日已到时间的任务，不补齐关机期间所有历史日报。服务器部署与备份说明见 [deploy/README.md](deploy/README.md)，实际迁移状态见 [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md)。

## 新增的五个工作区

1. **机会雷达**：公共搜索发现新链接、跟踪三个政府栏目并沿可识别的下一页抓取。默认同时关注算力/模型券、企业 AI 活动与竞赛、创业云资源。每轮最多 4 组搜索、6 个栏目页面、10 个正文来源；未采集的线索保留到后续轮次，也可点“采集正文”。
2. **证据与评估**：网页及附件原件归档；资格、申报窗口和公司事实分别核对。相关性分数只用于排序，不能变成“公司符合条件”。
3. **定期报告**：优先核对事项、新增线索、评估变化、未来七天疑似截止日期、公司资料缺口及采集故障。活动回顾、结果公示、疑似已截止线索默认不进入报名推荐。
4. **知识与问答**：维护带来源和版本的公司资料，检索政策原文并返回引用；对缺少的事实逐项提问。当前为本地关键词/中文词片检索与规则回答，**没有接入生成式对话模型**。知识文章不会自动提升成已确认的法人、费用或资质事实。
5. **申报准备**：使用已确认且在有效期内的公司事实预填通用清单；不足项进入问答流程。公司/政策/引用资料版本变化会使旧草稿失效，避免继续下载旧数据。输出 JSON 草稿，尚未操作外部申报网站。

来源与事项分开处理：不同 URL 的线索始终分别保留，报告不再按标题相似度隐藏“转载”。年份、批次、地区或发布单位不同，不能因为标题接近就视为同一事项；完全同名也不自动归并。URL 只去除明确的跟踪参数，保留 `ref/from/spm` 等可能标识正文的参数及重复参数顺序。问答逐来源列出链接，相同引文也保留各自来源。当前没有经人工复核的跨来源事项归并功能；同一事项的转载可能分别出现。

默认研究偏好参考本次任务与[句子公开业务介绍](https://www.juzibot.com/)，关注企业 AI 员工、智能体、客服、知识库与场景对接。官网介绍只保存在参考知识中；正式公司事实仍需内部确认。

## 使用闭环

1. 先在机会雷达点击“立即发现新机会”，也可添加官网通知 URL。服务器登记来源，worker 按队列采集；“刷新”查看结果。`config/sources.json` 是三条真实官网种子，包含政策办法、申报通知和结果公示，**登记不表示可以申请**。
2. 查看原文证据、附件和警告。每段有内容版本对应的 ID；“下载原件”返回原始文件。缺失附件、扫描 PDF、旧版 DOC 的表格结构等会保留警告，不能复核成完整证据。
3. 点击“生成条款草稿”。未配置模型时生成手工填写模板；配置后调用模型。结果始终是草稿，必须在“条款复核”检查资格、时间、逻辑和引用，填写复核人、说明，再保存。
4. 在“编辑公司事实”填已确认的资料。`confirmed` 必须同时有 `value` 和 `evidence`；预算、预计支出用 `estimate`；缺失用 `unknown`。证据说明目前由复核人核验，系统不会验证发票真实性。
5. 查看条件缺口、原文和材料清单，导出 Markdown。当前导出适合进一步整理进飞书文档；**尚未直接写入飞书，也没有申请提交功能**。

当前规则编辑器是 JSON，便于验证复杂条款，面向研究人员/开发者。后续可替换成表单，保留同一后端合同。公司字段由条款决定，引用到的字段在公司档案中不存在时，会产生明确缺口。

## 规则示例

```json
{
  "id": "registered_district",
  "label": "企业注册地须为海淀区",
  "op": "eq",
  "field": "registered_district",
  "value": "海淀区",
  "children": [],
  "evidence": [
    {"block_id": "从证据栏复制实际 ID", "quote": "从对应段落复制连续原文"}
  ]
}
```

复合关系用 `all`、`any`、`not`；比较支持 `eq`、`in`、`gte`、`lte`、`between`；无法确定或表达的条件用 `manual`。不要把例外条款或不明确条件省略。每项硬性条件和申报窗口必须引用当前版本的真实原文才能标为完整。金额单位与核算期间要在字段和条件中明确；当前不自动换算币种、金额单位或推断财务期间。

公司事实示例：

```json
{
  "registered_district": {
    "value": "海淀区",
    "status": "confirmed",
    "evidence": "实际营业执照核验记录（此处仅为格式示例）",
    "valid_from": "2026-01-01",
    "valid_to": null
  }
}
```

不要将上述示例直接作为句子互动的事实。真实档案初始为空，需核实法人主体、注册地、资质、采购类型与期间等。

## 模型接入

`.env.example` 是配置说明；程序**不会自动读取 `.env`**。在运行 worker 的终端导出：

```bash
export POLICY_MODEL_BASE_URL="https://你的服务地址/v1"
export POLICY_MODEL_NAME="你的模型名称"
# 通过本地安全方式设置 POLICY_MODEL_API_KEY；不要提交到代码仓库。
uv run policy worker
```

服务需支持 Chat Completions 和 `response_format: {"type":"json_object"}`。适配器只接收公开政策文本，不接收公司事实；先进行 JSON 合同验证、引用逐字校验，再交人工复核。超过输入预算或未完整结束的输出直接报错，不截断证据后给出确定结论。没有默认选定或付费调用的模型。

实现参考：[OpenAI JSON mode 文档](https://developers.openai.com/api/docs/guides/structured-outputs)。JSON 格式成功不代表条款正确，因此保留独立的规则与证据校验。

## 常用命令

```bash
uv run policy discover juzi
uv run policy research juzi
uv run policy report juzi --kind manual
uv run policy ask juzi --question '有哪些人工智能活动值得核对报名？'
uv run policy knowledge juzi
uv run policy prepare juzi --source-id SOURCE_ID
uv run policy add 'https://官网/通知.html' --title '政策标题'
uv run policy sources
uv run policy crawl SOURCE_ID
uv run policy detail SOURCE_ID
uv run policy extract SOURCE_ID
uv run policy import '/本地/政策.pdf' --title '政策标题'
uv run policy company juzi
uv run policy company juzi --file config/company.example.json
uv run policy evaluate juzi
uv run policy export juzi > runtime/worklist.md
uv run policy jobs
uv run policy worker --once
```

`--data-dir` 是全局参数，例如 `uv run policy --data-dir /tmp/policy-demo demo`。公司更新需传当前 `expected_version`，旧版本写入会被拒绝。复核亦绑定当前政策 ID 和证据包 hash，旧页面不能覆盖新政策。

## 实现结构

| 文件 | 职责 |
| --- | --- |
| `acquire.py` | 域名/公网地址检查、robots、限速、15 MB 文件预算、网页/附件解析 |
| `store.py` | SQLite 数据库、不可变原文文件、版本和执行记录；可配置 SQLAlchemy 数据源 |
| `extract.py` | 可配置模型接口、输出合同、段落引用校验 |
| `contracts.py` / `evaluator.py` | 条款树、三值逻辑、时间窗口、事实有效期、可解释判断 |
| `service.py` | 采集批次、条款复核、事实版本、即时评估与导出 |
| `discovery.py` | 搜索词轮转、栏目翻页、来源去重、分类排序、采集预算、故障记录 |
| `knowledge.py` | 版本化知识、引用检索、追问、事实复用、申报准备与过期检查 |
| `reports.py` | 每日/每周报告、证据变化、截止线索、覆盖与失败说明 |
| `jobs.py` | 持久任务、时间调度、幂等请求、租约/重试、心跳与 worker 单实例锁 |
| `scripts/local_service.py` | 当前项目的 macOS 用户级服务安装、停止和状态检查 |
| `app.py` / `web/` | 本地 API 与工作台 |

网页正文不变时也重新抓附件；附件内容变化会生成新证据包并退回草稿。采集失败不会清空旧原文，也不会继续显示旧的“可推进”。工作清单每次读取都用当前时间、当前事实重算；评估记录仅作审计，不直接作为当前结论缓存。确认事实过期、政策过期、来源超时都会影响新判断。

原文、解析块、证据包、条款修订、公司事实版本、评估记录分开存。原文通过 SHA-256 校验；证据包变化不会覆盖旧版本。`runtime/` 是实际数据，备份应同时包括数据库和 `blobs/`。

## 当前边界

- **单企业、单 worker 原型**。SQLite 已验证；PostgreSQL 仅保留接口。服务器按用户要求免登录访问；尚无应用内角色权限、数据库迁移框架或分布式调度。
- 已接入 DDGS 公共搜索与官网栏目翻页；困难来源可进入 [OpenCode 深入研究](OPENCODE_RESEARCH.md)，由搜索、HTTP 读取和独立 Chromium 渲染工具寻找实际原文。默认每日最多 8 次，每次最多 6 次工具调用。替代来源独立保留，关联关系待核对。搜索服务仍可能失败，覆盖率不等于全网召回率。
- 支持正文显式链接的跨站附件；macOS 使用 textutil、Linux 镜像使用 antiword 提取旧版 DOC 文字，表格结构须核对。尚无扫描 PDF OCR、旧版 XLS 和 ZIP 解包。登录、验证码、robots 限制、证书失败仍会作为缺口记录。
- 支持政策类型与材料列表，但金额公式、可获补金额、政策叠加、跨文件法律优先级由人工确认。结果公示或活动信息不能仅凭标题当成申报依据。
- 公司事实更新现在重算全部已登记政策，适合早期规模；未实现按字段依赖选择性重算。负责人、推进记录、飞书推送与文档同步属于下一阶段。
- 当前缺真实公司的已确认档案和模型服务配置，正式来源保持待复核。合成演示能演示完整闭环，但不能证明真实项目可申请。

## 验证

```bash
uv run ruff check src tests
uv run pytest -q
node --check src/compute_policy/web/app.js
node --check src/compute_policy/web/features.js
```

测试覆盖：三值逻辑、边界时间、估算事实、附件同 URL 更新、旧版复核拒绝、引用造假、缺附件、来源失败/过期、任务租约与重试、模型截断、跨源写请求拒绝、Office 表格定位、原件下载和导出。实网和界面验证记录见 `IMPLEMENTATION_VALIDATION.md`。

## 下一步如何扩展

先用真实的法人档案、资质和费用期间核验重点项目；持续统计错误推荐、遗漏条件与采集受阻来源。此后增加浏览器渲染/OCR、可靠搜索 API、语义检索和生成式问答，再将报告接入飞书。

外部申报按平台增加独立适配器：官方表单字段 → 公司事实/材料版本 → 缺口问答 → 草稿预填 → 人工核对 → 提交回执。不能把一个通用草稿当作已完成官方申报。当前没有登录账号、传递公司敏感资料或提交申请。

飞书 Base 双向协作和五项优先行动已加入，部署配置、可编辑字段及同步边界见 [FEISHU_SYNC.md](FEISHU_SYNC.md)。
