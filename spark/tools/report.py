"""数据报表工具：gen_report 生成自包含 HTML 报表（含 SVG 柱状图），零外部依赖。

对标钉钉/通义/飞书 AI 的「数据分析与可视化」：跑完数据直接产出可看的报表文件。
纯 Python 内联 SVG，双击 HTML 即可查看，无需 JS/网络。
"""
from __future__ import annotations

from pathlib import Path

from spark.tools.base import Tool, ToolContext, resolve_path, is_within

MAX_POINTS = 24  # 柱状图最多显示 24 个分类


def _svg_bar(labels: list[str], series: list[dict], height: int = 320) -> str:
    """生成 SVG 柱状图。series = [{name, data: [float]}]。"""
    n = len(labels)
    if not n:
        return "<p>无数据</p>"
    width = max(420, n * 46)
    pad_l, pad_b, pad_t = 56, 46, 22
    plot_w = width - pad_l - 20
    plot_h = height - pad_t - pad_b
    all_vals = [v for s in series for v in s["data"]]
    max_v = max(all_vals) if all_vals else 1
    max_v = max_v * 1.1 or 1
    ser_count = max(1, len(series))
    slot = plot_w / n
    bar_w = min(44, slot * 0.62 / ser_count)

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'style="width:100%;height:auto;font-family:system-ui,sans-serif">'
    )
    # 网格与 Y 轴刻度
    ticks = 5
    for i in range(ticks + 1):
        y = pad_t + plot_h - (plot_h * i / ticks)
        val = max_v * i / ticks
        parts.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - 20}" y2="{y:.1f}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{val:.0f}</text>'
        )
    # X 轴分类
    for i, lb in enumerate(labels):
        cx = pad_l + slot * i + slot / 2
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 18}" text-anchor="middle" font-size="11" fill="#6b7280">{lb}</text>'
        )
    # 柱子
    for s_idx, s in enumerate(series):
        for i, v in enumerate(s["data"]):
            cx = pad_l + slot * i + slot / 2
            bw = slot * 0.62 / ser_count
            x = cx - slot / 2 + (slot * 0.19) + s_idx * bw
            h = (v / max_v) * plot_h
            y = pad_t + plot_h - h
            color = s.get("color") or ("#0d9488" if s_idx % 2 == 0 else "#38bdf8")
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                f'rx="4" fill="{color}"><title>{s["name"]}: {v}</title></rect>'
            )
    parts.append("</svg>")
    # 图例
    legend = "".join(
        f'<span style="margin-right:14px;display:inline-flex;align-items:center;gap:5px">'
        f'<i style="width:10px;height:10px;border-radius:2px;background:{s.get("color") or ("#0d9488" if i % 2 == 0 else "#38bdf8")};display:inline-block"></i>{s["name"]}</span>'
        for i, s in enumerate(series)
    )
    return f'<div style="overflow-x:auto;padding:4px 0">{legend}<div style="min-width:{width}px">{chr(10).join(parts)}</div></div>'


async def _gen_report(args: dict, ctx: ToolContext) -> str:
    path = resolve_path(str(args.get("path", "")), ctx.workdir)
    if not is_within(path, ctx.workdir):
        return "错误：输出路径需在工作目录内"
    title = str(args.get("title") or "数据报表")
    note = str(args.get("note") or "")
    columns = [str(c) for c in (args.get("columns") or [])]
    rows = args.get("rows") or []
    if not rows:
        return "错误：rows 需为数据行（[{...}, ...]）"

    # 解析 rows：list[dict] → 每个 dict 一列分类 + 数值列
    keys = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    if not keys:
        return "错误：rows 每行应为对象，如 {\"分类\":\"华东\",\"销售额\":100}"
    label_key = keys[0]
    labels = [str(r[label_key]) for r in rows][:MAX_POINTS]
    series = []
    for k in keys[1:]:
        data = []
        for r in rows[:MAX_POINTS]:
            try:
                data.append(float(r.get(k, 0) or 0))
            except (TypeError, ValueError):
                data.append(0.0)
        if any(data):
            series.append({"name": k, "data": data})

    # 表格
    thead = "".join(f"<th style='text-align:left;padding:6px 10px;border-bottom:1px solid #e5e7eb'>{c}</th>" for c in keys)
    tbody = ""
    for r in rows[:MAX_POINTS]:
        tds = "".join(f"<td style='padding:6px 10px;border-bottom:1px solid #f3f4f6'>{r.get(c)}</td>" for c in keys)
        tbody += f"<tr>{tds}</tr>"

    chart_html = _svg_bar(labels, series) if series else "<p style='color:#6b7280'>无数值列可绘图</p>"

    html = f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;background:#f6f8fb;color:#1a2433;font-family:system-ui,'PingFang SC','Noto Sans SC',sans-serif">
<div style="max-width:960px;margin:0 auto;padding:24px 20px">
  <h1 style="font-size:20px;margin:0 0 4px">{title}</h1>
  {('<p style="margin:0 0 16px;color:#55647a;font-size:13px">' + note + '</p>') if note else ''}
  <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin-bottom:16px">
    {chart_html}
  </div>
  <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:8px;overflow-x:auto">
    <table style="border-collapse:collapse;width:100%;font-size:13px">{thead}</table>
  </div>
  <p style="color:#9aa3b2;font-size:12px;margin-top:12px">由 Spark 生成 · {len(rows)} 条数据</p>
</div></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return f"已生成 HTML 报表：{path}（含柱状图 + 表格，{len(rows)} 条数据）"


def build_report_tools() -> list[Tool]:
    return [
        Tool(
            name="gen_report",
            description="生成一份带柱状图的数据分析 HTML 报表（自包含、零依赖，双击即可查看）。rows 为对象数组（每行含分类与数值列），columns 可选。用于把分析结果产出为可视化报表文件。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "输出 .html 报表路径"},
                    "title": {"type": "string", "description": "报表标题"},
                    "rows": {"type": "array", "description": "数据行，如 [{\"月份\":\"1月\",\"销售额\":120}, ...]"},
                    "columns": {"type": "array", "description": "可选列说明（纯展示）"},
                    "note": {"type": "string", "description": "可选说明文字"},
                },
                "required": ["path", "rows"],
            },
            category="write",
            handler=_gen_report,
        ),
    ]