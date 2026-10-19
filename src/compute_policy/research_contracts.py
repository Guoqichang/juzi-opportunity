from typing import Any, Literal

from pydantic import Field

from .contracts import Contract, Fact


class Hub(Contract):
    url: str
    label: str


class ResearchSettings(Contract):
    enabled: bool = True
    search_enabled: bool = True
    regions: list[str] = Field(default_factory=lambda: ["海淀", "北京", "全国"], max_length=15)
    topics: list[str] = Field(
        default_factory=lambda: [
            "算力",
            "模型券",
            "创新券",
            "人工智能",
            "大模型",
            "智能体",
            "创新创业",
            "创业资源",
            "客服",
            "知识库",
            "企业应用",
            "场景征集",
        ],
        max_length=30,
    )
    exclude_terms: list[str] = Field(
        default_factory=lambda: ["中小学", "青少年", "幼儿", "临床医学", "招聘"], max_length=30
    )
    queries: list[str] = Field(default_factory=list, max_length=30)
    hubs: list[Hub] = Field(default_factory=list, max_length=30)
    trusted_domains: list[str] = Field(
        default_factory=lambda: [
            "beijing.gov.cn",
            "bjhd.gov.cn",
            "ncsti.gov.cn",
            "miracleplus.com",
            "aliyun.com",
            "volcengine.com",
            "cloud.tencent.com",
        ],
        max_length=50,
    )
    interval_hours: int = Field(default=6, ge=1, le=168)
    max_queries: int = Field(default=4, ge=0, le=10)
    max_hub_pages: int = Field(default=6, ge=0, le=30)
    max_collect: int = Field(default=10, ge=1, le=30)
    report_mode: Literal["daily_weekly", "daily_changes", "weekly"] = "daily_weekly"
    report_hour: int = Field(default=9, ge=0, le=23)
    timezone: Literal["Asia/Shanghai", "Asia/Singapore", "UTC"] = "Asia/Shanghai"


class SettingsUpdate(Contract):
    settings: ResearchSettings
    expected_version: int = Field(ge=0)


class KnowledgeInput(Contract):
    key: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=200000)
    source_ref: str = Field(min_length=1, max_length=2000)
    status: Literal["reference", "verified", "retired"] = "reference"
    expected_version: int = Field(ge=0)


class QuestionInput(Contract):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=100)
    source_id: str | None = None


class FactAnswer(Contract):
    field: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    fact: Fact
    expected_version: int = Field(ge=1)


class FormField(Contract):
    field: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=200)
    required: bool = True


class FormRequest(Contract):
    source_id: str
    fields: list[FormField] | None = Field(default=None, max_length=100)
    template_name: str = Field(default="通用准备清单（非官方申报表）", max_length=300)


class ResearchJobInput(Contract):
    company_id: str = "juzi"
    kind: Literal["daily", "weekly", "manual"] = "manual"


FIELD_QUESTIONS: dict[str, dict[str, Any]] = {
    "legal_name": {
        "label": "申报法人全称",
        "question": "这次用哪个法人主体申报？请以营业执照名称为准。",
        "type": "text",
    },
    "registered_district": {
        "label": "企业注册区县",
        "question": "该法人注册在哪个区县？请填写营业执照或登记记录来源。",
        "type": "text",
    },
    "eligible_compute_spend_cny": {
        "label": "已发生的合规算力支出（元）",
        "question": "对应政策核算期间，已签约、开票和支付的算力费用分别是多少？尚未核验的总额请保留估算。",
        "type": "number",
    },
    "sme_qualified": {
        "label": "科技型中小企业资格",
        "question": "该法人是否有在有效期内的科技型中小企业资格？请给出记录来源。",
        "type": "boolean",
    },
    "project_name": {"label": "申报项目名称", "question": "计划用哪个项目申请？", "type": "text"},
    "project_summary": {
        "label": "项目介绍",
        "question": "请提供已确认的项目介绍，说明产品、客户场景和算力用途。",
        "type": "text",
    },
    "credit_code": {
        "label": "统一社会信用代码",
        "question": "该申报主体的统一社会信用代码是什么？",
        "type": "text",
    },
    "contact_name": {"label": "申报联系人", "question": "由谁负责该项目申报？", "type": "text"},
    "contact_phone": {"label": "联系手机", "question": "申报联系人使用哪个电话？", "type": "text"},
}
