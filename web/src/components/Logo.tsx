export default function Logo({ size = 26, withWordmark = true }: { size?: number; withWordmark?: boolean }) {
  return (
    <span className="flex items-center gap-2">
      <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
        <defs>
          <linearGradient id="spark-bg" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
            <stop stopColor="#0c1f1c" />
            <stop offset="1" stopColor="#123b35" />
          </linearGradient>
          <linearGradient id="spark-g" x1="6" y1="4" x2="26" y2="28" gradientUnits="userSpaceOnUse">
            <stop stopColor="#99f6e4" />
            <stop offset="0.55" stopColor="#5eead4" />
            <stop offset="1" stopColor="#2dd4bf" />
          </linearGradient>
          <radialGradient id="spark-glow" cx="0.5" cy="0.42" r="0.62">
            <stop stopColor="#5eead4" stopOpacity="0.38" />
            <stop offset="1" stopColor="#5eead4" stopOpacity="0" />
          </radialGradient>
          <filter id="spark-soft" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="0.9" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <rect x="0.75" y="0.75" width="30.5" height="30.5" rx="9" fill="url(#spark-bg)" stroke="#1d4a43" strokeWidth="1.5" />
        <rect x="0.75" y="0.75" width="30.5" height="30.5" rx="9" fill="url(#spark-glow)" />
        <path
          d="M18.6 5.6 L9.9 17.3 h4.5 l-1.8 9.1 8.9-12.1 h-4.7 l1.3-8.7 z"
          fill="url(#spark-g)"
          stroke="#0c1f1c"
          strokeWidth="0.8"
          strokeLinejoin="round"
          filter="url(#spark-soft)"
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
