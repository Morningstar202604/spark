"""配置/元数据 API 路由：config、test-connection、recent-dirs、usage、plugins。

从 server.py 拆分（原 create_app 内"配置""用量""插件"分组端点，行为不变）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from spark.config import PRESETS, apply_preset, is_masked_key, save_config
from spark.memory import MemoryStore, make_embedder
from spark.provider import test_connection

from .api_common import AppState, check_token, config_payload, get_app_state

router = APIRouter()


@router.get("/api/config")
async def get_config(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    return config_payload(state)


@router.post("/api/config")
async def set_config(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await request.json()
    # provider 变化先应用预设（切换服务重置 base_url/model），
    # 随后显式字段覆盖预设：同 provider 再保存时，用户手填的第三方端点不被回滚
    new_provider = body.get("provider")
    if new_provider in PRESETS and new_provider != state.cfg.get("provider"):
        apply_preset(state.cfg, new_provider)
    for k in (
        "base_url",
        "proxy",
        "model",
        "workdir",
        "approval_mode",
        "model_fast",
        "memory_embed_model",
        "embed_base_url",
    ):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            state.cfg[k] = v.strip()
    # fallback_model 允许显式清空（区别于上面"空串跳过"的通用字段）
    if "fallback_model" in body and isinstance(body.get("fallback_model"), str):
        state.cfg["fallback_model"] = body["fallback_model"].strip()
    v = body.get("max_context_tokens")
    if isinstance(v, int) and v > 0:
        state.cfg["max_context_tokens"] = v
    if body.get("memory_embedding") in ("off", "volcengine", "openai", "local"):
        state.cfg["memory_embedding"] = body["memory_embedding"]
    key = body.get("api_key")
    if isinstance(key, str) and key and not is_masked_key(key):
        state.cfg["api_key"] = key
    # 独立语义嵌入密钥：与 api_key 同样的"打码不回写"规则
    ek = body.get("embed_api_key")
    if isinstance(ek, str) and ek and not is_masked_key(ek):
        state.cfg["embed_api_key"] = ek
    if new_provider == "mock":
        # 演示模式不联网：清掉本轮可能写入的密钥，不留残留
        state.cfg["api_key"] = ""
    # 访问令牌：显式提供 token 键才改（空字符串 = 清除，恢复本机免登录）
    if "token" in body and isinstance(body.get("token"), str):
        state.cfg["token"] = body["token"].strip()
    # MCP 服务器列表（本地 stdio / 远程 Streamable HTTP）
    ms = body.get("mcp_servers")
    if isinstance(ms, list):
        cleaned = []
        for item in ms:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            transport = str(item.get("transport", "stdio")).strip().lower()
            if transport not in ("stdio", "http"):
                transport = "stdio"
            command = str(item.get("command", "")).strip()
            url = str(item.get("url", "")).strip()
            if transport == "http":
                if not url:
                    continue
            elif not command:
                continue
            cleaned.append(
                {
                    "name": name,
                    "transport": transport,
                    "command": command,
                    "args": [str(a) for a in item.get("args") or []],
                    "url": url,
                    "headers": (
                        {str(k): str(v) for k, v in item["headers"].items()}
                        if isinstance(item.get("headers"), dict)
                        else {}
                    ),
                    "env": (
                        {str(k): str(v) for k, v in item["env"].items()}
                        if isinstance(item.get("env"), dict)
                        else {}
                    ),
                }
            )
        state.cfg["mcp_servers"] = cleaned
        state.reload_mcp()
    # ---- 高级可调项（类型/范围校验后才落盘） ----
    if "system_prompt" in body and isinstance(body.get("system_prompt"), str):
        state.cfg["system_prompt"] = body["system_prompt"][:20000]
    if "protected_paths" in body and isinstance(body.get("protected_paths"), list):
        cleaned_paths = []
        for item in body["protected_paths"][:50]:
            if isinstance(item, str) and item.strip():
                cleaned_paths.append(item.strip())
        state.cfg["protected_paths"] = cleaned_paths
    if "max_turns" in body:
        try:
            state.cfg["max_turns"] = max(1, min(200, int(body["max_turns"])))
        except (TypeError, ValueError):
            pass  # 无效值保留原配置
    if "tool_timeout" in body:
        try:
            state.cfg["tool_timeout"] = max(
                10, min(3600, int(body["tool_timeout"]))
            )
        except (TypeError, ValueError):
            pass
    if "temperature" in body:
        raw = body.get("temperature")
        try:
            if raw is None or str(raw).strip() == "":
                state.cfg["temperature"] = ""
            else:
                state.cfg["temperature"] = max(0.0, min(2.0, float(raw)))
        except (TypeError, ValueError):
            pass  # 无效值保留原配置
    if "max_tokens" in body:
        raw = body.get("max_tokens")
        try:
            if raw is None or str(raw).strip() == "":
                state.cfg["max_tokens"] = ""
            else:
                state.cfg["max_tokens"] = max(1, min(1_000_000, int(raw)))
        except (TypeError, ValueError):
            pass
    if isinstance(body.get("route_enabled"), bool):
        state.cfg["route_enabled"] = body["route_enabled"]
    # 改完自动验证（apply_patch 成功后跑受影响测试）：布尔开关，仅接受真正的 bool，
    # 否则前端关掉后 GET 回读不到、开关会"弹回"成开启
    if isinstance(body.get("auto_verify"), bool):
        state.cfg["auto_verify"] = body["auto_verify"]
    if "route_keywords" in body and isinstance(body.get("route_keywords"), str):
        state.cfg["route_keywords"] = body["route_keywords"][:2000]
    if "usage_pricing" in body and isinstance(body.get("usage_pricing"), dict):
        pricing: dict[str, dict] = {}
        for mk, mv in list(body["usage_pricing"].items())[:100]:
            if not isinstance(mk, str) or not isinstance(mv, dict):
                continue
            try:
                pricing[mk.strip()] = {
                    "input": float(mv.get("input", 0)),
                    "output": float(mv.get("output", 0)),
                }
            except (TypeError, ValueError):
                continue
        state.cfg["usage_pricing"] = pricing
        # 单价立即生效（重建 usage 的价格表，不丢历史）
        from spark.usage import DEFAULT_PRICING

        state.usage.pricing = {**DEFAULT_PRICING, **pricing}
    save_config(state.cfg)
    # 工作目录记入最近列表（切换项目不用每次手打路径）
    try:
        if state.cfg.get("workdir"):
            from spark.recent_dirs import remember

            remember(str(state.cfg["workdir"]))
    except Exception:  # noqa: BLE001
        pass
    # 语义记忆开关变化后重建记忆库嵌入器（下次对话生效）
    try:
        state.memory = MemoryStore(embedder=make_embedder(state.cfg))
    except Exception:  # noqa: BLE001
        pass
    return config_payload(state)  # 入口已鉴权；不再经 get_config 二次校验


@router.post("/api/test-connection")
async def test(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    check_token(request, state)
    body = await request.json()
    probe = dict(state.cfg)
    for k in ("base_url", "model", "proxy"):
        if body.get(k):
            probe[k] = body[k]
    key = body.get("api_key")
    if isinstance(key, str) and key and not set(key) <= {"*"}:
        probe["api_key"] = key
    ok, msg = await test_connection(probe)
    return {"ok": ok, "message": msg}


@router.get("/api/recent-dirs")
async def get_recent_dirs(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """最近使用的工作目录（最多 8 条，供前端快速选择）。"""
    check_token(request, state)
    from spark.recent_dirs import load_recent

    return {"dirs": load_recent()}


@router.get("/api/usage")
async def get_usage(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """用量统计：?session_id=xxx 查单会话；否则查全局总览。"""
    check_token(request, state)
    sid = (request.query_params.get("session_id") or "").strip()
    if sid:
        return state.usage.session_summary(sid)
    return state.usage.global_summary()


@router.get("/api/plugins")
async def get_plugins(request: Request, state: AppState = Depends(get_app_state)) -> dict:
    """插件列表：已加载工具与失败原因（页面只读展示，插件在本地目录增删后重启生效）。"""
    check_token(request, state)
    out = []
    for name, info in state.plugins.items():
        out.append(
            {
                "name": name,
                "tools": [t.name for t in info.get("tools", [])],
                "error": info.get("error"),
            }
        )
    return {
        "dir": str(state.plugin_dir),
        "plugins": out,
        "tool_count": len(state.plugin_tools),
    }


@router.get("/api/slash-commands")
async def get_slash_commands(
    request: Request, state: AppState = Depends(get_app_state)
) -> dict:
    """斜杠命令预设列表（供前端输入框提示使用）。"""
    check_token(request, state)
    from spark.slash import list_commands

    return {"commands": list_commands()}
