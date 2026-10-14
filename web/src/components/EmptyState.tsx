import { memo } from "react"
import { BrandMark } from "./BrandMark"
import { IconSpark } from "./icons"
import { t, type TranslationKey } from "../i18n"
import { useUiStore } from "../store/uiStore"

const suggestions: Array<{ title: TranslationKey; desc: TranslationKey; prompt: TranslationKey }> = [
  { title: "empty.suggest.explore", desc: "empty.suggest.exploreDesc", prompt: "empty.suggest.explorePrompt" },
  { title: "empty.suggest.code", desc: "empty.suggest.codeDesc", prompt: "empty.suggest.codePrompt" },
  { title: "empty.suggest.review", desc: "empty.suggest.reviewDesc", prompt: "empty.suggest.reviewPrompt" },
]

export const EmptyState = memo(function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  const lang = useUiStore((s) => s.lang)
  return (
    <div className="flex h-full items-center justify-center">
      <div className="anim-fade-up mx-auto max-w-xl px-6 py-10 text-center">
        <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-[20px]">
          <BrandMark size={64} />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-ink">Spark</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink2">{t("empty.tagline", lang)}</p>

        <div className="mt-8 grid gap-2.5 text-left">
          {suggestions.map((s) => (
            <button
              key={s.title}
              type="button"
              onClick={() => onPick(t(s.prompt, lang))}
              className="group flex items-center gap-3 rounded-xl border border-line bg-panel px-4 py-3 text-left transition-all hover:border-line2 hover:bg-hover active:scale-[0.99]"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-hover text-accent transition-colors group-hover:bg-accent-soft">
                <IconSpark size={15} />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-ink">{t(s.title, lang)}</span>
                <span className="block truncate text-xs text-ink3">{t(s.desc, lang)}</span>
              </span>
              <span className="ml-auto shrink-0 text-xs text-ink3 opacity-0 transition-opacity group-hover:opacity-100">{t("common.send", lang)} →</span>
            </button>
          ))}
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[11.5px] text-ink3">
          <span>· {t("empty.feature.code", lang)}</span>
          <span>· {t("empty.feature.memory", lang)}</span>
          <span>· {t("empty.feature.sandbox", lang)}</span>
          <span>· {t("sidebar.checkpoint", lang)}</span>
        </div>
      </div>
    </div>
  )
})
