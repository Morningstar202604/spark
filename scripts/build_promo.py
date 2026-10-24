import pathlib
import subprocess

shots = [
    ("10-empty-state.png", "Spark · 本地优先的 AI 编程 Agent"),
    ("11-chat.png", "真实模型 · 工具调用 · 流式回答"),
    ("20-inline-shell.png", "!cmd 内联 shell · 结果不进模型上下文"),
    ("12-settings-model.png", "任意 OpenAI 兼容端点 · 多模型档案"),
    ("13-settings-permissions.png", "六档审批 × 四档沙箱"),
    ("21-settings-display.png", "桌面通知 · 主题 · 中英双语"),
    ("16-file-viewer.png", "文件查看器 · 语法高亮"),
    ("15-command-palette.png", "命令面板 · 单文件 HTML 分享"),
    ("24-mobile-shell.png", "移动端同样完整"),
]
font = "C\\:/Windows/Fonts/msyh.ttc"
out = pathlib.Path("assets/video_frames")
out.mkdir(exist_ok=True)
for i, (name, cap) in enumerate(shots):
    src = pathlib.Path("assets/screenshots") / name
    dst = out / f"frame{i:02d}.png"
    # 先缩放到 1600x960（留 140px 字幕带），再在底部黑带上写标题：
    # drawtext 的 y 是相对 pad 后画布，早先 y=h-78 会落到画布外。
    vf = (
        "scale=1600:960:force_original_aspect_ratio=decrease,"
        "pad=1600:1100:(ow-iw)/2:0:color=0x14171c,"
        f"drawtext=fontfile='{font}':text='{cap}':fontcolor=white:fontsize=38:"
        "x=(w-text_w)/2:y=1000"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-update", "1", str(dst)],
        check=True,
    )
    print("frame", dst.name)
