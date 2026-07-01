# Agent Trace

<!-- 旧: 仅一句话定位 + 直接跳到 Why Agent Trace,访客无法快速理解项目全貌
**SQLite-backed, zero-infrastructure visual debugger for multi-agent coordination pathologies.**

Detect deadlocks, circular dependencies, and context bloat in your LLM agent workflows, with 100% accuracy gates and a built-in web UI.
-->

**Agent Trace** 是一个给多智能体（Multi-Agent）系统做"病理诊断"的轻量调试器。

当你用 LangGraph、CrewAI 或自研框架把多个 LLM Agent 串在一起协作时，会遇到三类单 Agent 系统不会出现的疑难杂症：

- **死锁（Deadlock）**：Agent A 等 B 释放资源，B 又在等 A —— 整个系统无声挂起，没有任何报错。
- **循环依赖（Circular Dependency）**：A 交给 B，B 交给 C，C 又交回 A —— 无限互相调用，烧光 token 却没有产出。
- **上下文膨胀（Context Bloat）**：对话越拖越长，token 累积超过模型窗口 —— 上下文被截断，推理质量断崖式下降。

现有的观测工具（Langfuse、Phoenix、Helicone）解决的是"**发生了什么**"——它们展示调用链和指标。Agent Trace 解决的是"**哪里出了病**"——它用结构化算法（Tarjan SCC 检环、WFG 检死锁、EMA 预测膨胀）直接告诉你系统病在哪，并且每个算法都经过 100% 准确率门控验证。

所有数据存在本地 SQLite 文件里，不需要装 Postgres、不需要起 ClickHouse、不需要配 Docker —— `pip install` 完直接用。自带 Web UI，火焰图看 token 去向，调用图看 Agent 拓扑和循环。

> **声明**：这是一个个人项目，开发和测试资源有限，功能与结论仅供参考，不构成生产级保证。欢迎提 Issue 和 PR。

[![Tests](https://img.shields.io/badge/tests-148%20passed-brightgreen)]()
[![Accuracy](https://img.shields.io/badge/accuracy-100%25-brightgreen)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()

---

## Why Agent Trace?

Multi-agent systems fail in ways that single-agent systems don't:

- **Deadlock**: Agent A waits for Agent B's resource, while B waits for A's, leading to a silent hang.
- **Circular dependency**: A delegates to B, B delegates to C, C delegates back to A, which produces an infinite loop.
- **Context bloat**: Token accumulation exceeds the model window, so context gets truncated and reasoning degrades.

Existing tools (Langfuse, Phoenix, Helicone) focus on **observability**: they show you what happened. Agent Trace focuses on **pathology detection**: it tells you what went wrong, with structural algorithms that are provably correct.

## Quick Start

```bash
pip install agent-trace[web]
```

```python
from agent_trace.detectors import CycleDetector, DeadlockDetector

# Detect circular agent dependencies
cycle = CycleDetector()
cycle.add_handoff("planner", "researcher")
cycle.add_handoff("researcher", "writer")
cycle.add_handoff("writer", "planner")  # → CycleDetected!

# Detect resource deadlocks
deadlock = DeadlockDetector()
deadlock.acquire("agent_a", "resource_1")
deadlock.acquire("agent_b", "resource_2")
deadlock.request("agent_a", "resource_2")
deadlock.request("agent_b", "resource_1")  # → DeadlockDetected!
```

Launch the web UI:

```bash
agent-trace serve --db ./traces.db --port 7600
```

Open `http://localhost:7600` for the flame graph and call graph visualization.

## Screenshots

### Overview

![UI Overview](images/ui-overview.png)

The main dashboard lists recent runs, with token totals, alert counts, and links into the per-run visualizations.

### Token Flame Graph

![Flame Graph](images/flame-graph.png)

The flame graph is the primary token-attribution view. Reading it:

- **Width** = cumulative token consumption (this step plus all nested children). A wide bar dominates the run.
- **Color** = this step's *own* token consumption on a warm gradient: light yellow → orange → deep red. The deeper the red, the more tokens this single step itself burned.
- **Container steps** (like `planner`) tend to be wide but light-colored. They delegate; they don't compute.
- **Leaf steps that burn tokens** (like `writer`) are both wide and deep red. That's the true hotspot.

Click any bar to zoom into that subtree.

### Agent Call Graph

![Call Graph](images/call-graph.png)

The call graph shows the agent handoff topology. Reading it:

- **Layout** = circle layout, stable across runs (no jitter, no force-directed drift).
- **Nodes** = agents. **Edges** = handoffs (`A → B` means A delegated to B).
- **Red arrows** = a circular dependency detected by the Tarjan SCC algorithm.
- **Warning banner** = appears at the top of the page when one or more cycles are present in the graph.

## Feature Comparison

| Feature | Agent Trace | Langfuse | Phoenix | Helicone |
|---|---|---|---|---|
| Deadlock detection | ✅ WFG + DFS | ❌ | ❌ | ❌ |
| Circular dependency | ✅ Tarjan SCC | ❌ | ❌ | ❌ |
| Context bloat prediction | ✅ EMA + 4-level | ❌ | ❌ | ❌ |
| Anomaly detection | ✅ 5-feature RF-style | basic | ❌ | ❌ |
| OTel GenAI semantic | ✅ v1.41 | partial | partial | ❌ |
| Zero infrastructure | ✅ SQLite default | requires Postgres | requires ClickHouse | requires Postgres |
| Web visualization | ✅ flame + graph | ✅ | ✅ | ✅ |
| Accuracy gate | ✅ 100% per module | N/A | N/A | N/A |

## Algorithms & Accuracy

Every detection module passes a **100% accuracy gate** (precision + recall) before release:

| Module | Algorithm | Benchmark | Result |
|---|---|---|---|
| Cycle detection | Tarjan SCC (O(V+E)) | 50-graph (25 cyclic + 25 DAG) | F1 = 1.0000 |
| Deadlock detection | WFG + incremental DFS | 20-scenario (10 deadlock + 10 safe) | P = R = 1.0 |
| Context bloat | tiktoken + EMA 3-layer | 100-step experiment | MAE = 1.0%, alerts 100% |
| Anomaly detection | 5-feature weighted vote | 100-scenario (50 anomalous + 50 normal) | F1 = 1.0000 |
| Web API | FastAPI + d3-flame-graph + cytoscape | 4-endpoint combo | 24.9ms (< 500ms) |

## Architecture

```
┌─────────────────────────────────────────────┐
│           Your Multi-Agent App              │
│   (LangGraph / CrewAI / custom OTel)        │
└──────────────────┬──────────────────────────┘
                   │ OTel GenAI spans
                   ▼
┌─────────────────────────────────────────────┐
│         AgentSpanEmitter (M1)               │
│   5 span types per OTel GenAI v1.41         │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
┌─────────────────┐  ┌───────────────────────┐
│  SQLiteBackend  │  │  Detectors (M3-M5,M7) │
│  (M2, WAL mode) │  │  Cycle / Deadlock /   │
│  traces         │  │  Bloat / Anomaly      │
│  observations   │  └───────────┬───────────┘
│  scores         │              │ alerts
└────────┬────────┘              ▼
         │              ┌───────────────────┐
         │              │  WebSocket stream │
         ▼              └─────────┬─────────┘
┌─────────────────────────────────┴───────────┐
│          Web UI (M6)                         │
│  d3-flame-graph (token tree)                │
│  cytoscape.js (agent call graph)            │
└─────────────────────────────────────────────┘
```

## Environment Requirements

- **Python**: 3.10 or newer (developed and tested on 3.12). The package declares `requires-python = ">=3.10"` in `pyproject.toml`.
- **Operating system**: Linux, macOS, or Windows. Linux is the primary development and CI target; macOS and Windows are supported but less frequently exercised.
- **External services**: none. SQLite is embedded in the Python standard library, so the storage backend works out of the box with no database server to install or run.
- **Browser** (for the web UI): any modern evergreen browser. Chrome, Firefox, and Edge are all fine. The UI uses d3-flame-graph and cytoscape.js, both of which target current WebKit, Gecko, and Blink.
- **Network**: the web UI binds to `localhost:7600` by default. Agent Trace itself makes no outbound calls.

## Installation

### From PyPI (when published)

```bash
pip install agent-trace[web]
```

### From source

```bash
# 1. Clone the repository
git clone https://github.com/agent-trace/agent-trace.git
cd agent-trace

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows

# 3. Install with all extras (dev + web)
pip install -e ".[dev,web]"

# 4. Verify installation
pytest tests/ -q              # Should show 148 passed
agent-trace --help            # Should show CLI help
```

### Web UI only (lightweight)

If you only need the visualization server and don't need the dev/test tooling:

```bash
pip install "agent-trace[web]"
agent-trace serve --db ./traces.db --port 7600
# Open http://localhost:7600
```

## Usage

### Cycle Detection

```python
from agent_trace.detectors import CycleDetector

detector = CycleDetector(on_cycle=lambda e: print(f"Cycle: {e.cycle}"))
detector.add_handoff("agent_a", "agent_b")
detector.add_handoff("agent_b", "agent_c")
detector.add_handoff("agent_c", "agent_a")
# → CycleDetected(cycle=('agent_a', 'agent_b', 'agent_c', 'agent_a'))
```

### Deadlock Detection

```python
from agent_trace.detectors import DeadlockDetector

detector = DeadlockDetector()
detector.acquire("agent_a", "db_connection")
detector.acquire("agent_b", "file_lock")
detector.request("agent_a", "file_lock")   # a waits for b
detector.request("agent_b", "db_connection")  # b waits for a → DeadlockDetected!
```

### Context Bloat Prediction

```python
from agent_trace.detectors import ContextBloatDetector, BloatLevel

detector = ContextBloatDetector(context_window=128000)
for step in agent_workflow:
    alert = detector.track("agent_1", step.output_text)
    if alert and alert.level == BloatLevel.CRITICAL:
        # Trigger context compression / summarization
        break

# Predict future token usage
predicted = detector.predict("agent_1", steps_ahead=5)
```

### Anomaly Detection

```python
from agent_trace.detectors import AnomalyDetector

detector = AnomalyDetector(context_window=128000)
result = detector.evaluate(
    agent_id="agent_1",
    token_history=[100, 200, 500, 1200],
    span_total=10,
    span_errors=3,
    handoff_depth=6,
    cycle_alerts=1,
    context_tokens=110000,
)
if result.is_anomaly:
    print(f"Anomaly! Score={result.score:.2f}")
    print(f"Triggered: {result.triggered_features}")
```

### OTel Integration

```python
from agent_trace.otel import AgentSpanEmitter

emitter = AgentSpanEmitter(
    tracer=tracer,
    agent_name="my_agent",
    agent_id="agent_001",
    model="gpt-4o",
    provider="openai",
)

with emitter.create_agent_span(metadata={"tools": ["search", "calc"]}):
    with emitter.invoke_agent_client_span(
        target_agent_id="agent_002",
        input={"query": "hello"},
    ):
        result = call_agent_002()
```

### Web UI

```python
from agent_trace.web import create_app
from agent_trace.storage import SQLiteBackend

storage = SQLiteBackend("./traces.db")
app = create_app(storage=storage)

# Run with uvicorn
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=7600)
```

## Demo

```bash
python examples/demo_deadlock.py
```

This runs a 4-agent scenario that triggers all three pathology types:

1. **Circular dependency**: 3 agents form a handoff cycle.
2. **Deadlock**: 2 agents acquire resources then request each other's.
3. **Context bloat**: token accumulation triggers WARNING → ERROR → CRITICAL.
4. **Anomaly detection**: the 5-feature model flags the agent as anomalous.

## Development

```bash
# Run all tests (148 tests, 100% accuracy gate)
pytest tests/ -v

# Run a specific module's gate
pytest tests/test_cycle_detector.py -v -s

# Run the web latency benchmark
pytest tests/test_web.py::TestRenderLatency -v -s
```

## Project Structure

```
agent-trace/
├── src/agent_trace/
│   ├── otel/              # M1: OTel GenAI v1.41 span emitter
│   ├── storage/           # M2: SQLite backend + abstract interface
│   ├── detectors/         # M3-M5, M7: pathology detectors
│   │   ├── cycle_detector.py       # Tarjan SCC
│   │   ├── deadlock_detector.py    # WFG + incremental DFS
│   │   ├── context_bloat.py        # tiktoken + EMA 3-layer
│   │   └── anomaly_detector.py     # 5-feature weighted vote
│   └── web/               # M6: FastAPI + d3-flame-graph + cytoscape
├── tests/                 # 148 tests, 100% accuracy gate
├── examples/              # Demo scripts
├── benchmarks/            # Accuracy benchmark scripts
├── PLAN.md                # Execution plan (8 modules, 3 waves)
├── CONTRIBUTING.md
└── LICENSE                # Apache 2.0
```

## Roadmap

- **v0.2**: PostgreSQL backend (pluggable StorageBackend) + LangGraph decorator
- **v0.3**: OTLP HTTP receiver for remote agent apps
- **v0.4**: Semantic cycle detection (LLM-based intent similarity)
- **v0.5**: EGTP (Enhanced Gradient-based Token Prediction) for bloat forecasting

## License

Apache 2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributions must pass the 100% accuracy gate.

## Star History

If Agent Trace helps you debug your multi-agent systems, please ⭐ star the repo. It helps others discover the project.
