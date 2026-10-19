from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import Approval, CompanyInput
from .demo import seed_demo
from .jobs import Queue
from .service import Service
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="算力政策采集、证据复核与规则评估")
    parser.add_argument("--data-dir", help="数据目录，默认 runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="生成标明虚构的演示政策，以及空白句子互动事实档案")
    source = commands.add_parser("add", help="登记政策 URL 并排队采集")
    source.add_argument("url")
    source.add_argument("--title", default="")
    source.add_argument("--host", action="append", default=[])
    commands.add_parser("sources")
    seeds = commands.add_parser("seed", help="从 JSON 种子列表登记官方来源并排队")
    seeds.add_argument("file", default="config/sources.json", nargs="?")
    for name in ("crawl", "extract", "detail"):
        sub = commands.add_parser(name)
        sub.add_argument("source_id")
    imported = commands.add_parser("import", help="导入本地 PDF/DOCX/XLSX/TXT/HTML")
    imported.add_argument("file")
    imported.add_argument("--title", default="")
    imported.add_argument("--url", help="原始公开来源 URL；影响格式识别，请保留扩展名")
    company = commands.add_parser("company", help="查看或更新公司事实")
    company.add_argument("company_id")
    company.add_argument("--file", help="CompanyInput JSON 文件")
    review = commands.add_parser("review", help="复核政策；审批 JSON 中包含当前 bundle hash")
    review.add_argument("policy_id")
    review.add_argument("file")
    for name in ("evaluate", "export"):
        sub = commands.add_parser(name)
        sub.add_argument("company_id", default="juzi", nargs="?")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    commands.add_parser("jobs")
    for name in ("discover", "research", "report", "knowledge", "ask", "prepare"):
        sub = commands.add_parser(name)
        sub.add_argument("company_id", default="juzi", nargs="?")
        if name == "report":
            sub.add_argument("--kind", choices=["daily", "weekly", "manual"], default="manual")
        if name == "knowledge":
            sub.add_argument("--file", help="KnowledgeInput JSON 文件")
        if name == "ask":
            sub.add_argument("--question", required=True)
        if name == "prepare":
            sub.add_argument("--source-id", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--host", default="127.0.0.1", help="容器内可用 0.0.0.0；对外访问须经认证网关")
    args = parser.parse_args()
    service = Service(Store(args.data_dir))
    queue = Queue(service)
    try:
        match args.command:
            case "demo":
                result = seed_demo(service)
            case "add":
                result = service.add_source(args.url, args.title, args.host)
                result["job"] = queue.enqueue("crawl", result["id"])
            case "sources":
                result = service.sources()
            case "seed":
                result = []
                for item in json.loads(Path(args.file).read_text()):
                    source = service.add_source(item["url"], item.get("title", ""), item.get("hosts"))
                    result.append(queue.enqueue("crawl", source["id"]))
            case "crawl":
                result = service.crawl(args.source_id)
            case "extract":
                result = service.extract(args.source_id)
            case "detail":
                result = service.detail(args.source_id)
            case "import":
                result = service.import_file(args.file, args.title, args.url)
            case "company":
                result = (
                    service.save_company(
                        args.company_id, CompanyInput.model_validate_json(Path(args.file).read_text())
                    )
                    if args.file
                    else service.company(args.company_id)
                )
            case "review":
                result = service.approve(
                    args.policy_id, Approval.model_validate_json(Path(args.file).read_text())
                )
            case "evaluate":
                result = service.worklist(args.company_id, save=True)
            case "export":
                print(service.export(args.company_id))
                return
            case "jobs":
                result = queue.list()
            case "discover":
                from .discovery import Discovery

                result = Discovery(service).run(args.company_id)
            case "research":
                from .discovery import Discovery

                result = {
                    "config": Discovery(service).config(args.company_id),
                    "candidates": Discovery(service).candidates(args.company_id),
                }
            case "report":
                from .reports import Reports

                result = Reports(service).generate(args.company_id, args.kind)
                print(result["markdown"])
                return
            case "knowledge":
                from .knowledge import Knowledge
                from .research_contracts import KnowledgeInput

                result = (
                    Knowledge(service).save(
                        args.company_id, KnowledgeInput.model_validate_json(Path(args.file).read_text())
                    )
                    if args.file
                    else Knowledge(service).entries(args.company_id)
                )
            case "ask":
                from .knowledge import Knowledge
                from .research_contracts import QuestionInput

                result = Knowledge(service).ask(args.company_id, QuestionInput(question=args.question))
            case "prepare":
                from .knowledge import Knowledge
                from .research_contracts import FormRequest

                result = Knowledge(service).prepare_form(
                    args.company_id, FormRequest(source_id=args.source_id)
                )
            case "worker":
                result = {"worked": queue.worker(once=args.once)}
            case "serve":
                import uvicorn

                from .app import create_app

                uvicorn.run(create_app(service.store), host=args.host, port=args.port)
                return
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        parser.exit(0, "已停止。持久化任务和原文保留在数据目录。\n")
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"操作失败：{error}\n")


if __name__ == "__main__":
    main()
