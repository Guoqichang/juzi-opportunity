# 服务器部署

本目录提供 Linux / Docker Compose 的部署配置。网关监听 80/443，网页和单 worker 共用本机持久数据目录；应用端口不发布到公网。网关提供 HTTPS，按用户要求免登录访问，知道链接即可查看和修改工作台。

部署前需要服务器连接信息、域名和访问范围。若已有反向代理占用 80/443，应接入现有代理；不要停止或覆盖其他站点。

## 数据迁移

`scripts/backup_runtime.py` 使用 SQLite 在线备份接口，随后从备份数据库取得证据文件列表并逐个校验 SHA-256。归档包含数据库、引用的原件和报告，不包含密钥、日志、缓存或旧备份。

```bash
uv run python scripts/backup_runtime.py export --data-dir runtime --output runtime/migration.tar.gz
uv run python scripts/backup_runtime.py verify runtime/migration.tar.gz
```

最终切换时先停止本机 worker、暂停编辑，重新生成最终快照，经 SSH 上传。在服务器上恢复到一个**尚不存在**的新目录；工具拒绝覆盖现有数据。恢复工具只需 Python 3.11+，容器包含 Python 3.12。

```bash
python3 scripts/backup_runtime.py restore incoming/migration.tar.gz --destination deploy/data
```

按容器 UID/GID `10001:10001` 设置数据目录和 `deploy/backups/` 所有权，目录只允许服务及管理员访问。先验证数据库、知识条目、报告和原件数量，再启动服务器 worker，避免本机与服务器持续写成两套数据。

## 服务与域名

1. 将代码放到独立目录，例如 `/srv/juzi-opportunity`；保留上一版本用于回退。
2. 把 `.env.example` 复制成 `deploy/.env`，设置 `POLICY_DOMAIN` 和持久数据绝对路径；启用 OpenCode 研究服务时还要设置 `POLICY_SERVER_IP`（服务器公网 IPv4，研究工具禁止访问它）。服务器优先使用正常系统 DNS。
3. 当前配置不加载账号文件，无需准备登录凭证；全部工作台页面与接口免登录。以后需要身份与成员权限时再配置认证。
4. 检查服务器 80/443 的占用与防火墙；域名 A 记录指向服务器 IPv4。只有已配置并验证 IPv6 时才增加 AAAA；存在 CDN 时另行核对代理与证书方式。
5. 在 `deploy/` 执行 `docker compose --env-file .env config --quiet`、`docker compose build`，然后 `docker compose up -d`。HTTPS 证书要求域名解析和验证端口可达。

线上验证应包含：不携带登录凭证时首页、API、写入及下载正常，且无认证弹窗；跨源写入被拒绝；worker 心跳和实际发现任务成功；重启后数据和报告仍在。Docker 的健康状态不是完整的业务验收。

## 日常运行

```bash
cd /srv/juzi-opportunity/deploy
docker compose ps
docker compose logs --tail=80 web worker gateway
sh backup.sh
```

`juzi-opportunity-backup.service/.timer` 提供每天北京时间 03:30 的备份。安装前检查其中的项目路径；备份目录须由 UID 10001 可写。当前不自动清理备份，需根据实际磁盘容量设置保留与异地复制策略。数据库与原件应作为一份整体恢复。

升级前备份数据，构建一个新的 `POLICY_RELEASE` 镜像标签，再切换服务。回退镜像前检查数据库结构兼容性；需要回退数据时先保留当前数据目录，用备份恢复到新目录，不能覆盖掉上线后的新记录。

Linux 镜像已包含 antiword，支持旧版 DOC 文本提取，表格结构仍需核对；OCR、旧版 XLS 和 ZIP 解包尚未接入。OpenCode 公开来源研究使用独立模型服务，工作台原有资格抽取与问答仍使用规则和证据检索。

## OpenCode 研究服务

研究扩展配置为 `compose.opencode.yaml`，部署细节见 [OPENCODE_RESEARCH.md](../OPENCODE_RESEARCH.md)。先构建主应用，再构建研究镜像；运行时使用独立状态卷、工具工作目录卷与私密提供方配置。首次运行会安装自定义工具依赖，npm 缓存必须放在持久化的 `/state/npm`，避免写满临时目录。

服务只在 Compose 私网监听 4096，没有公网端口。网站保持免登录，私网研究接口的口令仅挂载到 worker 与研究容器。默认配额为每个 UTC 日 8 次；每次最多 6 次工具调用，超时后中止。工具目录更新时同步 `/opt/research` 卷中的配置与 `.opencode/tools`，保留已安装依赖。

升级前执行备份并保留 `.env`。需要撤回此扩展时，恢复上一个 `POLICY_RELEASE` 并从 `COMPOSE_FILE` 移除 `compose.opencode.yaml`，再停止 `opencode` 容器；不要删除数据目录或卷。

参考：[uv 容器安装](https://docs.astral.sh/uv/guides/integration/docker/)、[Compose 启动与健康检查](https://docs.docker.com/compose/how-tos/startup-order/)。

## 本次服务器适配

实际服务器公网 IPv4 为 `203.0.113.10`，目录 `/srv/juzi-opportunity`，正式入口 <https://research.example.com>（公开版均为占位符）。`compose.mydev.yaml` 保留原有 80 端口服务，通过 443 的 TLS-ALPN 完成证书验证，不接管旧站点。2026-09-24 已完成证书签发和公网认证、同源写入、下载验收。

服务器 `.env` 使用 `POLICY_DOMAIN=research.example.com`，OpenCode 扩展后为 `COMPOSE_FILE=compose.yaml:compose.mydev.yaml:compose.opencode.yaml`。DNS A 记录指向 `203.0.113.10`，无 AAAA/CNAME。证书由 Caddy 自动续期；更改 DNS、启用 CDN 或配置 IPv6 前应核对 TLS-ALPN 连通性。

按用户要求已移除 HTTP Basic 认证和凭证文件挂载，原账号不再参与访问校验。临时网关已撤下，`compose.staging.yaml` 留作内部预览回退配置：需要时在 `COMPOSE_FILE` 末尾追加该文件并更新服务，入口将恢复为仅监听 `127.0.0.1:18768`，公网入口随之关闭。本机旧网页与 worker 均已停止，保留的数据只作迁移前快照。
