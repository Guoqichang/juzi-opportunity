from datetime import timedelta

from .acquire import Resource, parse
from .contracts import Approval, CompanyInput, Evidence, Fact, PolicyDraft, Rule, Window
from .service import Service
from .store import now


def seed_demo(service: Service):
    """Explicitly synthetic data. Never pre-fill the real company's unverified facts."""
    try:
        service.company("juzi")
    except KeyError:
        service.save_company(
            "juzi",
            CompanyInput(
                name="句子互动 · 待补充公司事实",
                expected_version=0,
                facts={
                    "registered_district": Fact(),
                    "eligible_compute_spend_cny": Fact(),
                    "sme_qualified": Fact(),
                },
            ),
        )
    try:
        service.company("demo")
    except KeyError:
        service.save_company(
            "demo",
            CompanyInput(
                name="演示企业（合成事实）",
                expected_version=0,
                facts={
                    "registered_district": Fact(
                        value="海淀区", status="confirmed", evidence="合成营业执照，仅演示"
                    ),
                    "eligible_compute_spend_cny": Fact(
                        value=500000, status="estimate", evidence="合成预算，尚无发票"
                    ),
                },
            ),
        )
    today = now().date()
    rows = [
        ("open", "可推进示例", "海淀区", today - timedelta(days=10), today + timedelta(days=20), False),
        (
            "missing",
            "支出证据缺口示例",
            "海淀区",
            today - timedelta(days=10),
            today + timedelta(days=20),
            True,
        ),
        ("closed", "已截止批次示例", "海淀区", today - timedelta(days=40), today - timedelta(days=2), False),
        (
            "ineligible",
            "地区不符示例",
            "朝阳区",
            today - timedelta(days=10),
            today + timedelta(days=20),
            False,
        ),
    ]
    for slug, title, district, start, end, spend in rows:
        source = service.add_source("https://demo.invalid/" + slug + ".txt", "【演示】" + title, kind="demo")
        # Rerunning the demo does not overwrite human edits or rotate example deadlines.
        if source["latest_bundle"]:
            continue
        content = f"这是虚构政策，仅用于验证系统，不可作为真实申报依据。\n企业注册地须为{district}。\n申报期为{start}至{end}。\n"
        if spend:
            content += "合规算力支出须达到人民币300000元，以合同、发票和付款记录为准。\n"
        resource = parse(Resource(source["url"], content.encode(), "text/plain"))
        result = service.ingest(source["id"], [resource])

        def evidence(index):
            block = resource.blocks[index]
            return [Evidence(block_id=block["id"], quote=block["text"])]

        rules = [
            Rule(
                id="location",
                label="企业注册地",
                op="eq",
                field="registered_district",
                value=district,
                evidence=evidence(1),
            )
        ]
        if spend:
            rules.append(
                Rule(
                    id="spend",
                    label="合规算力支出（人民币元）",
                    op="gte",
                    field="eligible_compute_spend_cny",
                    value=300000,
                    evidence=evidence(3),
                )
            )
        draft = PolicyDraft(
            title=source["title"],
            issuer="虚构演示部门",
            batch="演示批次",
            support_kind="voucher",
            window=Window(start=str(start), end=str(end), evidence=evidence(2)),
            eligibility=Rule(id="root", label="全部资格条件", op="all", children=rules),
            completeness="complete",
            materials=["营业执照", "算力合同与发票（按具体条款核实）"],
        )
        service.approve(
            result["policy_id"],
            Approval(
                draft=draft,
                expected_bundle_hash=result["bundle_hash"],
                reviewer="演示数据生成器",
                note="合成规则与合成原文一致，仅供演示",
            ),
        )
    return service.worklist("demo", save=True)
