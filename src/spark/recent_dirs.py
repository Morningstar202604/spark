"""最近工作目录记录（体验增强）：切换项目不用每次手打路径。

- 每次保存配置（工作目录非空）时记入最近列表（去重、最多 8 条）；
- 存 ~/.spark/recent_dirs.json；供前端下拉快速选择。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from spark.config import default_home

logger = logging.getLogger(__name__)

MAX_RECENT = 8


def _recent_file() -> Path:
    # 动态取 default_home()：SPARK_HOME 覆盖在调用时生效（勿在 import 时固化路径）
    return default_home() / "recent_dirs.json"


def load_recent() -> list[str]:
    f = _recent_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return [str(x) for x in data if x][:MAX_RECENT]
    except Exception:
        # JSON 损坏或读取失败时返回空列表，下次启动会覆盖
        logger.warning("recent_dirs.json 读取失败，使用空列表", exc_info=True)
        return []


def remember(workdir: str) -> None:
    workdir = (workdir or "").strip()
    if not workdir:
        return
    recent = load_recent()
    recent = [d for d in recent if d != workdir]
    recent.insert(0, workdir)
    f = _recent_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(recent[:MAX_RECENT], ensure_ascii=False), encoding="utf-8")
