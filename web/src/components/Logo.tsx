export default function Logo({ size = 26, withWordmark = true }: { size?: number; withWordmark?: boolean }) {
  return (
    <span className="flex items-center gap-2">
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
        <defs>
          <linearGradient id="spark-shell" x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse">
            <stop stopColor="var(--logo-a)" />
            <stop offset="1" stopColor="var(--logo-b)" />
          </linearGradient>
          <linearGradient id="spark-core" x1="10" y1="9" x2="23" y2="24" gradientUnits="userSpaceOnUse">
            <stop stopColor="#ffd9a8" />
            <stop offset="0.45" stopColor="#ff9d4f" />
            <stop offset="1" stopColor="#ff6a1f" />
          </linearGradient>
          <radialGradient id="spark-heat" cx="0.5" cy="0.5" r="0.5">
            <stop stopColor="#ff8a3d" stopOpacity="0.45" />
            <stop offset="1" stopColor="#ff8a3d" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#spark-shell)" stroke="var(--logo-stroke)" strokeWidth="1.5" />
        <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#spark-heat)" />
        <path
          d="M9.2 6.6 L9.2 14.1 L12.6 11.4 M22.8 25.4 L22.8 17.9 L19.4 20.6"
          stroke="var(--logo-stroke)"
          strokeWidth="1.3"
          strokeLinecap="round"
          opacity="0.85"
        />
        <path
          d="M18.2 8.6 L11.6 17.4 h3.9 l-1.4 6.6 6.6-8.9 h-3.9 l1.4-6.5 z"
          fill="url(#spark-core)"
          stroke="var(--logo-glyph)"
          strokeWidth="0.7"
          strokeLinejoin="round"
        />
      </svg>
      {withWordmark && (
        <span className="text-[15px] leading-none font-bold tracking-wide text-spark-text">
          Spark
          <span className="ml-1.5 hidden rounded bg-spark-accent/15 px-1.5 py-0.5 align-[2px] text-[9px] font-bold tracking-widest text-spark-accent uppercase sm:inline">
            agent
          </span>
        </span>
      )}
    </span>
  )
}
