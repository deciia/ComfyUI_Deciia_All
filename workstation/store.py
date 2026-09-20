"""Deciia 工作台：工具清单持久化 + 服务探测 + 节点/侧栏契约。

设计要点（作者 deciia）：
- 工作台本身是「启动器」：工具条目由用户自行增删，不写死在代码里；
- 清单持久化在 ComfyUI 用户目录（user/default/deciia_workstation/tools.json），
  与工作流无关 —— 换工作流/换标签页都看到同一份工具；
- 后端只做两件事：读写清单（带 revision 冲突检测）、探测目标 URL 是否在线；
- 前端（web/deciia_workstation.js）负责侧栏面板、卡片渲染、内嵌/新窗打开。

条目字段：
    id          稳定标识（前端生成 uuid）
    name        显示名
    url         目标地址（支持站内相对路径 /directordeck/ 或绝对 http://host:port/）
    icon        emoji 图标
    hint        右侧小字（端口/说明）
    open_mode   embed=ComfyUI 内浮层 iframe；new_window=浏览器新窗口
    status_url  可选，探测地址（缺省用 url）
    status_mode 可选，'json_field:<key>' 取字段；'http' 仅看 HTTP 200
    enabled     是否显示
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from functools import wraps
from pathlib import Path

SCHEMA = "deciia.workstation.tools"
VERSION = 1
PREFIX = "/deciia_workstation"
_STORE_DIR = ("deciia_workstation",)
_LOCK = asyncio.Lock()

OPEN_MODES = ("embed", "new_window")
STATUS_MODES = ("http", "json_field", "none")

DEFAULT_TOOLS = [
    {
        "id": "obsidian-director",
        "name": "曜石导演台",
        "url": "/minimax_h3_t8/director/ui",
        "icon": "🎬",
        "hint": "T8",
        "open_mode": "embed",
        "status_url": "/minimax_h3_t8/director/capabilities",
        "status_mode": "http",
        "status_label": "T8 导演台",
        "enabled": True,
    },
    {
        "id": "directordeck",
        "name": "Director",
        "url": "/directordeck/",
        "icon": "🎥",
        "hint": "DirectorDeck",
        "open_mode": "new_window",
        "status_url": "/directordeck/status",
        "status_mode": "json_field:backend",
        "status_label": "Director 后端",
        "enabled": True,
    },
    {
        "id": "dreamifly",
        "name": "Dreamifly",
        "url": "/dreamifly/",
        "icon": "🎨",
        "hint": ":3000",
        "open_mode": "new_window",
        "status_url": "/dreamifly/",
        "status_mode": "http",
        "status_label": "Dreamifly",
        "enabled": True,
    },
    {
        "id": "nexus-bta",
        "name": "NEXUS BTA Studio",
        "url": "/nexus-bta/",
        "icon": "🤖",
        "hint": ":7861",
        "open_mode": "new_window",
        "status_url": "/nexus-bta/",
        "status_mode": "http",
        "status_label": "NEXUS BTA",
        "enabled": True,
    },
]


# --------------------------------------------------------------------------- 路径

def _store_path() -> Path:
    import folder_paths

    root = Path(folder_paths.get_user_directory()) / _STORE_DIR[0]
    root.mkdir(parents=True, exist_ok=True)
    return root / "tools.json"


# --------------------------------------------------------------------------- 校验

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _clean_url(value: str) -> str:
    """只允许站内相对路径或 http(s) 绝对地址；拒绝其它协议与脚本注入。"""
    text = str(value or "").strip()
    if not text:
        raise ValueError("地址不能为空")
    if len(text) > 2048:
        raise ValueError("地址过长")
    if text.startswith("/"):
        if text.startswith("//"):
            raise ValueError("地址不允许以 // 开头")
        return text
    if re.match(r"^https?://", text, re.I):
        return text
    raise ValueError("地址必须是站内路径（/xxx）或 http(s):// 开头的完整地址")


def normalize_tool(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("工具条目必须是对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("工具名称不能为空")
    if len(name) > 80:
        raise ValueError("工具名称过长（≤80 字）")
    tool_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
    if not _ID_RE.match(tool_id):
        raise ValueError("工具 id 非法（仅字母数字与 _ . : -）")
    open_mode = str(raw.get("open_mode") or "embed").strip()
    if open_mode not in OPEN_MODES:
        raise ValueError(f"open_mode 必须是 {'/'.join(OPEN_MODES)}")
    status_mode = str(raw.get("status_mode") or "http").strip()
    key = status_mode.split(":", 1)[1].strip() if status_mode.startswith("json_field:") else ""
    if status_mode.startswith("json_field:"):
        if not key:
            raise ValueError("json_field 模式需要字段名，如 json_field:backend")
        status_mode = f"json_field:{key}"
    elif status_mode not in STATUS_MODES:
        raise ValueError(f"status_mode 必须是 {'/'.join(STATUS_MODES)} 或 json_field:<key>")
    return {
        "id": tool_id,
        "name": name,
        "url": _clean_url(raw.get("url")),
        "icon": str(raw.get("icon") or "🧩")[:8],
        "hint": str(raw.get("hint") or "")[:40],
        "open_mode": open_mode,
        "status_url": _clean_url(raw["status_url"]) if raw.get("status_url") else "",
        "status_mode": status_mode,
        "status_label": str(raw.get("status_label") or "")[:40],
        "enabled": bool(raw.get("enabled", True)),
    }


def normalize_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是对象")
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError("tools 必须是数组")
    if len(tools) > 64:
        raise ValueError("工具数量上限 64")
    cleaned = [normalize_tool(t) for t in tools]
    ids = [t["id"] for t in cleaned]
    if len(ids) != len(set(ids)):
        raise ValueError("工具 id 重复")
    return {"tools": cleaned}


# --------------------------------------------------------------------------- 存储

def load_tools() -> dict:
    path = _store_path()
    if not path.exists():
        payload = {"schema": SCHEMA, "version": VERSION, "revision": 0,
                   "updated_at": time.time(), "tools": [normalize_tool(t) for t in DEFAULT_TOOLS]}
        _write(path, payload)
        return payload
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(f".broken-{int(time.time())}.json")
        path.replace(backup)
        payload = {"schema": SCHEMA, "version": VERSION, "revision": 0,
                   "updated_at": time.time(), "tools": [normalize_tool(t) for t in DEFAULT_TOOLS]}
        _write(path, payload)
        return payload
    body = normalize_payload(data)
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "revision": int(data.get("revision") or 0),
        "updated_at": float(data.get("updated_at") or time.time()),
        "tools": body["tools"],
    }


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_tools(payload: dict, expected_revision: int | None) -> dict:
    current = load_tools()
    if expected_revision is not None and int(expected_revision) != current["revision"]:
        raise RuntimeError(
            f"清单已被其它标签页修改（当前 revision={current['revision']}，"
            f"你基于 revision={expected_revision}）——请刷新后重试"
        )
    body = normalize_payload(payload)
    fresh = dict(current)
    fresh.update({"tools": body["tools"], "revision": current["revision"] + 1,
                  "updated_at": time.time()})
    _write(_store_path(), fresh)
    return fresh


# --------------------------------------------------------------------------- 服务探测

def _local_port() -> int:
    """本机 ComfyUI 端口：优先取运行实例，其次环境变量，最后 8188。"""
    try:
        from server import PromptServer

        port = getattr(PromptServer.instance, "port", None)
        if port:
            return int(port)
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(os.environ.get("COMFYUI_PORT", "8188"))
    except ValueError:
        return 8188

async def probe(url: str, mode: str, timeout: float = 2.5) -> dict:
    """探测目标服务是否在线。站内相对路径走本机回环。"""
    import aiohttp

    target = url
    if url.startswith("/"):
        target = f"http://127.0.0.1:{_local_port()}{url}"
    if mode == "none":
        return {"ok": None, "detail": "未启用探测"}
    started = time.perf_counter()
    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(target, allow_redirects=True) as resp:
                elapsed = int((time.perf_counter() - started) * 1000)
                if resp.status >= 400:
                    return {"ok": False, "status": resp.status, "detail": f"HTTP {resp.status}", "ms": elapsed}
                if mode.startswith("json_field:"):
                    key = mode.split(":", 1)[1]
                    try:
                        body = await resp.json(content_type=None)
                    except Exception:
                        return {"ok": False, "status": resp.status, "detail": "返回不是 JSON", "ms": elapsed}
                    value = body.get(key) if isinstance(body, dict) else None
                    ok = value not in (None, "", False)
                    return {"ok": bool(ok), "status": resp.status, "value": value,
                            "detail": f"{key}={value}" if value is not None else f"字段 {key} 缺失",
                            "ms": elapsed}
                return {"ok": True, "status": resp.status, "detail": f"HTTP {resp.status}", "ms": elapsed}
    except asyncio.TimeoutError:
        return {"ok": False, "detail": f"超时（{timeout}s）"}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(error).__name__}: {error}"[:160]}


# --------------------------------------------------------------------------- 路由

_REGISTERED = False


def register_routes() -> bool:
    global _REGISTERED
    if _REGISTERED:
        return True
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return False
    server = getattr(PromptServer, "instance", None)
    if server is None:
        return False
    routes = server.routes

    @routes.get(PREFIX + "/tools")
    async def get_tools(request):
        async with _LOCK:
            data = await asyncio.to_thread(load_tools)
        return web.json_response(data)

    @routes.post(PREFIX + "/tools")
    async def set_tools(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
        async with _LOCK:
            try:
                data = await asyncio.to_thread(
                    save_tools, payload, payload.get("expected_revision")
                )
            except RuntimeError as conflict:
                return web.json_response({"error": str(conflict), "code": "revision_conflict"}, status=409)
            except ValueError as bad:
                return web.json_response({"error": str(bad)}, status=400)
        return web.json_response(data)

    @routes.post(PREFIX + "/probe")
    async def probe_tool(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
        try:
            url = _clean_url(payload.get("url"))
        except ValueError as bad:
            return web.json_response({"error": str(bad)}, status=400)
        mode = str(payload.get("status_mode") or "http")
        result = await probe(url, mode)
        return web.json_response(result)

    _REGISTERED = True
    return True
