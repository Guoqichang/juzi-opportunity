"""Feishu Base bridge via the official CLI; generated fields and human edits have separate ownership."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

from .actions import Actions, FollowupInput, RelationInput
from .contracts import CompanyInput, Contract, Fact
from .discovery import Discovery
from .knowledge import Knowledge
from .reports import Reports
from .research_contracts import FIELD_QUESTIONS, KnowledgeInput
from .service import Conflict, Service
from .store import RuntimeState, Store, digest, stamp

SCHEMAS = {
    "opportunities": (
        "机会与行动",
        "线索ID",
        [
            "机会名称",
            "线索ID",
            "原文链接",
            "类别",
            "原文状态",
            "批次",
            "资格结论",
            "申报窗口",
            "支持摘要",
            "材料要求",
            "资格缺口",
            "原文依据",
            "下一步行动",
            "工作台链接",
            "跟进状态",
            "负责人",
            "跟进备注",
        ],
        ["跟进状态", "负责人", "跟进备注"],
    ),
    "relations": (
        "来源关系",
        "关系ID",
        ["关系名称", "关系ID", "来源一", "来源二", "关系判断", "项目名称", "批次", "判断依据"],
        ["关系判断", "项目名称", "批次", "判断依据"],
    ),
    "facts": (
        "公司资料与缺口",
        "资料键",
        ["资料名称", "资料键", "待回答问题", "资料值", "确认状态", "依据说明", "有效起日", "有效止日"],
        ["资料值", "确认状态", "依据说明", "有效起日", "有效止日"],
    ),
    "knowledge": (
        "知识资料",
        "资料键",
        ["资料标题", "资料键", "资料正文", "资料来源", "资料状态"],
        ["资料标题", "资料正文", "资料来源", "资料状态"],
    ),
    "reports": (
        "研究报告",
        "报告ID",
        ["报告名称", "报告ID", "生成时间", "优先行动", "报告正文", "完整报告"],
        [],
    ),
}


def config():
    path = os.getenv("POLICY_BASE_CONFIG")
    return json.loads(Path(path).read_text()) if path else None


def get_state(service, key):
    with service.store.session() as session:
        row = session.get(RuntimeState, key)
        return row.value if row else {}


def put_state(service, key, value):
    with service.store.session() as session:
        session.merge(RuntimeState(key=key, value=value, updated_at=stamp()))


def public_status(service):
    cfg = config()
    return {"enabled": bool(cfg), "url": cfg.get("url") if cfg else None, **get_state(service, "base-sync")}


class SyncResolution(Contract):
    table: Literal["opportunities", "relations", "facts", "knowledge"]
    key: str
    choice: Literal["local", "base"]
    expected_hash: str


def resolve(service, data):
    cfg = config()
    if not cfg:
        raise ValueError("飞书同步未配置")
    conflicts = get_state(service, "base-sync").get("conflicts", [])
    conflict = next((c for c in conflicts if c["table"] == data.table and c["key"] == data.key), None)
    if not conflict or conflict["hash"] != data.expected_hash:
        raise Conflict("冲突内容已变化，请刷新后选择")
    state_key = "base-resolution:" + digest([cfg["base_token"], data.table, data.key])
    put_state(service, state_key, data.model_dump())
    return {"state": "queued"}


class LarkCLI:
    def __init__(self, cfg):
        self.cfg = cfg

    def call(self, command, table=None, **flags):
        args = [
            "lark-cli",
            "base",
            "+" + command,
            "--base-token",
            self.cfg["base_token"],
            "--as",
            "bot",
            "--format",
            "json",
        ]
        if self.cfg.get("profile"):
            args.extend(["--profile", self.cfg["profile"]])
        if table:
            args.extend(["--table-id", self.cfg["tables"][table]])
        temporary = None
        if "json" in flags:
            temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8")
            temporary.write(flags["json"])
            temporary.flush()
            flags["json"] = "@" + Path(temporary.name).name
        for key, value in flags.items():
            for item in value if isinstance(value, list) else [value]:
                args.extend(["--" + key.replace("_", "-"), str(item)])
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(Path(temporary.name).parent) if temporary else None,
            )
        finally:
            if temporary:
                temporary.close()
        # CLI diagnostics can contain credentials; only persist a command and exit code.
        if result.returncode:
            raise RuntimeError(f"飞书 {command} 未成功（退出码 {result.returncode}）")
        body = json.loads(result.stdout)
        if not body.get("ok"):
            raise RuntimeError(f"飞书 {command} 返回失败")
        return body["data"]

    def rows(self, table, fields):
        actual = self.call("field-list", table)["fields"]
        by_name = {f["name"]: f for f in actual}
        if any(name not in by_name for name in fields):
            raise ValueError(f"{SCHEMAS[table][0]}缺少同步字段，请恢复字段名")
        if any(by_name[name]["type"] not in {"text", "select"} for name in fields):
            raise ValueError("同步字段类型变化，请核对表结构")
        result, offset = {}, 0
        while True:
            page = self.call("record-list", table, field_id=fields, limit=200, offset=offset)
            values, ids = page["data"], page["record_id_list"]
            if len(values) != len(ids):
                raise ValueError("飞书记录与标识数量不一致")
            for record_id, row in zip(ids, values, strict=True):
                mapped = {k: (v if v is not None else "") for k, v in zip(page["fields"], row, strict=True)}
                for name, value in mapped.items():
                    field = by_name[name]
                    if field["type"] == "select":
                        if isinstance(value, list):
                            if len(value) > 1:
                                raise ValueError("同步字段需要单选：" + name)
                            mapped[name] = value[0] if value else ""
                    elif field.get("style", {}).get("type") == "url" and value:
                        link = re.fullmatch(r"\[[^\]]*\]\((https?://.+)\)", value)
                        if link:
                            mapped[name] = link.group(1)
                key = mapped[SCHEMAS[table][1]]
                if not key:
                    continue
                if not isinstance(key, str) or key in result:
                    raise ValueError(f"{SCHEMAS[table][0]}存在重复或无效业务键，同步暂停该表")
                result[key] = {"id": record_id, "fields": mapped}
            offset += len(values)
            if not page.get("has_more"):
                break
            if not values:
                raise ValueError("飞书分页未前进")
        return result

    def create(self, table, rows):
        fields = SCHEMAS[table][2]
        for start in range(0, len(rows), 100):
            self.call(
                "record-batch-create",
                table,
                json=json.dumps(
                    {
                        "fields": fields,
                        "rows": [[r.get(f, "") for f in fields] for r in rows[start : start + 100]],
                    },
                    ensure_ascii=False,
                ),
            )

    def update(self, table, record_id, fields):
        if fields:
            self.call(
                "record-upsert", table, record_id=record_id, json=json.dumps(fields, ensure_ascii=False)
            )


def followup_fields(data):
    return {
        "跟进状态": data.get("status", "待核实"),
        "负责人": data.get("owner", ""),
        "跟进备注": data.get("note", ""),
    }


def fact_fields(fact):
    value = fact.get("value")
    return {
        "资料值": ""
        if value is None
        else value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False),
        "确认状态": {"confirmed": "已确认", "estimate": "估算", "unknown": "未知"}[
            fact.get("status", "unknown")
        ],
        "依据说明": fact.get("evidence", ""),
        "有效起日": fact.get("valid_from") or "",
        "有效止日": fact.get("valid_to") or "",
    }


def parse_fact(fields, key):
    value = fields["资料值"]
    kind = FIELD_QUESTIONS.get(key, {}).get("type")
    if value == "":
        value = None
    elif kind == "number":
        value = float(value)
    elif kind == "boolean":
        if value not in {"true", "false", "是", "否"}:
            raise ValueError("布尔资料请填写 是/否 或 true/false")
        value = value in {"true", "是"}
    status = {"已确认": "confirmed", "估算": "estimate", "未知": "unknown"}.get(fields["确认状态"])
    return Fact(
        value=value,
        status=status,
        evidence=fields["依据说明"],
        valid_from=fields["有效起日"] or None,
        valid_to=fields["有效止日"] or None,
    )


class BaseSync:
    def __init__(self, service, cfg=None, client=None):
        self.service, self.cfg = service, cfg or config()
        if not self.cfg:
            raise ValueError("飞书同步未配置")
        self.client = client or LarkCLI(self.cfg)
        self.company = self.cfg.get("company_id", "juzi")
        self.actions = Actions(service)

    def rows(self, table):
        """Return stable business keys, field maps and version-checked edit callbacks."""
        service, company = self.service, self.company
        rows = {}
        if table == "opportunities":
            followups = self.actions.feedback(company)
            work = {i["source_id"]: i for i in service.worklist(company)["items"]}
            plans = {a["candidate_id"]: a for a in self.actions.plan(company, limit=10000)}
            for c in Discovery(service).candidates(company, limit=None):
                key = c["id"]
                item, follow = work.get(c["source_id"], {}), followups.get(key, {})
                detail = service.detail(c["source_id"]) if c["source_id"] else {}
                draft = (detail.get("policy") or {}).get("draft", {})
                window = draft.get("window", {})
                evidence = (
                    draft.get("identity_evidence", [])
                    + draft.get("summary_evidence", [])
                    + window.get("evidence", [])
                    + draft.get("material_evidence", [])
                )
                fields = {
                    "机会名称": c["title"],
                    "线索ID": key,
                    "原文链接": c["url"],
                    "类别": {
                        "event": "活动",
                        "compute_support": "算力与补贴",
                        "cloud_credit": "创业云资源",
                    }.get(c["category"], "企业支持"),
                    "原文状态": {"collected": "已归档", "blocked": "采集受阻", "discovered": "待采集"}[
                        c["state"]
                    ],
                    "批次": draft.get("batch", "待核实"),
                    "资格结论": item.get("decision", "待核实"),
                    "申报窗口": " 至 ".join(
                        [
                            window.get("start") or "起日待核实",
                            window.get("end") or ("持续开放" if window.get("ongoing") else "止日待核实"),
                        ]
                    ),
                    "支持摘要": draft.get("summary", ""),
                    "材料要求": "\n".join(draft.get("materials", [])),
                    "资格缺口": "\n".join(g["label"] + "：" + g["reason"] for g in item.get("gaps", [])),
                    "原文依据": "\n".join(e["block_id"] + "：" + e["quote"] for e in evidence)[:16000],
                    "下一步行动": plans.get(key, {}).get("next_action", "查看原文与跟进状态"),
                    "工作台链接": self.cfg["site_url"]
                    + ("/?source=" + c["source_id"] if c["source_id"] else "/"),
                    **followup_fields(follow),
                }

                def apply(edit, key=key, version=follow.get("version", 0)):
                    self.actions.save_feedback(
                        company,
                        key,
                        FollowupInput(
                            status=edit["跟进状态"],
                            owner=edit["负责人"],
                            note=edit["跟进备注"],
                            expected_version=version,
                        ),
                    )

                rows[key] = (fields, apply)
        elif table == "relations":
            candidates = {c["id"]: c for c in Discovery(service).candidates(company, limit=None)}
            for relation in self.actions.relations(company):
                key = relation["id"]
                fields = {
                    "关系名称": relation["left_title"] + " ↔ " + relation["right_title"],
                    "关系ID": key,
                    "来源一": candidates[relation["left_id"]]["url"],
                    "来源二": candidates[relation["right_id"]]["url"],
                    "关系判断": relation["relation"],
                    "项目名称": relation["program"],
                    "批次": relation["batch"],
                    "判断依据": relation["evidence"],
                }

                def apply(edit, relation=relation):
                    self.actions.save_relation(
                        company,
                        RelationInput(
                            left_id=relation["left_id"],
                            right_id=relation["right_id"],
                            relation=edit["关系判断"],
                            program=edit["项目名称"],
                            batch=edit["批次"],
                            evidence=edit["判断依据"],
                            expected_version=relation["version"],
                        ),
                    )

                rows[key] = (fields, apply)
        elif table == "facts":
            facts = service.company(company)["facts"]
            questions = {q["field"]: q for q in Knowledge(service).followups(company, limit=None)}
            for key in sorted(set(facts) | set(questions) | set(FIELD_QUESTIONS)):
                fact = facts.get(key, Fact().model_dump(mode="json"))
                meta = questions.get(key, FIELD_QUESTIONS.get(key, {}))
                fields = {
                    "资料名称": meta.get("label", key),
                    "资料键": key,
                    "待回答问题": meta.get("question", "请提供已核验信息及依据"),
                    **fact_fields(fact),
                }

                def apply(edit, key=key, expected=fact_fields(fact)):
                    current = service.company(company)
                    if fact_fields(current["facts"].get(key, {})) != expected:
                        raise Conflict("公司资料已变化，请刷新")
                    current["facts"][key] = parse_fact(edit, key).model_dump(mode="json")
                    service.save_company(
                        company,
                        CompanyInput(
                            name=current["name"], facts=current["facts"], expected_version=current["version"]
                        ),
                    )

                rows[key] = (fields, apply)
        elif table == "knowledge":
            latest = {}
            for entry in Knowledge(service).entries(company, history=True):
                latest.setdefault(entry["key"], entry)
            for entry in latest.values():
                fields = {
                    "资料标题": entry["title"],
                    "资料键": entry["key"],
                    "资料正文": entry["body"],
                    "资料来源": entry["source_ref"],
                    "资料状态": {"reference": "参考", "verified": "已核验", "retired": "停用"}[
                        entry["status"]
                    ],
                }
                rows[entry["key"]] = (fields, self.knowledge_apply(entry["key"], entry["version"]))
        else:
            for r in Reports(service).list(company):
                title = {"daily": "日报", "weekly": "周报", "manual": "即时报告"}[r["kind"]]
                fields = {
                    "报告名称": r["created_at"][:10] + " · " + title,
                    "报告ID": r["id"],
                    "生成时间": r["created_at"],
                    "优先行动": "\n".join(
                        a["title"] + "：" + a["next_action"] for a in r["payload"].get("actions", [])
                    ),
                    "报告正文": r["markdown"][:18000]
                    + ("\n（正文较长，请打开完整报告）" if len(r["markdown"]) > 18000 else ""),
                    "完整报告": self.cfg["site_url"]
                    + "/api/reports/"
                    + company
                    + "/"
                    + r["id"]
                    + "/download",
                }
                rows[r["id"]] = (fields, None)
        return rows

    def knowledge_apply(self, key, version):
        def apply(edit):
            Knowledge(self.service).save(
                self.company,
                KnowledgeInput(
                    key=key,
                    expected_version=version,
                    title=edit["资料标题"],
                    body=edit["资料正文"],
                    source_ref=edit["资料来源"],
                    status={"参考": "reference", "已核验": "verified", "停用": "retired"}.get(
                        edit["资料状态"]
                    ),
                ),
            )

        return apply

    def table(self, table):
        fields, editable = SCHEMAS[table][2:]
        remote = self.client.rows(table, fields)
        local = self.rows(table)
        conflicts, errors, updates, imports = [], [], 0, 0
        if table == "knowledge":
            for key, item in remote.items():
                if key not in local:
                    try:
                        # Retired entries remain retired; don't recreate historical keys.
                        known = {
                            e["key"] for e in Knowledge(self.service).entries(self.company, history=True)
                        }
                        if key not in known:
                            self.knowledge_apply(key, 0)(item["fields"])
                            imports += 1
                    except (ValueError, Conflict) as error:
                        errors.append({"table": table, "key": key, "error": str(error)[:300]})
            local = self.rows(table)
        missing = [v[0] for key, v in local.items() if key not in remote]
        if missing:
            self.client.create(table, missing)
            remote = self.client.rows(table, fields)  # Read back real record IDs; retries never blind-create.
        for key, (desired, apply) in local.items():
            if key not in remote:
                raise ValueError("新建记录回读缺失，停止同步")
            target = remote[key]
            human = {f: desired[f] for f in editable}
            actual = {f: target["fields"].get(f, "") for f in editable}
            state_key = "base-row:" + digest([self.cfg["base_token"], table, key])
            previous = get_state(self.service, state_key)
            local_changed = human != previous.get("human", human)
            remote_changed = actual != previous.get("human", human)
            try:
                if local_changed and remote_changed and human != actual:
                    conflict_hash = digest([human, actual])
                    choice = get_state(
                        self.service, "base-resolution:" + digest([self.cfg["base_token"], table, key])
                    )
                    if choice.get("expected_hash") == conflict_hash:
                        remote_changed = choice["choice"] == "base"
                    else:
                        conflicts.append(
                            {
                                "table": table,
                                "key": key,
                                "title": desired[fields[0]],
                                "local": human,
                                "base": actual,
                                "hash": conflict_hash,
                            }
                        )
                        continue
                if remote_changed and actual != human:
                    apply(actual)
                    human = (
                        {f: self.rows(table)[key][0][f] for f in editable}
                        if table != "knowledge" or actual.get("资料状态") != "停用"
                        else actual
                    )
                    imports += 1
                patch = {
                    f: desired[f]
                    for f in fields
                    if f not in editable and desired[f] != target["fields"].get(f, "")
                }
                # Human fields flow outward only when the website changed and Base did not.
                patch.update({f: human[f] for f in editable if human[f] != actual[f]})
                self.client.update(table, target["id"], patch)
                updates += bool(patch)
                put_state(self.service, state_key, {"record_id": target["id"], "human": human})
            except (ValueError, Conflict) as error:
                errors.append({"table": table, "key": key, "error": str(error)[:300]})
        return {
            "rows": len(local),
            "created": len(missing),
            "updated": updates,
            "imported": imports,
            "conflicts": conflicts,
            "errors": errors,
        }

    def run(self):
        receipt = {"started_at": stamp(), "tables": {}, "conflicts": [], "errors": []}
        for table in SCHEMAS:
            try:
                result = self.table(table)
                receipt["tables"][table] = {
                    k: v for k, v in result.items() if k not in {"conflicts", "errors"}
                }
                receipt["conflicts"].extend(result["conflicts"])
                receipt["errors"].extend(result["errors"])
            except Exception as error:
                receipt["errors"].append({"table": table, "error": str(error)[:300]})
        receipt["finished_at"] = stamp()
        receipt["state"] = "needs_attention" if receipt["errors"] or receipt["conflicts"] else "ok"
        put_state(self.service, "base-sync", receipt)
        return receipt


def main():
    service = Service(Store())
    with (service.store.path / "base-sync.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            result = BaseSync(service).run()
            print(
                json.dumps(
                    {
                        "state": result["state"],
                        "finished_at": result["finished_at"],
                        "tables": result["tables"],
                        "errors": len(result["errors"]),
                        "conflicts": len(result["conflicts"]),
                    }
                ),
                flush=True,
            )
            time.sleep(300)


if __name__ == "__main__":
    main()
