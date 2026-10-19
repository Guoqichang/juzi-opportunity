import { tool } from "@opencode-ai/plugin"

function publicTool(action: string, description: string) {
  return tool({
    description,
    args: { value: tool.schema.string().describe(action === "search" ? "公开主题搜索词，不含公司内部资料" : "要读取的公开网页完整 URL") },
    async execute(args, context) {
      const proc = Bun.spawn(["/app/.venv/bin/python", "-m", "compute_policy.research_tools", context.sessionID, action, args.value], { stdout: "pipe", stderr: "pipe" })
      const timer = setTimeout(() => proc.kill(), 65000)
      try {
        const output = await new Response(proc.stdout).text()
        await proc.exited
        return output.slice(0, 22000) || "工具未返回结果，不能当作采集成功。"
      } finally { clearTimeout(timer) }
    },
  })
}

export const search = publicTool("search", "搜索公开的政策和活动；优先查主办方或主管部门原文，保留年份、批次和地区。")
export const fetch = publicTool("fetch", "抓取公开网页或附件并保存原始证据。默认先使用此工具读取来源。")
export const render = publicTool("render", "用独立浏览器渲染需要 JavaScript 的公开网页并保存 DOM 证据。不能解决验证码、登录或证书过期；此时应查其他公开来源。")
