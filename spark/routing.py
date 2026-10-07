"""模型路由：根据用户任务复杂度在快/强模型间选择。

优先使用 semantic-router（嵌入向量语义匹配），不可用时退回关键词子串
匹配（STRONG_TASK_KEYWORDS）。可在设置里关闭（route_enabled=False）或自
定义关键词（route_keywords）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Fallback keyword matching（semantic-router 不可用时的兜底）────────────
# 这些关键词仅在没有嵌入 API 或 semantic-router 调用失败时使用，与旧版
# 行为完全一致，保证降级后仍可工作。
STRONG_TASK_KEYWORDS = (
    "写",
    "改",
    "修",
    "重构",
    "实现",
    "创建",
    "删除",
    "迁移",
    "优化",
    "修复",
    "报错",
    "异常",
    "部署",
    "编译",
    "运行",
    "测试",
    "接口",
    "登录",
    "配置",
    "bug",
    "fix",
    "test",
    "deploy",
    "refactor",
    "compile",
)


def _route_keywords(cfg: dict) -> tuple[str, ...]:
    """自定义强任务关键词（逗号/空格/换行分隔）；未配置 → 内置词表。"""
    raw = str(cfg.get("route_keywords") or "").strip()
    if not raw:
        return STRONG_TASK_KEYWORDS
    parts = [p.lower() for p in re.split(r"[,\uff0c\s]+", raw) if p]
    return tuple(parts) if parts else STRONG_TASK_KEYWORDS


def _keyword_route(cfg: dict[str, Any], user_text: str) -> str | None:
    """关键词子串匹配路由（兜底逻辑，与旧版完全一致）。"""
    t = (user_text or "").lower()
    for kw in _route_keywords(cfg):
        if kw in t:
            return None
    return cfg.get("model_fast") or None


# ── Semantic-router 集成（可选）──────────────────────────────────────────
# 模块级缓存：避免每轮对话重建 encoder / router。
_router: Any = None
_router_cfg_key: int = 0  # 用配置 hash 检测是否需要重建


def _cfg_key(cfg: dict[str, Any]) -> int:
    """取影响路由决策的配置项生成 hash，配置不变 → 复用缓存。"""
    return hash((
        cfg.get("base_url", ""),
        cfg.get("api_key", ""),
        cfg.get("embed_model", ""),
        cfg.get("route_model_name", ""),
    ))


def _build_router(cfg: dict[str, Any]):
    """构造 semantic-router Router 实例；任何失败 → 返回 None。"""
    try:
        from semantic_router import Route
        from semantic_router.encoders import OpenAIEncoder
        from semantic_router.layer import Router
    except ImportError:
        logger.debug("semantic-router 未安装，使用关键词路由")
        return None

    base_url = (cfg.get("base_url") or "").rstrip("/")
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        logger.debug("semantic-router: 缺少 api_key，使用关键词路由")
        return None

    embed_model = (
        cfg.get("embed_model")
        or cfg.get("memory_embed_model")
        or "text-embedding-3-small"
    )

    try:
        kwargs: dict[str, Any] = {"name": embed_model}
        # OpenAIEncoder 在较新版本支持 base_url，旧版不支持 → try/except
        if base_url:
            try:
                encoder = OpenAIEncoder(
                    api_key=api_key, base_url=base_url, **kwargs
                )
            except TypeError:
                # 旧版 OpenAIEncoder 不接受 base_url 参数
                encoder = OpenAIEncoder(api_key=api_key, **kwargs)
        else:
            encoder = OpenAIEncoder(api_key=api_key, **kwargs)
    except Exception as exc:
        logger.warning("semantic-router encoder 初始化失败：%s", exc)
        return None

    # 强任务样本：编码、调试、重构、搭建、改动类
    strong_route = Route(
        name="strong_task",
        utterances=[
            # 中文
            "写一个完整的 web 应用",
            "帮我写一个脚本读取这个文件",
            "实现复杂的算法",
            "重构这个模块的代码",
            "帮我修改这段代码",
            "修复这个 bug",
            "实现用户登录注册功能",
            "创建数据库迁移脚本",
            "优化这段 SQL 查询的性能",
            "部署到服务器",
            "编写单元测试和集成测试",
            "配置 CI/CD 流程",
            "分析这个报错信息并修复",
            "调优代码性能",
            "搭建一个新的后端服务",
            "帮我做一个登录页面",
            "写一个完整的爬虫程序",
            "修复测试失败的问题",
            "重构数据访问层代码",
            "给这个项目加上缓存",
            "实现一个 REST API",
            "帮我调试这个程序",
            "修改配置文件",
            "编译整个项目",
            "运行测试用例",
            # English
            "write a complete web application",
            "implement a complex algorithm",
            "refactor this code",
            "debug this issue",
            "fix this error",
            "create a new feature",
            "deploy the application",
            "optimize performance",
            "write unit tests",
            "set up a database migration",
            "build a REST API",
            "analyze this error message",
            "add authentication to the app",
            "configure CI/CD pipeline",
        ],
    )

    # 弱任务样本：闲聊、简单问好、非动手类
    weak_route = Route(
        name="weak_task",
        utterances=[
            # 中文
            "你好",
            "今天天气怎么样",
            "谢谢你",
            "再见",
            "你好呀",
            "早上好",
            "晚上好",
            "讲个笑话",
            "你是什么",
            "现在几点了",
            "今天日期是多少",
            "最近有什么新闻",
            "给我打个招呼",
            "你叫什么名字",
            "跟我说声 hi",
            # English
            "hello",
            "hi",
            "how are you",
            "what's up",
            "good morning",
            "good night",
            "tell me a joke",
            "thank you",
            "bye",
            "what time is it",
            "who are you",
            "good afternoon",
            "see you later",
        ],
    )

    try:
        router = Router(
            encoder=encoder,
            routes=[strong_route, weak_route],
            auto_sync="local",
        )
    except Exception as exc:
        logger.warning("semantic-router Router 初始化失败：%s", exc)
        return None

    logger.info("semantic-router 就绪（embed_model=%s）", embed_model)
    return router


def _get_router(cfg: dict[str, Any]):
    """取缓存的 semantic-router，配置变更或首次使用时构建。"""
    global _router, _router_cfg_key
    key = _cfg_key(cfg)
    if _router is None or _router_cfg_key != key:
        _router = _build_router(cfg)
        _router_cfg_key = key
    return _router


# ── 公共接口 ──────────────────────────────────────────────────────────────
def route_model(cfg: dict[str, Any], user_text: str) -> str | None:
    """返回本轮应使用的模型名；None = 用主模型（不路由）。

    规则（符合人的直觉）：
    - 简单问答 / 闲聊 → 走快模型省时省钱
    - 涉及改动、排错、搭建等动手任务 → 走主模型保证质量

    实现优先使用 semantic-router 做语义匹配，若库未安装、API 调用失败
    或返回不确定，则退化为 STRONG_TASK_KEYWORDS 子串匹配。
    """
    # ── 前置检查（与旧版一致，短路返回 None）──
    if cfg.get("route_enabled") is False:
        return None
    fast = (cfg.get("model_fast") or "").strip()
    main = cfg.get("model") or ""
    if not fast or fast == main or main == "mock":
        return None

    # ── 尝试 semantic-router 语义路由 ──
    router = _get_router(cfg)
    if router is not None:
        try:
            match = router(user_text)
            route_name = getattr(match, "name", None) if match else None
            if route_name == "strong_task":
                return None  # 强任务走主模型
            # weak_task 或无匹配 → 快模型（简单问题默认走快模型更安全）
            return fast
        except Exception as exc:  # noqa: BLE001
            logger.debug("semantic-router 路由失败，退回关键词：%s", exc)

    # ── Fallback：关键词子串匹配（旧版逻辑）──
    return _keyword_route(cfg, user_text)
