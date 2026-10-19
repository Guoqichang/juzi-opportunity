from __future__ import annotations

import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .actions import Actions
from .discovery import IDENTITY_POLICY, Discovery, priority_candidates
from .knowledge import Knowledge
from .research_contracts import ResearchSettings
from .service import Service, record
from .store import Report, now, uid

LABELS = {
    "event": "活动/路演/竞赛",
    "compute_support": "算力与补贴",
    "cloud_credit": "创业云资源",
    "business_support": "企业支持",
    "information": "资讯线索",
    "open_call": "招募/申报通知",
    "program": "政策办法",
    "result_notice": "结果公示",
    "recap": "活动回顾",
    "expired_notice": "原文标注失效/废止",
    "discovered": "等待采集",
    "collected": "原文已归档",
    "blocked": "采集受阻",
}


class Reports:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store

    def list(self, company_id):
        with self.store.session() as session:
            return [
                record(row)
                for row in session.scalars(
                    select(Report)
                    .where(Report.company_id == company_id)
                    .order_by(Report.created_at.desc())
                    .limit(60)
                )
            ]

    def generate(self, company_id, kind="daily", *, at=None, key=None):
        if kind not in {"daily", "weekly", "manual"}:
            raise ValueError("invalid report kind")
        at = at or now()
        if at.tzinfo is None:
            raise ValueError("report time requires timezone")
        config = ResearchSettings.model_validate(Discovery(self.service).config(company_id)["settings"])
        local = at.astimezone(ZoneInfo(config.timezone))
        key = key or f"{company_id}:{kind}:{local.date()}" + (":" + uid() if kind == "manual" else "")
        with self.store.session() as session:
            old = session.scalar(select(Report).where(Report.key == key))
            if old:
                return record(old)
        start = at - timedelta(days=7 if kind == "weekly" else 1)
        work = self.service.worklist(company_id, as_of=at)
        candidates = Discovery(self.service).candidates(company_id)
        runs = Discovery(self.service).runs(company_id)
        runs = [run for run in runs if start <= datetime.fromisoformat(run["started_at"]) <= at]
        old_reports = [
            r
            for r in self.list(company_id)
            if r["kind"] == kind and datetime.fromisoformat(r["period_end"]) <= at
        ]
        previous = old_reports[0]["payload"].get("fingerprints", {}) if old_reports else {}
        fingerprints = {
            item["source_id"]: {
                k: item[k]
                for k in ["bundle_id", "facts_version", "decision", "window_status", "evidence_status"]
            }
            for item in work["items"]
        }
        changes = [
            item
            for item in work["items"]
            if item["source_id"] in previous
            and fingerprints[item["source_id"]] != previous[item["source_id"]]
        ]
        new = [c for c in candidates if start <= datetime.fromisoformat(c["first_seen"]) <= at]
        deadline_hints = []
        for c in candidates:
            for hint in c["hints"].get("dates", []):
                delta = (datetime.fromisoformat(hint["date"]).date() - local.date()).days
                if hint["kind"] == "possible_deadline" and 0 <= delta <= 7:
                    deadline_hints.append({"title": c["title"], "url": c["url"], **hint})
        payload = {
            "actions": Actions(self.service).plan(company_id),
            "identity_policy": IDENTITY_POLICY,
            "new_candidates": new,
            "changes": changes,
            "deadline_hints": deadline_hints,
            "actionable": [item for item in work["items"] if item["decision"] == "可推进"],
            "needs_review": [item for item in work["items"] if item["decision"] == "待核实"],
            "questions": Knowledge(self.service).followups(company_id),
            "runs": runs,
            "fingerprints": fingerprints,
            "coverage": {
                "runs": len(runs),
                "queries": sum(len(r["summary"].get("queries", [])) for r in runs),
                "hubs": sum(len(r["summary"].get("hubs", [])) for r in runs),
                "errors": sum(len(r["summary"].get("errors", [])) for r in runs),
            },
            "facts_version": work["company"]["version"],
            "report_mode": config.report_mode,
        }
        title = {"daily": "每日简报", "weekly": "每周汇总", "manual": "即时报告"}[kind]

        def safe(value):
            return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ")

        lines = [
            f"# {safe(work['company']['name'])} · {title}",
            "",
            f"生成时间：{local.isoformat()}。",
            f"新增相关线索 {len(new)} 条；已复核可推进 {len(payload['actionable'])} 条；需核实 {len(payload['needs_review'])} 条。",
            "",
            "相关性评分用于研究排序，不能代替资格判定。",
            "不同来源分别保留；标题相似不代表同一事项，批次、地区和申报条件需逐条核对。",
            "",
            "## 本期优先行动",
            "",
            *[
                f"- {safe(a['title'])}：{safe(a['next_action'])}；负责人：{safe(a['followup']['owner'] or '待认领')}；{safe(a['url'])}"
                for a in payload["actions"]
            ],
            "",
            "## 新发现的机会",
            "",
            "| 机会 | 类别/文档性质 | 发现依据 | 采集状态 |",
            "| --- | --- | --- | --- |",
        ]
        for c in new:
            lines.append(
                "| "
                + " | ".join(
                    map(
                        safe,
                        [
                            c["title"] + " · " + c["url"],
                            LABELS.get(c["category"], c["category"])
                            + " / "
                            + LABELS.get(c["document_kind"], c["document_kind"]),
                            "；".join(c["reasons"]),
                            LABELS.get(c["state"], c["state"]),
                        ],
                    )
                )
                + " |"
            )
        if not new:
            lines.append("\n本期未记录到新增相关线索；这不代表全网没有机会。")
        lines += ["", "## 建议优先核对", ""]
        all_priority = priority_candidates(candidates, config, limit=len(candidates))
        priority = all_priority[:8]
        payload["priority"] = priority
        payload["priority_total"] = len(all_priority)
        lines.append(
            f"按来源分别排序，优先展示 {len(priority)} / {len(all_priority)} 条；其余可在机会雷达查看。"
        )
        for c in priority:
            lines.append(f"- {safe(c['title'])}：先核实当前批次、报名时间与适用主体；{safe(c['url'])}")
        if not priority:
            lines.append("尚无足够明确的招募或申报线索。")
        lines += ["", "## 已复核可推进", ""]
        lines += [
            f"- {safe(i['title'])}：准备材料并核对官方申报表；{safe(i['source_url'])}"
            for i in payload["actionable"]
        ] or ["当前没有满足全部已建模条件的已复核项目。"]
        lines += ["", "## 重要变化", ""]
        lines += [
            f"- {safe(i['title'])}：{i['decision']}，窗口 {i['window_status']}；{safe(i['source_url'])}"
            for i in changes
        ] or ["暂无与上期相比的已记录变化。"]
        lines += ["", "## 未来七天的疑似截止日期（待核对）", ""]
        lines += [
            f"- {safe(h['title'])}：{h['date']}；原文“{safe(h['quote'])}”；{safe(h['url'])}"
            for h in deadline_hints
        ] or ["未提取到明确的七天内截止线索；未提供日期的项目仍须检查原文。"]
        lines += ["", "## 需要补充的公司资料", ""]
        lines += [f"- {safe(q['question'])}" for q in payload["questions"]] or [
            "当前已建模条件没有新增公司事实缺口。"
        ]
        lines += [
            "",
            "## 采集覆盖与故障",
            "",
            f"本期发现任务 {len(runs)} 次，搜索 {payload['coverage']['queries']} 组，栏目页面 {payload['coverage']['hubs']} 个，失败项 {payload['coverage']['errors']} 个。",
        ]
        for run in runs:
            for error in run["summary"].get("errors", [])[:10]:
                lines.append(f"- {safe(error['stage'])}：{safe(error['url'])}；{safe(error['error'])}")
        if not runs:
            lines.append("本期没有完成发现记录，请检查 worker；不可将此报告解读为完成全网排查。")
        lines += ["", f"公司事实版本：{payload['facts_version']}。本报告未提交任何申请。"]
        with self.store.session() as session:
            row = Report(
                key=key,
                company_id=company_id,
                kind=kind,
                period_start=start.isoformat(),
                period_end=at.isoformat(),
                payload=payload,
                markdown="\n".join(lines) + "\n",
            )
            session.add(row)
            session.flush()
            result = record(row)
        directory = self.store.path / "reports"
        directory.mkdir(exist_ok=True)
        (directory / f"{result['id']}.md").write_text(result["markdown"])
        return result

    def due(self, company_id, at=None):
        at = at or now()
        config = ResearchSettings.model_validate(Discovery(self.service).config(company_id)["settings"])
        local = at.astimezone(ZoneInfo(config.timezone))
        if not config.enabled or local.hour < config.report_hour:
            return []
        kinds = []
        if config.report_mode != "weekly":
            kinds.append("daily")
        if config.report_mode in {"daily_weekly", "weekly"} and local.weekday() == 4:
            kinds.append("weekly")
        return [{"kind": kind, "key": f"{company_id}:{kind}:{local.date()}"} for kind in kinds]
