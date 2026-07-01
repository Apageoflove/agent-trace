# tests/test_web.py
"""M6 门禁测试: Web 可视化后端

门禁: REST API 100% 正确 + WebSocket 连通 + <500ms 渲染
覆盖:
  - 健康检查
  - trace CRUD API
  - call graph 端点（cytoscape 格式）
  - flame graph 端点（d3-flame-graph 格式）
  - WebSocket 实时流
  - 404 处理
  - 端到端：注入数据 → API 查询 → 结构正确
  - 渲染延迟 <500ms 门禁
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agent_trace.storage import SQLiteBackend
from agent_trace.storage.base import ObservationRecord, ScoreRecord, TraceRecord
from agent_trace.web import create_app


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def client() -> TestClient:
    storage = SQLiteBackend(":memory:")
    app = create_app(storage=storage)
    return TestClient(app)


def _seed_trace(storage: SQLiteBackend, trace_id: str = "trace-001") -> None:
    storage.create_trace(
        TraceRecord(id=trace_id, name="test-trace", created_at=_now(), updated_at=_now(), session_id="sess-1", user_id="user-1")
    )
    storage.create_observation(
        ObservationRecord(
            id="obs-1",
            trace_id=trace_id,
            name="invoke-agent-a",
            type="SPAN",
            start_time=_now(),
            operation_name="invoke_agent",
            provider_name="openai",
            parent_observation_id=None,
            agent_id="agent_a",
            input_tokens=100,
            output_tokens=200,
            input='{"target_agent": "agent_b"}',
            output='{"result": "ok"}',
        )
    )
    storage.create_observation(
        ObservationRecord(
            id="obs-2",
            trace_id=trace_id,
            name="invoke-agent-b",
            type="SPAN",
            start_time=_now(),
            operation_name="invoke_agent",
            provider_name="openai",
            parent_observation_id="obs-1",
            agent_id="agent_b",
            input_tokens=50,
            output_tokens=80,
            input='{"target_agent": "agent_a"}',
            output='{"result": "loop"}',
        )
    )
    storage.create_score(
        ScoreRecord(id="score-1", trace_id=trace_id, name="quality", value=0.85, created_at=_now(), comment="good")
    )


@pytest.fixture
def populated_client() -> TestClient:
    storage = SQLiteBackend(":memory:")
    _seed_trace(storage)
    app = create_app(storage=storage)
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestTraceAPI:
    def test_list_traces_empty(self, client: TestClient):
        resp = client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["traces"] == []
        assert data["limit"] == 20

    def test_list_traces_with_data(self, populated_client: TestClient):
        resp = populated_client.get("/api/traces")
        assert resp.status_code == 200
        traces = resp.json()["traces"]
        assert len(traces) == 1
        assert traces[0]["id"] == "trace-001"

    def test_get_trace_detail(self, populated_client: TestClient):
        resp = populated_client.get("/api/traces/trace-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace"]["id"] == "trace-001"
        assert len(data["observations"]) == 2
        assert len(data["scores"]) == 1
        assert data["scores"][0]["name"] == "quality"

    def test_get_trace_404(self, client: TestClient):
        resp = client.get("/api/traces/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_list_traces_pagination(self, populated_client: TestClient):
        resp = populated_client.get("/api/traces?limit=5&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert data["offset"] == 0


class TestCallGraph:
    def test_graph_structure(self, populated_client: TestClient):
        resp = populated_client.get("/api/traces/trace-001/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        node_ids = {n["data"]["id"] for n in data["nodes"]}
        assert "agent_a" in node_ids
        assert "agent_b" in node_ids
        assert len(data["edges"]) >= 1

    def test_graph_empty_trace(self, client: TestClient):
        storage = client.app.state.storage
        storage.create_trace(TraceRecord(id="empty-t", name="empty", created_at=_now(), updated_at=_now(), session_id="s"))
        resp = client.get("/api/traces/empty-t/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []


class TestFlameGraph:
    def test_flame_structure(self, populated_client: TestClient):
        resp = populated_client.get("/api/traces/trace-001/flame")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "value" in data
        assert "children" in data
        assert data["name"] == "trace"
        assert data["value"] > 0
        assert len(data["children"]) >= 1

    def test_flame_child_values(self, populated_client: TestClient):
        resp = populated_client.get("/api/traces/trace-001/flame")
        data = resp.json()
        root_child = data["children"][0]
        assert "name" in root_child
        assert "value" in root_child
        # 旧: assert root_child["value"] == 300  # 仅自身 token,子溢出父导致火焰图错位
        # 新: 父节点累积子树 token, obs-1(300) + 子 obs-2(50+80=130) = 430
        assert root_child["value"] == 430
        assert root_child["value"] >= sum(c["value"] for c in root_child["children"])

    def test_flame_empty_trace(self, client: TestClient):
        storage = client.app.state.storage
        storage.create_trace(TraceRecord(id="empty-f", name="empty", created_at=_now(), updated_at=_now(), session_id="s"))
        resp = client.get("/api/traces/empty-f/flame")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "root"
        assert data["value"] == 1


class TestWebSocket:
    def test_ws_connect_and_receive(self, populated_client: TestClient):
        with populated_client.websocket_connect("/ws/stream") as ws:
            populated_client.post(
                "/api/traces/trace-001/event",
                json={"type": "cycle_detected", "cycle": ["a", "b", "a"]},
            )
            msg = ws.receive_text()
            data = json.loads(msg)
            assert data["trace_id"] == "trace-001"
            assert data["type"] == "cycle_detected"
            assert data["cycle"] == ["a", "b", "a"]


class TestIndexPage:
    def test_index_returns_html(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Agent Trace" in resp.text

    def test_index_contains_cytoscape(self, client: TestClient):
        resp = client.get("/")
        assert "cytoscape" in resp.text.lower()

    def test_index_contains_flamegraph(self, client: TestClient):
        resp = client.get("/")
        assert "d3-flamegraph" in resp.text.lower() or "flame" in resp.text.lower()


class TestRenderLatency:
    """门禁: API 组合响应 <500ms"""

    def test_full_load_under_500ms(self, populated_client: TestClient):
        start = time.perf_counter()
        populated_client.get("/api/traces")
        populated_client.get("/api/traces/trace-001")
        populated_client.get("/api/traces/trace-001/graph")
        populated_client.get("/api/traces/trace-001/flame")
        elapsed_ms = (time.perf_counter() - start) * 1000

        print(f"\n[M6 Benchmark] 4 端点组合延迟={elapsed_ms:.1f}ms (门禁 <500ms)")
        assert elapsed_ms < 500, f"渲染延迟 {elapsed_ms:.1f}ms 超过 500ms 门禁"


class TestEndToEnd:
    def test_inject_and_query(self, client: TestClient):
        storage = client.app.state.storage
        storage.create_trace(TraceRecord(id="e2e-1", name="e2e", created_at=_now(), updated_at=_now(), session_id="s"))
        storage.create_observation(
            ObservationRecord(
                id="o1",
                trace_id="e2e-1",
                name="create-alpha",
                type="SPAN",
                start_time=_now(),
                operation_name="create_agent",
                provider_name="openai",
                parent_observation_id=None,
                agent_id="alpha",
                input_tokens=10,
                output_tokens=20,
            )
        )
        resp = client.get("/api/traces/e2e-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace"]["id"] == "e2e-1"
        assert len(data["observations"]) == 1
        assert data["observations"][0]["agent_id"] == "alpha"

    def test_broadcast_to_ws(self, client: TestClient):
        with client.websocket_connect("/ws/stream") as ws:
            client.post(
                "/api/traces/x/event",
                json={"type": "test", "payload": 42},
            )
            msg = ws.receive_text()
            assert json.loads(msg)["payload"] == 42
