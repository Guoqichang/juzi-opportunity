# OpenCode 公开来源研究

普通采集负责批量收集；困难来源进入 OpenCode 研究队列。入口在机会雷达的“深入查找原文”。工作台显示研究步骤、取得的来源和未解决的缺口。

## 处理路径

1. HTTP 采集处理 robots 合法跳转、短暂 429/502/503/504 重试、正文和显式附件链接；跨站附件独立校验公网地址与访问规则。
2. OpenCode 1.18.29 通过私网 HTTP API 接收一条公开来源的标题、URL 与抓取错误。沿用本机已有的 aiping / DeepSeek-V4-Flash 提供方配置，只复制该模型的配置。
3. 模型只能调用三个工具：搜索、HTTP 读取、独立 Chromium 渲染。没有 shell、文件编辑、业务数据库或外部提交工具。
4. 工具保存取得的原始字节、正文位置与步骤回执。工作台校验哈希并归档，模型回答和搜索摘要不会直接变成证据。
5. 替代来源建为独立线索，标记“与原事项关系待核对”。找到转载或相似活动不会覆盖原来源、批次、资格和截止日期。
6. 政府页面的 `ArticleTitle` 优先于通用站点标题。原文标题明确标注“失效/废止”时，退出当前申报优先清单；政策解读按资讯保留。搜索摘要不能把已取得原文的失效标记覆盖回去。

## 配额与隔离

- 全站每天最多 8 次深入研究（UTC 零点重置，即北京时间 08:00），每次最多 6 次工具调用、6 轮模型步骤；工具会话 180 秒后不能再启动操作，模型请求超时后中止会话。
- 单 worker 顺序执行，自动队列最多提前排 2 条；同一来源自动研究每天至多一次。普通采集不消耗此配额。
- 研究服务无公网端口。应用 UI 继续免登录；OpenCode 私网 API 使用独立随机口令，不向浏览器暴露。
- OpenCode 容器不挂载生产数据库、公司知识库或 SSH 凭证。模型只收到公开源信息。Chromium 使用全新上下文，所有 HTTP 流量经过公网 DNS 校验与固定 IP 请求；禁用直接 DNS、Service Worker、WebSocket 和非 GET 请求。
- 原文、模型返回和网页内指令均不可改变工具权限；工具预算由程序校验。模型不能自行添加工具或修改配额。

## 部署

在现有 `COMPOSE_FILE` 追加 `compose.opencode.yaml`。先构建同一版本的主应用镜像，再构建 OpenCode 镜像；它继承主应用的采集模块并安装浏览器依赖。`deploy/research` 归 UID 10001 所有；`deploy/secrets/opencode-provider.json` 与 `opencode-password` 只对服务账号和管理员可读，禁止进入镜像和代码归档。

OpenCode 项目目录与运行状态使用独立 Docker 卷。更新研究工具或项目配置时需要同步该工作目录，不能只更新镜像而忽略已存在的工作卷。原始证据入库后移除研究临时字节文件，保留小体积步骤回执；公司事实与原件仍由既有备份机制保存。

当前 DOC 使用 antiword 转文本，表格结构仍待人工核对。扫描 PDF OCR、旧 XLS、ZIP 解包尚未实现。登录、验证码、过期证书和明确禁止访问的来源仍可能失败；研究员可寻找其他公开原文，不能宣称已经恢复原站。

官方文档：[OpenCode Server](https://opencode.ai/docs/server/)、[自定义工具](https://opencode.ai/docs/custom-tools/)、[权限](https://opencode.ai/docs/permissions/)、[最大步骤](https://opencode.ai/docs/agents/#max-steps)、[Playwright 路由](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)。
