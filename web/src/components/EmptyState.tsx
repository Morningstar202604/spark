import { memo } from "react"
import { IconSpark } from "./icons"

const suggestions = [
  { title: "解读这个项目", desc: "分析仓库结构与核心架构", prompt: "完整解读这个项目：架构、模块、技术栈与关键流程" },
  { title: "写代码", desc: "实现一个功能或修复 Bug", prompt: "请帮我实现一个完整的功能模块，包含实现思路与代码" },
  { title: "检查与重构", desc: "审查代码质量并给出改进方案", prompt: "审查当前代码的质量问题，并给出重构建议与具体修改" },
]

export const EmptyState = memo(function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="anim-fade-up mx-auto max-w-xl px-6 py-10 text-center">
        <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-soft">
          <IconSpark size={28} />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-ink">Spark</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink2">本地自托管的 AI 编程智能体，代码与密钥都在你手里</p>

        <div className="mt-8 grid gap-2.5 text-left">
          {suggestions.map((s) => (
            <button
              key={s.title}
              type="button"
              onClick={() => onPick(s.prompt)}
              className="group flex items-center gap-3 rounded-xl border border-line bg-panel px-4 py-3 text-left transition-all hover:border-line2 hover:bg-hover active:scale-[0.99]"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-hover text-accent transition-colors group-hover:bg-accent-soft">
                <IconSpark size={15} />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-ink">{s.title}</span>
                <span className="block truncate text-xs text-ink3">{s.desc}</span>
              </span>
              <span className="ml-auto shrink-0 text-xs text-ink3 opacity-0 transition-opacity group-hover:opacity-100">发送 →</span>
            </button>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[11.5px] text-ink3">
          <span>· 代码生成与重构</span>
          <span>· 长程记忆</span>
          <span>· 沙箱安全执行</span>
          <span>· 检查点回滚</span>
        </div>
      </div>
    </div>
  )
})
