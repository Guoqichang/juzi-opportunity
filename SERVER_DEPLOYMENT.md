# 服务器部署记录

> 公开版说明：域名、服务器地址与目录已换成占位符（`research.example.com`、`203.0.113.10`、`/srv/juzi-opportunity`）；文中提到的 `runtime/` 验收记录、`deploy/.env*` 备份和数据目录不在仓库中。

2026-09-24。已上线：**[句子机会研究台](https://research.example.com)**。DNS、真实 HTTPS 证书与公网读写均已验收。上线后按用户要求移除账号密码，当前免登录访问。

## 已部署

- 服务器：公网 IPv4 `203.0.113.10`，Ubuntu 24.04 / Docker Compose。
- 项目：`/srv/juzi-opportunity`；数据：`deploy/data`；备份：`deploy/backups`。
- 当前应用镜像：`juzi-opportunity:v0.2.0-server-20260924-r4`，摘要 `sha256:7d2b1fcf3ea8dbe9aa15e6bffe37c85d46c4857cf512c44c0ba109f0c4830afe`。
- 研究镜像：`juzi-opencode:v0.2.0-server-20260924-r4`，摘要 `sha256:2b1853c04552722d2d732bfec36124099f42f0312cdb8c29a5674a8e4546d445`。包含 OpenCode 1.18.29 与 Chromium，自定义工具仅用于公开来源调查。
- 四个服务：web、单 worker、Caddy 网关、OpenCode 研究服务。web 只监听 `127.0.0.1:18766`；网关通过公网 443 提供 HTTPS；OpenCode 没有公网端口。原 80 端口站点保持原进程运行，临时 18768 入口已撤下。
- 公司资料、知识库、证据、报告、任务和 54 条原线索已通过一致性备份迁移；服务器首轮达到 67 条，r3 验收时共 74 条。
- 每 6 小时扫描、每天北京时间 09:00 简报、周五 09:00 周报沿用原配置。
- 备份定时器 `juzi-opportunity-backup.timer` 已启用，每天北京时间 03:30 后随机延迟最多 5 分钟；首次执行成功。备份在同机，尚未配置异地副本。
- 本机原网页和 worker 均已停止并禁用自动启动，避免两边独立编辑与扫描。本机数据保留为迁移前快照，后续编辑统一进入服务器工作台。

## 已验证

首次部署时 83 项本地自动化测试通过，r3 增至 94 项。实际 Linux 镜像构建与启动通过；初始认证模式下未登录 401、跨源写入 403、登录后首页/脚本/样式/问答数据/报告生成与下载正常；当前免登录访问另经验证。web 和 worker 替换镜像重启后资料和报告仍在，心跳正常。Docker 已设为开机启动，容器配置了自动重启。

服务器实际运行一轮搜索与采集：四组搜索均返回结果，新增 13 条、归档 7 个来源，其余故障保留在运行记录。发现系统 DNS 优先选择不可达 IPv6 的问题后，加入经过公网地址校验的 IPv4 优先与有限连接回退；重新实抓北京利企服务栏目成功，返回 27569 字节、29 个链接，未关闭 TLS 验证。

首次公网验收时间：2026-09-24 08:30（北京时间）。DNS A 指向 `203.0.113.10`，无 AAAA/CNAME。TLS 1.3 握手通过，Let's Encrypt 证书域名匹配，有效期至 2026-12-22 23:30:46 UTC，由 Caddy 自动续期。初始账号保护下的登录、跨源写入拦截、报告生成与下载均通过；随后按用户要求移除认证，最新免登录验收见 `runtime/server-public-noauth-validation.json`。

Linux 已安装 antiword，可提取旧版 DOC 文字，表格结构仍需核对；OCR、旧版 XLS 和 ZIP 解包尚未实现。OpenCode 研究服务已使用独立模型，问答仍使用证据检索与规则回答；申报输出草稿，不执行外部提交。

## 来源误归并修复（r3）

2026-09-24 09:01（北京时间）完成上线验收。移除报告优先清单的 88% 标题相似度过滤：真实数据中的经开区 2026 年上半年与 2025 年下半年算力券通知，此前相似度约 94.9%，后者会被当作转载隐藏；现在分别保留。数据库原始记录未合并、未删除，无需拆分迁移。URL 保留可能区分正文的参数；问答相同文字的引用也按来源独立保留。页面显示来源域名，旧报告显示旧版去重提示。

94 项测试、Ruff 与 JS 语法检查通过；公网 API 验证全部 74 条原始线索仍在、两批次对应不同来源 ID、定向问答只引用所选来源、worker 心跳正常。新报告 `0e1d3a28384e484897d04654e51eb74e` 已生成并下载校验；符合优先核对筛选的线索共 25 条，摘要显示前 8 条，剩余线索仍在雷达中。未改动资格或截止筛选规则。验收记录：`runtime/server-identity-validation.json`。本轮浏览器自动化因本机窗口变化未完成视觉验收，页面 HTML/JS 和接口已验证。

升级前已执行数据备份，保留 r2 镜像及 `deploy/.env.before-identity-r3`；本次无数据库结构变更。跨来源真实事项归并尚未实现，转载可能分别出现，后续需以发布机关、文号、批次和原文关系为依据再关联。

## 采集与 OpenCode 扩展（r4）

2026-09-24 09:43（北京时间）完成业务验收。106 项测试通过，真实 OpenCode 模型调用、工具回执、原文入库、报告生成与下载均已验证。升级前备份成功，保留 r3 镜像及 `deploy/.env.before-opencode-r4`；无数据库结构变更。

公开线索从 74 条增至 75 条。jlts、techchn 和搜狐三个受阻来源均由研究服务找到可实际抓取的替代来源，原来源与替代来源分别保留、关系待核对。官方海淀模型券指南中的“失效”标记已显示，并从当前申报优先清单排除。普通采集实测中，中小企业服务平台跨站 DOC 与经开区上半年算力券 DOC 各提取 74 个文字块；北京市政策页恢复为健康，App 推广链接不再造成假附件故障。techchn 原站仍断开连接，一份经开区 DOC 仍转换失败，均保留真实缺口。

独立 Chromium 对海淀通知公告栏目实测取得 42 个文字块与 84 个链接，同时记录 3 个未加载子资源。该结果验证了渲染工具，不代表所有栏目已自动切换为浏览器扫描。网页界面实测按钮与研究步骤正常、无 JavaScript 错误，截图在 `runtime/juzi-opencode-radar.png` 和 `runtime/juzi-opencode-result.png`。

新报告：`a70503f8df4a4b089a0d926da51af06d`。验收记录：`runtime/server-opencode-validation.json`。研究服务细节、配额和尚未实现的格式支持见 [OPENCODE_RESEARCH.md](OPENCODE_RESEARCH.md)。

## 访问与运维

正式入口：<https://research.example.com>。全部页面和接口免登录，知道链接即可查看和修改工作台；尚无应用内成员角色。网关已移除 Basic 认证及凭证文件挂载，旧账号不再参与访问校验。HTTPS 与同源写入检查保留；同源检查不代表身份认证。

服务器 `.env` 已配置：

```bash
COMPOSE_FILE=compose.yaml:compose.mydev.yaml:compose.opencode.yaml
POLICY_DOMAIN=research.example.com
```

TLS-ALPN 使用 443，不接管旧站点的 80 端口，因此请使用完整 HTTPS 地址。旧环境文件已在服务器项目内备份；`compose.staging.yaml` 保留用于需要时恢复仅本机监听的临时网关。DNS 以后仍需直接指向本服务器；启用 CDN 或更改解析前需重新核对证书验证方式。

详细命令与回退方式见 [deploy/README.md](deploy/README.md)，机器可读验收记录在 `runtime/server-validation.json` 和 `runtime/server-public-validation.json`，服务器生成的报告副本在 `runtime/server-latest-report.md`。

## r5 · 2026-09-26

线上版本 `v0.3.0-server-20260926-r5`，追加 `compose.base.yaml`。Web、worker、OpenCode、Base 同步容器均在现有专用 Compose 网络中，只有原 Caddy 暴露 HTTPS；80 端口上的既有服务未变。网站沿用免登录设置。

组件镜像验收：
- Web/worker：`sha256:2c6431066b26073aecca975a2fd40cde288ccd9e1cf6203f5d13f1c46838046e`
- OpenCode/浏览器桥：`sha256:cf483ca45e92719b0748416c16946619eda23df169e4bd48a732c4e4f88c9bc0`
- Base 同步：`sha256:083faa42cc91f1c513efb99fa854d651c98bf944ec03a93fd64951f11835b0d1`

r5 Python/Chromium/OpenCode 依赖与 r4 相同，迭代时使用 `Dockerfile.app-update` 和 `Dockerfile.opencode-update` 复用已验收环境；从零构建仍走 `Dockerfile`、`Dockerfile.opencode`、`Dockerfile.base`。升级依赖时不得使用 r4 增量构建路径。

同步机器人编辑权限仅授予本次创建的 Base；授权未触发协作消息通知。机器人持有的应用凭证只位于同步容器只读密钥挂载，CLI 运行配置位于 tmpfs。模型提取凭证单独挂载到 worker。

回滚配置保存在 `deploy/.env.pre-r5`；r4 镜像保留。回滚时先停止 Base 同步，恢复旧 `.env` 并重建对应服务。新表、同步基线和公司资料版本保留在 SQLite，r4 不会使用新增表。仅在需要恢复数据本身时使用完整一致性备份，避免单独回退同步基线。

发布前备份：`policy-20260926T134818Z.tar.gz`。发布后再次执行备份服务。自动备份仍为同机备份，未改为异地存储。
