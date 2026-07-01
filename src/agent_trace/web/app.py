# src/agent_trace/web/app.py
"""FastAPI 后端 — REST API + WebSocket 实时流

端点:
  GET  /                           前端页面
  GET  /api/traces                 列表（分页）
  GET  /api/traces/{tid}           详情（含 observations）
  GET  /api/traces/{tid}/graph     agent 调用图（cytoscape 格式）
  GET  /api/traces/{tid}/flame     span 树（d3-flame-graph 格式）
  WS   /ws/stream                  实时 trace 事件流
  GET  /api/health                 健康检查
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from agent_trace.storage import SQLiteBackend, StorageBackend
from agent_trace.storage.base import TraceQuery


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if is_dataclass(obj):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


_STATIC_DIR = Path(__file__).parent / "static"


def create_app(storage: StorageBackend | None = None) -> FastAPI:
    """创建 FastAPI 应用

    storage: 注入的存储后端，None 时用临时 SQLite
    """
    if storage is None:
        storage = SQLiteBackend(":memory:")

    app = FastAPI(title="Agent Trace", version="0.1.0")
    app.state.storage = storage
    app.state.ws_clients: set[WebSocket] = set()

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/traces")
    async def list_traces(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        query = TraceQuery(limit=limit, offset=offset)
        traces = storage.list_traces(query)
        return {"traces": [_to_jsonable(t) for t in traces], "limit": limit, "offset": offset}

    @app.get("/api/traces/{trace_id}")
    async def get_trace(trace_id: str) -> JSONResponse:
        trace = storage.get_trace(trace_id)
        if trace is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        observations = storage.list_observations_by_trace(trace_id)
        scores = storage.list_scores_by_trace(trace_id)
        return JSONResponse(
            {
                "trace": _to_jsonable(trace),
                "observations": [_to_jsonable(o) for o in observations],
                "scores": [_to_jsonable(s) for s in scores],
            }
        )

    @app.get("/api/traces/{trace_id}/graph")
    async def get_call_graph(trace_id: str) -> JSONResponse:
        observations = storage.list_observations_by_trace(trace_id)
        nodes_map: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for obs in observations:
            agent_id = obs.agent_id
            if agent_id and agent_id not in nodes_map:
                nodes_map[agent_id] = {
                    "data": {"id": agent_id, "label": agent_id},
                }
            op = obs.operation_name or ""
            if "handoff" in op.lower() or "invoke" in op.lower():
                input_data = obs.input or "{}"
                target = _extract_target_agent(input_data)
                if target and target != agent_id and target not in nodes_map:
                    nodes_map[target] = {
                        "data": {"id": target, "label": target},
                    }
                if target and target != agent_id:
                    edges.append(
                        {"data": {"source": agent_id, "target": target, "id": f"{agent_id}->{target}"}}
                    )
        return JSONResponse(
            {
                "nodes": list(nodes_map.values()),
                "edges": edges,
            }
        )

    @app.get("/api/traces/{trace_id}/flame")
    async def get_flame_graph(trace_id: str) -> JSONResponse:
        observations = storage.list_observations_by_trace(trace_id)
        root = _build_flame_tree(observations)
        return JSONResponse(root)

    @app.websocket("/ws/stream")
    async def ws_stream(ws: WebSocket) -> None:
        await ws.accept()
        app.state.ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            app.state.ws_clients.discard(ws)

    async def broadcast(event: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for client in app.state.ws_clients:
            try:
                await client.send_text(json.dumps(event))
            except Exception:
                dead.append(client)
        for d in dead:
            app.state.ws_clients.discard(d)

    @app.post("/api/traces/{trace_id}/event")
    async def inject_event(trace_id: str, event: dict[str, Any]) -> dict[str, str]:
        await broadcast({"trace_id": trace_id, **event})
        return {"status": "broadcasted"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = _STATIC_DIR / "index.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    return app


def _extract_target_agent(input_data: str) -> str | None:
    try:
        data = json.loads(input_data) if isinstance(input_data, str) else input_data
        if isinstance(data, dict):
            return data.get("target_agent") or data.get("to_agent") or data.get("delegate_to")
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _build_flame_tree(observations: list[Any]) -> dict[str, Any]:
    """构建 d3-flame-graph 兼容的树结构

    d3-flame-graph 格式: {name, value, children: [...]}
    value = token 数 (input + output) 或 duration
    """
    if not observations:
        return {"name": "root", "value": 1, "children": []}

    by_parent: dict[str | None, list[Any]] = {}
    for obs in observations:
        parent_id = obs.parent_observation_id
        by_parent.setdefault(parent_id, []).append(obs)

    def build_node(obs: Any) -> dict[str, Any]:
        name = f"{obs.agent_id or 'unknown'}:{obs.operation_name or 'op'}"
        # 旧: value 仅算自身 token,导致子节点 token 大于父节点时溢出父条 -> 火焰图错位
        # value = (obs.input_tokens or 0) + (obs.output_tokens or 0)
        # 新: 火焰图铁律要求父宽度 >= 子之和,故 value = 自身 token + 所有子孙累积
        self_tokens = (obs.input_tokens or 0) + (obs.output_tokens or 0)
        children = [build_node(c) for c in by_parent.get(obs.id, [])]
        value = self_tokens + sum(c["value"] for c in children)
        if value == 0:
            value = 1
        # 旧: return {"name": name, "value": value, "children": children}
        # 新: 附带 self 字段(该步自身 token,不含子树),供前端按热点上色
        return {"name": name, "value": value, "self": self_tokens, "children": children}

    roots = by_parent.get(None, [])
    if not roots:
        roots = [observations[0]]

    root_value = sum(
        (o.input_tokens or 0) + (o.output_tokens or 0) or 1 for o in observations
    )
    children = [build_node(r) for r in roots]
    return {"name": "trace", "value": root_value, "children": children}
