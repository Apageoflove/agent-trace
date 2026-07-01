# Agent Trace — 项目规划书 (PLAN.md)

> **创建日期**: 2026-06-30
> **文档性质**: 项目执行唯一依据，后续所有实现必须遵循此文档，不得偏离，如有技术调整也可更新此md后再按此执行
> **变更原则**: 任何范围/算法/模块调整必须先更新此文档并经确认，禁止口头变更
> **调研基础**: 3 份并行调研报告（竞品+Harness 工程 / 检测算法论文 / Metis 预规划分析），共 60+ 论文与工程参考

---

## 1. 项目定位（差异化，禁止照抄竞品）

### 1.1 一句话定位

> **Agent Trace — Zero-infrastructure visual debugger for multi-agent coordination pathologies. Detects deadlocks, circular dependencies, and context bloat — the three failure modes every multi-agent system hits, that every existing observability tool misses.**
>
> 存储策略：SQLite 默认零配置（开箱即用）+ 可插拔存储后端（`StorageBackend` 抽象接口，PG 等留接口位 v0.2 实现）。区别于 Langfuse 强制 PG+ClickHouse+Redis+S3 重型栈，我们让重型栈成为可选。

### 1.2 三大差异化能力（每项必须有竞品对比依据）

| 能力 | 现有竞品短板（调研证据） | 我们的差异化点 |
|---|---|---|
| **多 Agent 死锁检测** | Langfuse/Phoenix/Helicone 只看 trace 不分析协作病态；Tangle/looptrip 只检测不可视化不持久化 | Wait-For Graph + 增量 DFS + 可视化 + SQLite 持久化闭环 |
| **循环依赖识别** | Agentproof 仅静态分析；IBM ICPE 2026 F1=0.72 但无开源实现；looptrip 无 runtime SpanProcessor | Tarjan SCC（结构 100%）+ 语义相似度混合（目标 F1≥0.72）+ 实时增量 |
| **上下文膨胀预警** | 所有现有工具（contextguard/ctxguard/claude-context-monitor）都聚焦单 Agent IDE 场景，没有一个针对多 Agent 协作；Langfuse 只数 token 不分析增长模式 | 三层管道：tiktoken 精确计数 + EMA 滑动窗口预测 + 阈值告警（50/75/90/95% 四级） |

### 1.3 直接架构竞争者分析

| 项目 | Stars | 与我们的关系 | 它的弱点（我们的机会） |
|---|---|---|---|
| llm-trace (MIT) | 2 | 架构几乎重合（SQLite+Python+7600 端口） | 无死锁/循环/上下文检测；README 是功能列表无杀手 demo；营销失败 |
| Tangle (intuitai) | - | 方法学前辈（WFG+增量 DFS） | 无可视化 dashboard；无 SQLite 单文件；无上下文膨胀检测 |
| looptrip (PyPI) | - | 最直接的方法学竞争者（2-iteration 早期检测） | 无 runtime SpanProcessor；无可视化；无存储 |
| Langfuse | 30,150 | 最强开源竞品 | 依赖 PG+ClickHouse+Redis+S3 重型栈；无死锁/循环/上下文检测 |

### 1.4 市场窗口叙事钩子（用于 GitHub README/HN/推文）

- **OpenAI 2026-02-11** 发《Harness Engineering》专文，5 个月实验 0 行手写代码，承认"最难的挑战是设计环境、反馈循环、控制系统"
- **Anthropic 2026-03** 三 Agent Harness（Planner-Generator-Evaluator），自述"context anxiety"+"synchronous execution creates bottlenecks"+"the entire system can be blocked while waiting for a single subagent"
- **Martin Fowler 2026-04-02** 定为年度核心工程实践，明确说"harness-coverage 类工具是空白市场"

---

## 2. 算法选型（带论文出处和准确率，禁止臆想）

### 2.1 核心检测算法

| 模块 | 选定算法 | 论文/工程出处 | 报告准确率 | 实施复杂度 |
|---|---|---|---|---|
| **环检测（结构）** | Tarjan SCC (O(V+E)) | Tarjan 1972；TheAlgorithms/Python `tarjans_scc.py` | 100%（结构环） | 低 |
| **环检测（语义，v2）** | Embedding 相似度 + 滑动窗口 | IBM ICPE 2026 (arxiv 2511.10650) | F1=0.72（混合后） | 中 |
| **死锁检测** | Wait-For Graph + 增量 DFS | Chandy-Misra-Haas 1983；Tangle 2026 实战 | 100%（单进程视图） | 低 |
| **Context Bloat L1** | tiktoken 精确计数 | Anthropic context engineering 2025 | 100%（计数） | 低 |
| **Context Bloat L2** | EMA 滑动窗口预测 | PreflightLLMCost 2026 | MAE 8-15% | 低 |
| **Context Bloat L3（v2）** | EGTP 熵引导 token 池化 | ICLR 2026 (arxiv 2602.11812) | MAE -29% vs baseline | 中 |
| **异常检测 v1** | Random Forest 5 特征 | xhybrid-2026 benchmark | F1=99.95% | 低 |

### 2.2 OTel GenAI 标准采用

- **版本**: v1.41.1 (2026-05)，状态 `Development`（未稳定，但已是事实标准）
- **5 类 agent span 必须支持**:
  1. `create_agent` (CLIENT)
  2. `invoke_agent_client` (CLIENT)
  3. `invoke_agent_internal` (INTERNAL)
  4. `invoke_workflow`
  5. `execute_tool` (INTERNAL)
- **必备属性**: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.conversation.id`, `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.tool.name`, `error.type`
- **双发射策略**: 生产走 stable 核心属性（v1.36 baseline）+ `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` 开实验属性
- **隐私**: 默认不 capture `gen_ai.input.messages`/`output.messages`，提供 opt-in 开关

### 2.3 可视化技术栈

| 视图 | 库 | 用途 |
|---|---|---|
| Trace 详情（火焰图/冰柱图） | d3-flame-graph (MIT) | 单 trace span 层级 + 耗时 |
| Agent Call Graph (DAG) | cytoscape.js + dagre 布局 (MIT) | 多 Agent 调用关系 + 死锁着色 |
| 实时传输 | WebSocket (FastAPI) | span 流式增量更新 |

**性能预算**: 单视图 <1000 spans 用 d3；DAG <200 节点用 cytoscape dagre；200-2000 节点用 vis-network canvas；>2000 节点聚合到 cluster。

---

## 3. 8 模块 Wave 划分（每模块 100% 准确率门禁，未达标禁止进入下一 Wave）

### 3.1 Wave 1（基础设施，必须先完成）

#### M1: 项目骨架 + OTel GenAI 集成
- **交付物**: `pyproject.toml` + `src/agent_trace/` 包结构 + 5 类 agent span 发射器
- **准确率指标**: span 字段覆盖率 100%
- **验证方法**: 单元测试——每类 span 正确生成且字段齐全
- **门禁**: 测试通过率 100% 才进入 M2

#### M2: SQLite 存储层 + 查询 API
- **交付物**: schema（traces/observations/scores 三表，参考 Langfuse 数据模型简化）+ CRUD + 查询 API
- **准确率指标**: CRUD 100% 正确 + 单查询 <10ms
- **验证方法**: 10k trace 压测 + 边界用例（空/超大/Unicode）
- **门禁**: 压测通过 + 准确率 100% 才进入 Wave 2

### 3.2 Wave 2（核心检测，依赖 Wave 1）

#### M3: 环检测模块（Tarjan SCC）
- **交付物**: `detectors/cycle_detector.py` + 增量图维护 + 告警事件
- **算法**: Tarjan SCC (O(V+E)) + 增量维护
- **准确率指标**: 结构环 F1 100%（precision 100% + recall 100%）
- **验证方法**: 50 图 benchmark（含/不含环各 25）+ 真实 LangGraph 工作流
- **门禁**: F1 100% 才进入 M4

#### M4: 死锁检测模块（WFG + 增量 DFS）
- **交付物**: `detectors/deadlock_detector.py` + Wait-For Graph 维护 + LangGraph 装饰器
- **算法**: WFG + 增量 DFS（Tangle 模式）
- **准确率指标**: 死锁 precision 100% + recall 100%
- **验证方法**: 4-agent 循环依赖 demo（可复现）+ 20 场景（10 死锁 + 10 正常）
- **门禁**: precision/recall 均 100% 才进入 Wave 3

### 3.3 Wave 3（预警 + 可视化 + 发布）

#### M5: Context Bloat 预警
- **交付物**: `detectors/context_bloat.py` + 三层管道（L1 tiktoken + L2 EMA + L3 可选 EGTP）
- **准确率指标**: 预测 MAE ≤15% + 四级阈值告警触发率 100%
- **验证方法**: 100 步长运行 agent 实验，验证 50/75/90/95% 阈值依次触发
- **门禁**: MAE ≤15% + 告警 100% 才算通过

#### M6: Web 可视化
- **交付物**: FastAPI 后端 + d3-flame-graph + cytoscape.js 前端 + WebSocket 流式
- **准确率指标**: 渲染延迟 <500ms + 实时更新可用
- **验证方法**: Playwright 截图验证 + 真实 trace 渲染
- **门禁**: 延迟 <500ms + 截图无视觉缺陷才算通过

#### M7: 异常检测 v1
- **交付物**: `detectors/anomaly.py` + RF 5 特征模型
- **准确率指标**: F1 100%
- **验证方法**: 1k 标注 trace 交叉验证（5-fold）
- **门禁**: F1 100% 才算通过

#### M8: Demo + README + Benchmark + 发布
- **交付物**:
  - 4-agent 死锁 demo（30 秒 GIF）
  - README（第一屏杀手模板 + 4 列对比表 + Quick Start）
  - 公开 benchmark（vs Langfuse/Phoenix 在 4-agent 场景）
  - Apache 2.0 LICENSE + CONTRIBUTING.md + Discord 链接
  - CI badges (lint/type/test)
- **门禁**: 真实 surface 跑通 1 个 multi-agent 示例 + 展示三种告警

---

## 4. 准确率门禁规则（铁律，禁止跳过）

### 4.1 通用规则
- 每模块完成后必须跑准确率验证脚本
- 准确率 = (正确判定数 / 总测试用例数) × 100%
- **100%**: PASS，可进入下一模块
- **<100%**: BLOCKED，禁止进入下一模块，必须迭代修复
- **最多 3 轮迭代**，3 轮后仍 <100% 则升级 Oracle + 写入 manual-steps.txt

### 4.2 每模块验证报告格式（强制）
```
模块: <name>
API surface 验证: <列表>
真实输入测试: <数量 + 描述>
边界用例覆盖: <列表>
跨模块兼容性: <PASS/FAIL + 细节>
发现问题: <列表或 none>
应用优化: <列表或 none>
准确率: <X>%
判定: PASS / NEEDS_OPTIMIZATION / BLOCKED
```

### 4.3 Wave 依赖规则
- Wave N 所有模块必须验证通过才进入 Wave N+1
- 模块优化后，所有依赖它的模块必须重新验证兼容性
- "地基不牢地动山摇"——上游 bug 会污染下游所有模块

---

## 5. Star 策略（基于竞品数据归纳，非臆想）

### 5.1 README 第一屏杀手模板
```markdown
# 🔍 Agent Trace

**Stop debugging multi-agent systems with grep.**

Three failure modes kill every multi-agent system:
- 🔒 **Deadlock** — A waits for B, B waits for A. Silent hang.
- 🔄 **Circular dependency** — Agent loop with no progress.
- 📈 **Context bloat** — 200K tokens, $50/run, no warning.

**Agent Trace detects all three, in 30 seconds, zero infrastructure.**

\`\`\`bash
pip install agent-trace
agent-trace watch my_agent.py
# Opens http://localhost:7600
\`\`\`

**vs Langfuse**: No Docker. No ClickHouse. No Postgres. No Redis. No S3.
**vs Phoenix**: Detects deadlocks Phoenix misses.
**vs Helicone**: Tracks multi-agent coordination, not just LLM calls.

[Demo GIF: detecting a 4-agent circular wait in 0.3s]
```

### 5.2 4 列对比表（基于调研真实数据）
| | Langfuse | Phoenix | Helicone | **Agent Trace** |
|---|---|---|---|---|
| Stars | 30,150 | 10,204 | 5,839 | 新 |
| License | MIT | ELv2 | Apache 2.0 | Apache 2.0 |
| 基础设施 | PG+CH+Redis+S3 | 本机 only | 自托管+云 | **SQLite 默认零配置 / PG 可插拔** |
| 死锁检测 | ❌ | ❌ | ❌ | ✅ |
| 循环依赖 | ❌ | ❌ | ❌ | ✅ |
| 上下文膨胀预警 | ❌ | ❌ | ❌ | ✅ |
| 多 Agent 协作分析 | ❌ | ❌ | ❌ | ✅ |
| OTel GenAI 兼容 | ✅ | ✅ (OpenInference) | ❌ | ✅ |

### 5.3 公开 Benchmark
- 数据集: IBM LoopBench (arxiv 2512.13713) + 自造 4-agent 死锁场景
- 对比对象: Langfuse（仅 trace 展示）、Tangle（仅检测）
- 指标: 死锁检测 precision/recall、检测延迟、上下文膨胀预测 MAE

### 5.4 发布就绪检查清单
- [ ] 4-agent 循环依赖在 SQLite 存储下亚秒级检测
- [ ] 100 步长运行 agent 上下文膨胀在 70% 阈值告警
- [ ] 兼容 OTel GenAI v1.41 规范的 span 输入
- [ ] README 包含 4 列对比表 + 30 秒 demo GIF
- [ ] Apache 2.0 许可证
- [ ] CI 通过 (lint + type + test)
- [ ] Discord 社区链接
- [ ] 至少 1 个真实 LangGraph sample 跑通

---

## 6. 执行规则（强制遵循）

### 6.1 项目隔离（RULE 1-4）
- 所有依赖必须在 `/media/work/KIOXIA_XD20/Agent/Agent_Trace/` 目录内
- Python venv 已建在 `.venv/`，所有 pip 安装用 venv 内的 pip（`.venv/bin/pip`）
- 禁止系统级安装、禁止全局 npm、禁止引用项目外文件

### 6.1.1 依赖镜像策略（用户指定，已配置并验证）
- **镜像**: 上海交大 PyPI 镜像 `https://mirror.sjtu.edu.cn/pypi/web/simple`
- **配置位置**: `.venv/pip.conf`（venv 级配置，项目隔离，激活 venv 后自动生效）
- **配置内容**:
  ```ini
  [global]
  index-url = https://mirror.sjtu.edu.cn/pypi/web/simple
  trusted-host = mirror.sjtu.edu.cn
  timeout = 120
  retries = 3
  ```
- **验证状态**: ✅ 已验证（2026-06-30）— `pip install six` 成功从 SJTU 下载，`pip config list` 确认配置生效
- **切换规则**: 仅当 SJTU 镜像不可用时，按 RULE 7d 顺序切换至清华源 `https://pypi.tuna.tsinghua.edu.cn/simple`，禁止跳过 SJTU 直接用其他镜像

### 6.2 代码修改规则（RULE 19）
- 修改已有代码必须"注释旧 + 下方加新"，禁止直接覆盖删除
- 纯新增代码无此约束
- 用户明确说"直接替换"时无此约束

### 6.3 变更透明（RULE 13）
- 每次文件修改前必须报告: 文件路径 + 修改范围 + 原因 + 预期副作用
- 修改后必须立即验证: LSP 诊断 / 语法检查 / 读回确认

### 6.4 失败处理（RULE 14）
- 任何工具调用失败立即停止 + 报告
- 同一操作失败 3 次必须停止询问用户
- 禁止"假设成功"继续执行

### 6.5 语言（RULE 11）
- 所有面向用户响应必须用中文
- 代码注释用中文，技术术语保留英文
- 文档用中文

### 6.6 任务延续（RULE 12）
- 主任务完成后按优先级执行剩余任务
- 任务形成链条，任务 N 的输出是任务 N+1 的输入
- 用户中断时暂停队列，用户完成后恢复

---

## 7. 关键决策记录（自主选定默认值）

| 决策点 | 选定值 | 理由 |
|---|---|---|
| 框架支持优先级 | 原生 LangGraph 装饰器 + OTLP HTTP receiver | LangGraph 是当前最流行的多 Agent 框架；OTLP 保证语言中立 |
| 接入方式 | Python SDK `@traceable` 装饰器 + OTLP 双通道 | 双通道兼顾易用性和通用性 |
| Demo 场景 | 自造可控 4-agent 死锁 demo（可复现）+ 1 个真实 LangGraph sample | 可复现是 GitHub star 关键 |
| 许可证 | Apache 2.0 | 调研显示 Apache 2.0 是 5-7k 档项目的共同点 |
| 端口 | 7600 | 与 llm-trace 一致，便于迁移 |
| 数据模型 | Langfuse 简化版（Trace/Observation/Score 三表） | 降低用户迁移成本 |
| 存储后端 | SQLite 默认零配置 + `StorageBackend` 抽象接口（PG 等留 v0.2 接口位） | 应对"单 SQLite 会 low"质疑，让重型栈成为可选而非强制 |
| Python 版本 | 3.12（已装 3.12.3） | OTel GenAI instrumentation 全支持 |

---

## 8. 目录结构（规划）

```
/media/work/KIOXIA_XD20/Agent/Agent_Trace/
├── PLAN.md                    # 本文档（唯一执行依据）
├── pyproject.toml             # 项目配置 + 依赖
├── README.md                  # GitHub 发布用（M8 产出）
├── LICENSE                    # Apache 2.0
├── CONTRIBUTING.md            # 贡献指南（M8 产出）
├── .venv/                     # 本地虚拟环境（已建）
├── src/agent_trace/
│   ├── __init__.py
│   ├── otel/                  # OTel GenAI span 发射器（M1）
│   ├── storage/               # SQLite 存储层（M2）
│   ├── detectors/             # 检测模块（M3-M5, M7）
│   │   ├── cycle_detector.py      # M3 环检测
│   │   ├── deadlock_detector.py   # M4 死锁检测
│   │   ├── context_bloat.py       # M5 上下文膨胀预警
│   │   └── anomaly.py             # M7 异常检测
│   ├── api/                   # FastAPI 后端（M6）
│   └── web/                   # 前端静态资源（M6）
│       ├── flame_graph/          # d3-flame-graph
│       └── call_graph/           # cytoscape.js
├── tests/                     # 测试（每模块配套）
│   ├── test_otel.py
│   ├── test_storage.py
│   ├── test_cycle_detector.py
│   ├── test_deadlock_detector.py
│   ├── test_context_bloat.py
│   ├── test_anomaly.py
│   └── benchmarks/             # 准确率验证脚本
├── examples/                  # Demo 脚本
│   ├── deadlock_4agent.py     # 4-agent 死锁 demo
│   └── langgraph_sample.py    # 真实 LangGraph sample
├── benchmarks/                # 公开 benchmark 数据
└── docs/                      # 文档
```

---

## 9. 当前状态

- [x] 调研完成（3 份并行报告）
- [x] 规划文档（本文件）
- [x] 项目骨架目录已建
- [x] Python venv 已建（3.12.3 + pip 26.1.2）
- [ ] M1: 项目骨架 + OTel 集成（进行中）
- [ ] M2-M8: 待执行

---

## 10. 参考资料索引

### 竞品与 Harness 工程
- OpenAI Harness Engineering: https://openai.com/index/harness-engineering/ (2026-02-11)
- Anthropic 多 Agent: https://www.anthropic.com/engineering/multi-agent-research-system
- Martin Fowler: https://martinfowler.com/articles/harness-engineering.html (2026-04-02)
- Langfuse: https://github.com/langfuse/langfuse (30,150⭐)
- Phoenix: https://github.com/arize-ai/phoenix (10,204⭐)
- Helicone: https://github.com/Helicone/helicone (5,839⭐)
- llm-trace: https://github.com/llm-trace/llm-trace (2⭐，直接架构竞争者)
- Tangle: https://github.com/intuitai/tangle
- looptrip: https://pypi.org/project/looptrip/

### 算法论文
- Tarjan SCC 1972: https://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm
- Chandy-Misra-Haas 1983: https://apps.dtic.mil/sti/html/tr/ADA120371/
- IBM ICPE 2026 环检测: https://arxiv.org/abs/2511.10650 (F1=0.72)
- Anthropic context engineering 2025: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- EGTP ICLR 2026: https://arxiv.org/pdf/2602.11812
- xhybrid-2026 异常检测: https://github.com/tanviranindo/xhybrid-2026 (RF F1=99.95%)
- LoopBench: https://arxiv.org/pdf/2512.13713

### OTel GenAI
- Spec v1.41.1: https://github.com/open-telemetry/semantic-conventions
- Agent spans: https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md

### 可视化
- d3-flame-graph: https://github.com/spiermar/d3-flame-graph
- cytoscape.js: http://js.cytoscape.org/
- Brendan Gregg 火焰图: https://www.brendangregg.com/flamegraphs.html

---

**本文件为项目执行唯一依据。任何实现偏离此文档即为缺陷，必须回退对齐。**
