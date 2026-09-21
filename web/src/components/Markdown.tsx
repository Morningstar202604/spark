import { memo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import "highlight.js/styles/github-dark.css"
import hljs from "highlight.js/lib/core"
import bash from "highlight.js/lib/languages/bash"
import python from "highlight.js/lib/languages/python"
import typescript from "highlight.js/lib/languages/typescript"
import javascript from "highlight.js/lib/languages/javascript"
import json from "highlight.js/lib/languages/json"
import toml from "highlight.js/lib/languages/ini"
import sql from "highlight.js/lib/languages/sql"

hljs.registerLanguage("bash", bash)
hljs.registerLanguage("shell", bash)
hljs.registerLanguage("python", python)
hljs.registerLanguage("py", python)
hljs.registerLanguage("typescript", typescript)
hljs.registerLanguage("ts", typescript)
hljs.registerLanguage("tsx", typescript)
hljs.registerLanguage("javascript", javascript)
hljs.registerLanguage("js", javascript)
hljs.registerLanguage("json", json)
hljs.registerLanguage("toml", toml)
hljs.registerLanguage("ini", toml)
hljs.registerLanguage("sql", sql)

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  let highlighted = ""
  let ok = false
  if (lang && hljs.getLanguage(lang)) {
    try {
      highlighted = hljs.highlight(code, { language: lang }).value
      ok = true
    } catch {
      ok = false
    }
  }
  return (
    <div className="my-2 overflow-hidden rounded-lg border border-spark-line">
      <div className="flex items-center justify-between bg-[#0c1117] px-3 py-1 text-[10px] tracking-wider text-spark-muted uppercase">
        <span>{lang || "text"}</span>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(code)}
          className="rounded px-1.5 py-0.5 hover:bg-spark-line"
        >
          复制
        </button>
      </div>
      <pre className="overflow-auto p-3 text-xs leading-relaxed">
        {ok ? (
          <code className="hljs" dangerouslySetInnerHTML={{ __html: highlighted }} />
        ) : (
          <code>{code}</code>
        )}
      </pre>
    </div>
  )
}

function DiffBlock({ code }: { code: string }) {
  const lines = code.split("\n")
  return (
    <pre className="my-2 overflow-auto rounded-lg border border-spark-line bg-[#0c1117] p-3 font-mono text-xs leading-relaxed">
      {lines.map((line, i) => {
        const cls = line.startsWith("+")
          ? "text-emerald-400 bg-emerald-950/40"
          : line.startsWith("-")
            ? "text-red-400 bg-red-950/40"
            : line.startsWith("@@")
              ? "text-sky-400"
              : "text-spark-muted"
        return (
          <div key={i} className={`px-1 ${cls}`}>
            {line || " "}
          </div>
        )
      })}
    </pre>
  )
}

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const raw = String(children)
            const match = /language-(\w+)/.exec(className || "")
            const isBlock = raw.includes("\n") || match
            if (!isBlock) {
              return (
                <code className="rounded bg-[#0c1117] px-1.5 py-0.5 font-mono text-[0.85em] text-spark-accent" {...props}>
                  {raw}
                </code>
              )
            }
            const content = raw.replace(/\n$/, "")
            if (!match && (content.includes("diff") || /^(?:[+-]{1}[^+-]|[+-]\s)/m.test(content)) && content.split("\n").some((l) => l.startsWith("+") || l.startsWith("-"))) {
              return <DiffBlock code={content} />
            }
            return <CodeBlock code={content} lang={match?.[1] || ""} />
          },
          pre({ children }) {
            return <>{children}</>
          },
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noreferrer" className="text-spark-accent underline">
                {children}
              </a>
            )
          },
          table({ children }) {
            return (
              <div className="my-2 overflow-auto rounded-lg border border-spark-line">
                <table className="w-full text-xs">{children}</table>
              </div>
            )
          },
          th({ children }) {
            return <th className="border-b border-spark-line bg-[#0c1117] px-3 py-1.5 text-left font-bold">{children}</th>
          },
          td({ children }) {
            return <td className="border-b border-spark-line/50 px-3 py-1.5">{children}</td>
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})

export default Markdown
