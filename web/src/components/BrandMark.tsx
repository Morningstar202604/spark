import { cls } from "./ui"

/**
 * BrandMark — Spark 品牌徽标：渐变圆角方块 + 星火符号。
 * 用于侧边栏顶部、空状态、设置面板品牌卡、favicon。
 */
export function BrandMark({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <span
      className={cls("inline-flex shrink-0 items-center justify-center rounded-xl", className)}
      style={{
        width: size,
        height: size,
        background: "linear-gradient(135deg, var(--accent) 0%, #f59e0b 55%, #fbbf24 100%)",
        boxShadow: "0 1px 2px rgba(0,0,0,.12), 0 4px 12px -4px rgba(249,115,22,.45)",
      }}
      aria-hidden="true"
    >
      <svg width={size * 0.58} height={size * 0.58} viewBox="0 0 24 24" fill="none">
        <path
          d="M12 2c.6 3.8 2.1 5.9 5.6 7.1-.6 1-1.2 1.9-1.5 3.2M12 2C9.8 5.2 7.9 7 4.5 7.9c.8 1.3 1.5 2.3 2.3 3.4M12 2c1.1 4.6 3.1 7.5 7.2 8.9M12 22c-.5-3.2-1.9-5-4.9-6.1.6-.9 1-1.6 1.4-2.8M12 22c1.9-2.8 3.5-4.3 6.6-5.2-.7-1.1-1.3-1.9-2.1-2.8M12 22c-.9-4-2.7-6.4-6.4-7.6"
          stroke="white"
          strokeWidth="2.1"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M12 9v6M9 12h6" stroke="white" strokeWidth="2.1" strokeLinecap="round" />
      </svg>
    </span>
  )
}
