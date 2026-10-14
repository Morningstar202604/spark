import { memo, useCallback, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import hljs from "highlight.js/lib/common"
import { IconCheck, IconCopy } from "./icons"
import { useUiStore } from "../store/uiStore"
import { resolveTheme } from "../theme"
import { cls } from "./ui"
import { t } from "../i18n"

// 语言缺失时由 CodeBlock 内的 highlightAuto 兜底，无需手动注册

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [copied, setCopied] = useState(false)
  const uiLang = useUiStore((s) => s.lang)
  const language = lang.toLowerCase()
  let highlighted = ""
  let ok = false
  try {
    if (language && hljs.getLanguage(language)) {
      highlighted = hljs.highlight(code, { language }).value
      ok = true
    } else {
      highlighted = hljs.highlightAuto(code).value
      ok = true
    }
  } catch {
    ok = false
  }

  const copy = useCallback(() => {
    navigator.clipboard?.writeText(code).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }, [code])

  return (
    <div className="codeblock anim-fade">
      <div className="codeblock-header">
        <span className="flex items-center gap-2">
          <span className="font-semibold text-ink2">{lang || "text"}</span>
          {ok && <span className="text-[10px] text-ink3">{t("common.lines", uiLang, { n: code.split("\n").length })}</span>}
        </span>
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-ink3 transition-colors hover:bg-hover hover:text-ink"
        >
          {copied ? <IconCheck size={12} className="text-ok" /> : <IconCopy size={12} />}
          <span>{copied ? t("common.copied", uiLang) : t("common.copy", uiLang)}</span>
        </button>
      </div>
      <pre tabIndex={0} aria-label={t("markdown.codeBlock", uiLang)}>
        {ok ? <code className={cls("hljs")} dangerouslySetInnerHTML={{ __html: highlighted }} /> : <code>{code}</code>}
      </pre>
    </div>
  )
}

function DiffBlock({ code }: { code: string }) {
  const theme = useUiStore((s) => s.theme)
  const lang = useUiStore((s) => s.lang)
  const dark = resolveTheme(theme) === "dark"
  const lines = code.split("\n")
  return (
    <pre className="codeblock max-h-[520px] overflow-auto p-0" tabIndex={0} aria-label={t("markdown.diffBlock", lang)}>
      <div className="codeblock-header">
        <span className="font-semibold text-ink2">diff</span>
        <span className="text-[10px] text-ink3">+{lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length} / -{lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length}</span>
      </div>
      <div className="p-0">
        {lines.map((line, i) => {
          const add = line.startsWith("+") && !line.startsWith("+++")
          const del = line.startsWith("-") && !line.startsWith("---")
          const hunk = line.startsWith("@@")
          const clsLine = add ? cls("add", dark && "dark") : del ? cls("del", dark && "dark") : hunk ? "hunk" : "ctx"
          return (
            <div key={i} className={cls("diff-line", clsLine)}>
              {line || " "}
            </div>
          )
        })}
      </div>
    </pre>
  )
}

function looksLikeDiff(text: string): boolean {
  const lines = text.split("\n")
  return lines.some((l) => l.startsWith("@@")) && (lines.some((l) => l.startsWith("+")) || lines.some((l) => l.startsWith("-")))
}

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const raw = String(children)
            const match = /language-(\w+)/.exec(className || "")
            const isBlock = raw.includes("\n") || match
            if (!isBlock) {
              return (
                <code className="inline-code" {...props}>
                  {raw}
                </code>
              )
            }
            const content = raw.replace(/\n$/, "")
            if (!match && looksLikeDiff(content)) {
              return <DiffBlock code={content} />
            }
            return <CodeBlock code={content} lang={match?.[1] || ""} />
          },
          pre({ children }) {
            return <>{children}</>
          },
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noreferrer" className="hover:underline">
                {children}
              </a>
            )
          },
          table({ children }) {
            return (
              <div className="my-2 overflow-auto rounded-lg border border-line">
                <table className="w-full text-sm">{children}</table>
              </div>
            )
          },
          th({ children }) {
            return <th className="border-b border-line bg-panel2 px-3 py-2 text-left font-semibold text-ink2">{children}</th>
          },
          td({ children }) {
            return <td className="border-b border-line/60 px-3 py-2 text-ink2">{children}</td>
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
})

export default Markdown
