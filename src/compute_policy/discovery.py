from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update

from .acquire import Fetcher, normalize
from .research_contracts import Hub, ResearchSettings, SettingsUpdate
from .service import Conflict, Service, record
from .store import Candidate, DiscoveryRun, ResearchConfig, RuntimeState, digest, now, stamp

IDENTITY_POLICY = "separate-source-urls-v1"


def defaults():
    return ResearchSettings(
        queries=[
            "北京 算力券 申报 {year}",
            "北京 人工智能 活动 报名 {year}",
            "海淀 创业大赛 {year}",
            "创业公司 云 资源 扶持 {year}",
            "奇绩 创业 算力 {year}",
            "海淀 模型券 申报 {year}",
            "北京 企业 智能体 场景 征集 {year}",
            "北京 企业 人工智能 路演 报名 {year}",
        ],
        hubs=[
            Hub(url="https://kw.beijing.gov.cn/zwgk/tzgg/", label="北京科委通知公告"),
            Hub(url="https://www.beijing.gov.cn/fuwu/lqfw/gggs/", label="北京利企服务公告"),
            Hub(url="https://zyk.bjhd.gov.cn/zwdt/xxgk/tzgg/", label="海淀通知公告"),
        ],
    )


def canonical(url):
    parsed = urlsplit(normalize(url))
    # Only known analytics parameters are disposable. Generic ref/from/spm values and
    # repeated parameter order may identify different documents or application batches.
    query = [
        part
        for part in parsed.query.split("&")
        if not unquote_plus(part.partition("=")[0]).lower().startswith(("utm_", "_vtm_"))
    ]
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", "&".join(query), ""))


def is_trusted(url, domains):
    host = urlsplit(url).hostname or ""
    return host.endswith(".gov.cn") or any(host == d or host.endswith("." + d) for d in domains)


def classify(title):
    if any(w in title for w in ["失效", "废止", "已撤销"]):
        kind = "expired_notice"
    elif "政策解读" in title:
        kind = "information"
    elif (
        "公示" in title
        or re.search(r"(公示|公布).*(名单|结果)|入围.*名单", title)
        or any(
            w in title for w in ["拟支持", "获奖名单", "资格审核", "入围名单", "结果公示", "拟立项", "拟认定"]
        )
    ):
        kind = "result_notice"
    elif any(
        w in title
        for w in ["圆满", "成功举办", "回顾", "精彩回放", "顺利举办", "收官", "亮相路演", "完整名单"]
    ):
        kind = "recap"
    elif any(w in title for w in ["报名", "征集", "申报", "启动", "发放", "招募"]):
        kind = "open_call"
    elif any(w in title for w in ["办法", "政策", "措施", "方案"]):
        kind = "program"
    else:
        kind = "information"
    if any(w in title for w in ["活动", "大赛", "竞赛", "路演", "对接", "峰会", "沙龙", "大会", "招募"]):
        category = "event"
    elif any(w in title for w in ["云额度", "云资源", "创业计划", "Activate", "startup", "初创"]):
        category = "cloud_credit"
    elif any(w in title for w in ["算力", "模型券", "创新券", "补贴"]):
        category = "compute_support"
    elif any(w in title for w in ["创业", "扶持", "支持项目", "支持申报", "项目申报"]):
        category = "business_support"
    else:
        category = "information"
    return category, kind


def rank(title, snippet, url, settings):
    text = title + " " + snippet
    matched = [term for term in settings.topics if term.lower() in text.lower()]
    strong = [term for term in settings.topics if term.lower() in title.lower()]
    excluded = [term for term in settings.exclude_terms if term in title]
    reasons = ["主题：" + term for term in matched]
    if excluded:
        return -100, ["与研究范围不符：" + "、".join(excluded)]
    if not matched:
        return 0, []
    score = len(strong) * 12 + len(matched) * 3
    if any(term in text for term in settings.regions if term != "全国"):
        score += 5
        reasons.append("地区与研究范围相关")
    if is_trusted(url, settings.trusted_domains):
        score += 8
        reasons.append("政府或已配置的发布方域名")
    _, kind = classify(title)
    if kind == "open_call":
        score += 8
    elif kind in {"recap", "result_notice", "expired_notice"}:
        score -= 15
        reasons.append("结果/回顾/失效线索，不能当作开放报名")
    years = [int(y) for y in re.findall(r"\b(20\d{2})年?", title)]
    if years and max(years) < now().year:
        score -= 10
        reasons.append("标题含往年年份，需检查批次")
    return max(1, score), reasons


def date_hints(blocks):
    results = []
    pattern = re.compile(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?")
    for block in blocks:
        text = block["text"]
        for match in pattern.finditer(text):
            excerpt = text[max(0, match.start() - 45) : match.end() + 55]
            if not any(word in excerpt for word in ["申报", "报名", "截止", "举办", "召开", "征集"]):
                continue
            try:
                value = date(*map(int, match.groups())).isoformat()
            except ValueError:
                continue
            before = text[max(0, match.start() - 20) : match.start()]
            is_deadline = bool(re.search(r"(?:截至|截止)(?:日期|时间)?[为至到：:\s]*$", before)) or bool(
                re.match(r"[\d:：时点分\s]*前", text[match.end() :])
            )
            hint = {
                "date": value,
                "kind": "possible_deadline" if is_deadline else "mentioned_date",
                "block_id": block["id"],
                "quote": excerpt,
                "verified": False,
            }
            if hint not in results:
                results.append(hint)
    return results[:12]


def priority_candidates(candidates, settings, limit=8):
    eligible = [
        c
        for c in candidates
        if c["document_kind"] in {"open_call", "program"}
        and c["deadline_hint_status"] != "possibly_past"
        and c["state"] != "blocked"
    ]
    eligible.sort(
        key=lambda c: (
            c["state"] != "collected",
            not is_trusted(c["url"], settings.trusted_domains),
            -c["score"],
        )
    )
    unique, urls = [], set()
    for candidate in eligible:
        # A source document is not an event identity. Similar (even identical) titles,
        # template text and shared attachments cannot establish that two events match.
        url = canonical(candidate["url"])
        if url in urls:
            continue
        urls.add(url)
        unique.append(candidate)
    result = []
    for category in ["compute_support", "event", "cloud_credit", "business_support"]:
        first = next((c for c in unique if c["category"] == category), None)
        if first:
            result.append(first)
    selected = {c["id"] for c in result}
    result.extend(c for c in unique if c["id"] not in selected)
    return result[:limit]


class Discovery:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store

    def config(self, company_id):
        if company_id == "demo":
            raise ValueError("演示档案不启用主动研究")
        self.service.company(company_id)
        with self.store.session() as session:
            row = session.get(ResearchConfig, company_id)
            if row is None:
                row = ResearchConfig(company_id=company_id, settings=defaults().model_dump(mode="json"))
                session.add(row)
                session.flush()
            return record(row)

    def save_config(self, company_id, data: SettingsUpdate):
        self.config(company_id)
        for hub in data.settings.hubs:
            normalize(hub.url)
        with self.store.session() as session:
            count = session.execute(
                update(ResearchConfig)
                .where(
                    ResearchConfig.company_id == company_id, ResearchConfig.version == data.expected_version
                )
                .values(settings=data.settings.model_dump(mode="json"), version=data.expected_version + 1)
            ).rowcount
            if count != 1:
                raise Conflict("研究配置已更新，请刷新")
        return self.config(company_id)

    def candidates(self, company_id, *, limit=500):
        with self.store.session() as session:
            rows = list(
                session.scalars(
                    select(Candidate)
                    .where(Candidate.company_id == company_id)
                    .order_by(Candidate.score.desc(), Candidate.first_seen.desc())
                    .limit(limit)
                )
            )
            results = []
            today = now().astimezone(ZoneInfo("Asia/Shanghai")).date()
            for row in rows:
                deadlines = [
                    date.fromisoformat(hint["date"])
                    for hint in row.hints.get("dates", [])
                    if hint["kind"] == "possible_deadline"
                ]
                temporal = (
                    "possibly_past" if deadlines and all(d < today for d in deadlines) else "unverified"
                )
                results.append({**record(row), "deadline_hint_status": temporal})
            return results

    def runs(self, company_id):
        with self.store.session() as session:
            return [
                record(row)
                for row in session.scalars(
                    select(DiscoveryRun)
                    .where(DiscoveryRun.company_id == company_id)
                    .order_by(DiscoveryRun.started_at.desc())
                    .limit(30)
                )
            ]

    def register_candidate(self, company_id, candidate_id):
        self.config(company_id)
        with self.store.session() as session:
            row = session.get(Candidate, candidate_id)
            if row is None or row.company_id != company_id:
                raise KeyError("candidate not found")
            url, title = row.url, row.title
        source = self.service.add_source(url, title)
        with self.store.session() as session:
            session.get(Candidate, candidate_id).source_id = source["id"]
        return source

    def refresh_source(self, source_id, *, document_title=None):
        detail = self.service.detail(source_id)
        failed = detail["source"]["health"] == "failed"
        with self.store.session() as session:
            for row in session.scalars(select(Candidate).where(Candidate.source_id == source_id)):
                row.state = "blocked" if failed else "collected"
                if (
                    not failed
                    and document_title
                    and (classify(document_title)[1] != "information" or "政策解读" in document_title)
                ):
                    row.title = document_title
                    row.category, row.document_kind = classify(document_title)
                    row.hints = {**row.hints, "document_title": document_title}
                row.hints = {
                    **row.hints,
                    "dates": date_hints(detail["blocks"]),
                    "warnings": detail["warnings"],
                    "bundle_hash": detail["bundle"]["content_hash"] if detail["bundle"] else None,
                    "block_count": len(detail["blocks"]),
                    "collected_at": detail["source"]["last_success"],
                    "error": detail["source"]["error"] if failed else "",
                }

    def observe(self, company_id, url, title, snippet, origin, settings):
        try:
            url = canonical(url)
        except ValueError:
            return None, False
        title = re.sub(r"\s+", " ", title).strip()[:500]
        if len(title) < 6:
            return None, False
        score, reasons = rank(title, snippet[:1200], url, settings)
        if score <= 0:
            return None, False
        category, kind = classify(title)
        with self.store.session() as session:
            row = session.scalar(
                select(Candidate).where(Candidate.company_id == company_id, Candidate.url == url)
            )
            created = row is None
            if created:
                row = Candidate(
                    company_id=company_id,
                    url=url,
                    title=title,
                    score=score,
                    reasons=reasons,
                    category=category,
                    document_kind=kind,
                    origins=[origin],
                    hints={"search_snippet": snippet[:1200]},
                )
                session.add(row)
            else:
                row.last_seen = stamp()
                row.origins = (row.origins + [origin])[-20:] if origin not in row.origins else row.origins
                row.score, row.reasons = score, reasons
                row.category, row.document_kind = classify(row.hints.get("document_title", title))
            session.flush()
            return record(row), created

    def run(self, company_id, *, searcher=None, fetcher_factory=Fetcher, renderer=None):
        from .browser_bridge import configured as browser_configured
        from .browser_bridge import render_remote

        renderer = renderer or (render_remote if browser_configured() else None)
        settings = ResearchSettings.model_validate(self.config(company_id)["settings"])
        if not settings.enabled:
            raise ValueError("主动发现当前已暂停")
        summary = {
            "queries": [],
            "hubs": [],
            "new_candidates": 0,
            "seen_candidates": 0,
            "collected": 0,
            "errors": [],
            "changed_sources": [],
            "budget": settings.model_dump(mode="json"),
        }
        with self.store.session() as session:
            previous_count = session.scalar(
                select(func.count()).select_from(DiscoveryRun).where(DiscoveryRun.company_id == company_id)
            )
        with self.store.session() as session:
            run = DiscoveryRun(company_id=company_id)
            session.add(run)
            session.flush()
            run_id = run.id
        observed = set()

        def observe(url, title, snippet, origin):
            row, created = self.observe(company_id, url, title, snippet, origin, settings)
            if row:
                observed.add(row["id"])
                summary["new_candidates"] += int(created)

        try:
            # Public-topic queries only; company financial facts never enter the search request.
            if settings.search_enabled and settings.queries:
                if searcher is None:
                    from ddgs import DDGS

                    def searcher(query):
                        return DDGS(timeout=18).text(query, region="cn-zh", max_results=8, backend="bing")

                start = (previous_count * settings.max_queries) % len(settings.queries)
                queries = (settings.queries + settings.queries)[
                    start : start + min(settings.max_queries, len(settings.queries))
                ]
                for template in queries:
                    query = template.replace("{year}", str(now().year))
                    try:
                        results = list(searcher(query))[:8]
                        summary["queries"].append({"query": query, "results": len(results), "state": "ok"})
                        for row in results:
                            observe(
                                row.get("href", ""),
                                row.get("title", ""),
                                row.get("body", ""),
                                {"type": "search", "query": query, "run_id": run_id},
                            )
                    except Exception as error:
                        summary["queries"].append({"query": query, "results": 0, "state": "failed"})
                        summary["errors"].append({"stage": "search", "url": query, "error": str(error)[:400]})
            frontier = [(hub.url, hub.label, 0) for hub in settings.hubs]
            visited = set()
            while frontier and len(visited) < settings.max_hub_pages:
                url, label, depth = frontier.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                host = urlsplit(url).hostname
                fetcher = fetcher_factory([host])
                try:
                    strategy_key = "hub-strategy:" + digest(url)
                    with self.store.session() as session:
                        strategy = session.get(RuntimeState, strategy_key)
                        prefer_browser = bool(strategy and strategy.value.get("browser"))
                    method = "http"
                    page = None
                    if renderer and prefer_browser:
                        try:
                            page = renderer(url)
                            method = "browser"
                        except Exception as error:
                            summary["errors"].append(
                                {"stage": "browser", "url": url, "error": str(error)[:300]}
                            )
                    if page is None:
                        page = fetcher.fetch(url)
                    if renderer and not page.links and method != "browser":
                        page = renderer(url)
                        method = "browser"
                    if page.links and method == "browser":
                        with self.store.session() as session:
                            session.merge(
                                RuntimeState(key=strategy_key, value={"browser": True}, updated_at=stamp())
                            )
                    summary["hubs"].append(
                        {
                            "url": url,
                            "label": label,
                            "links": len(page.links),
                            "state": "ok" if page.links else "empty",
                            "method": method,
                        }
                    )
                    if not page.links:
                        summary["errors"].append(
                            {
                                "stage": "hub",
                                "url": url,
                                "error": "未解析到链接；可能需要浏览器渲染或栏目适配",
                            }
                        )
                    for link in page.links:
                        observe(
                            link["url"],
                            link["text"],
                            "",
                            {"type": "hub", "url": url, "run_id": run_id, "method": method},
                        )
                        same_host = urlsplit(link["url"]).hostname == host
                        if (
                            depth < 2
                            and same_host
                            and ("下一页" in link["text"] or re.search(r"index_\d+\.(s?html?)$", link["url"]))
                        ):
                            frontier.append((link["url"], label, depth + 1))
                except Exception as error:
                    summary["errors"].append({"stage": "hub", "url": url, "error": str(error)[:400]})
                finally:
                    fetcher.close()
            summary["seen_candidates"] = len(observed)
            candidates = [
                c for c in self.candidates(company_id) if c["id"] in observed or c["state"] == "discovered"
            ]
            from .actions import crawl_due

            source_rows = {s["id"]: s for s in self.service.sources()}
            candidates = [
                c for c in candidates if not c["source_id"] or crawl_due(source_rows[c["source_id"]])
            ]
            # Spend the body-collection budget on relevant leads, with uncollected leads first.
            candidates.sort(
                key=lambda c: (
                    c["state"] == "collected",
                    c["document_kind"] not in {"open_call", "program"},
                    -c["score"],
                )
            )
            diversified = []
            for category in ["event", "compute_support", "cloud_credit", "business_support"]:
                diversified.extend([c for c in candidates if c["category"] == category][:2])
            selected_ids = {c["id"] for c in diversified}
            diversified.extend(c for c in candidates if c["id"] not in selected_ids)
            for candidate in diversified[: settings.max_collect]:
                source = self.service.add_source(candidate["url"], candidate["title"])
                with self.store.session() as session:
                    session.get(Candidate, candidate["id"]).source_id = source["id"]
                try:
                    fetcher = fetcher_factory(source["hosts"])
                    try:
                        result = self.service.crawl(source["id"], fetcher=fetcher)
                    finally:
                        fetcher.close()
                    detail = self.service.detail(source["id"])
                    hints = {
                        "dates": date_hints(detail["blocks"]),
                        "warnings": detail["warnings"],
                        "bundle_hash": result["bundle_hash"],
                        "block_count": len(detail["blocks"]),
                        "collected_at": stamp(),
                    }
                    with self.store.session() as session:
                        row = session.get(Candidate, candidate["id"])
                        row.state, row.hints = "collected", {**row.hints, **hints}
                    summary["collected"] += 1
                    if result["changed"]:
                        summary["changed_sources"].append(source["id"])
                except Exception as error:
                    with self.store.session() as session:
                        row = session.get(Candidate, candidate["id"])
                        row.state = "blocked"
                        row.hints = {**row.hints, "error": str(error)[:500]}
                    summary["errors"].append(
                        {"stage": "collect", "url": candidate["url"], "error": str(error)[:400]}
                    )
            summary["budget_deferred"] = max(0, len(candidates) - settings.max_collect)
        except Exception as error:
            summary["errors"].append({"stage": "run", "url": "", "error": str(error)[:500]})
            raise
        finally:
            with self.store.session() as session:
                row = session.get(DiscoveryRun, run_id)
                row.state = "partial" if summary["errors"] else "done"
                row.summary, row.finished_at = summary, stamp()
        return {"id": run_id, **summary}

    def due(self, at=None):
        at = at or now()
        with self.store.session() as session:
            configs = list(session.scalars(select(ResearchConfig)))
        result = []
        for config in configs:
            settings = ResearchSettings.model_validate(config.settings)
            runs = self.runs(config.company_id)
            if settings.enabled and (
                not runs
                or at - datetime.fromisoformat(runs[0]["started_at"])
                >= timedelta(hours=settings.interval_hours)
            ):
                result.append(config.company_id)
        return result
