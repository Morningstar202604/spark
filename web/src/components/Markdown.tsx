import { memo, useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import "highlight.js/styles/github.css"
import hljs from "highlight.js/lib/core"
import type { LanguageFn } from "highlight.js"
import bash from "highlight.js/lib/languages/bash"
import python from "highlight.js/lib/languages/python"
import typescript from "highlight.js/lib/languages/typescript"
import javascript from "highlight.js/lib/languages/javascript"
import json from "highlight.js/lib/languages/json"

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

const deferredLanguageLoads = new Map<string, Promise<LanguageFn>>()

function loadDeferredLanguage(language: string): Promise<LanguageFn> {
  const canonical = language === "toml" ? "ini" : language
  const existing = deferredLanguageLoads.get(canonical)
  if (existing) return existing

  const promise = (canonical === "ini" ? import("highlight.js/lib/languages/ini") : import("highlight.js/lib/languages/sql"))
    .then(({ default: languageFn }) => {
      hljs.registerLanguage(canonical, languageFn)
      if (canonical === "ini") hljs.registerLanguage("toml", languageFn)
      return languageFn
    })
    .catch((error: unknown) => {
      deferredLanguageLoads.delete(canonical)
      throw error
    })
  deferredLanguageLoads.set(canonical, promise)
  return promise
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const language = lang.toLowerCase()
  const deferred = language === "toml" || language === "ini" || language === "sql"
  const [ready, setReady] = useState(!deferred || Boolean(hljs.getLanguage(language)))

  useEffect(() => {
    if (!deferred || hljs.getLanguage(language)) {
      setReady(true)
      return
    }
    let active = true
    setReady(false)
    loadDeferredLanguage(language)
      .then(() => {
        if (active) setReady(true)
      })
      .catch(() => {
        if (active) setReady(false)
      })
    return () => {
      active = false
    }
  }, [deferred, language])

  let highlighted = ""
  let ok = false
  if (ready && hljs.getLanguage(language)) {
    try {
      highlighted = hljs.highlight(code, { language }).value
      ok = true
    } catch {
      ok = false
    }
  }
  return (
    <div className="my-2 overflow-hidden rounded-lg border border-spark-line">
      <div className="flex items-center justify-between bg-spark-side px-3 py-1 text-[10px] tracking-wider text-spark-muted uppercase">
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
    <pre className="my-2 overflow-auto rounded-lg border border-spark-line bg-spark-code p-3 font-mono text-xs leading-relaxed">
      {lines.map((line, i) => {
        const cls = line.startsWith("+")
          ? "text-spark-ok bg-spark-ok/12"
          : line.startsWith("-")
            ? "text-spark-err bg-spark-err/12"
            : line.startsWith("@@")
              ? "text-spark-user"
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
                <code className="rounded bg-spark-code px-1.5 py-0.5 font-mono text-[0.85em] text-spark-accent" {...props}>
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
            return <th className="border-b border-spark-line bg-spark-side px-3 py-1.5 text-left font-bold">{children}</th>
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
