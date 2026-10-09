import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function fmtTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}月${dd}日 ${hh}:${mi}`;
}

export function shortPath(p: string): string {
  const s = String(p);
  const sep = s.includes("\\") ? "\\" : "/";
  const seg = s.split(/[\\/]+/).filter(Boolean);
  if (seg.length <= 3) return s;
  const head = seg[0]; // Windows 盘符（C:）或根段
  const tail = seg.slice(-2).join(sep);
  return head + sep + "…" + sep + tail;
}

export function fmtTokens(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

export function uid(prefix = ""): string {
  return prefix + Math.random().toString(36).slice(2, 10);
}
