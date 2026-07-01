# src/agent_trace/detectors/cycle_detector.py
"""环检测模块 — Tarjan SCC + 增量维护

算法来源:
  - Tarjan 1972 SCC: https://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm
  - TheAlgorithms/Python: https://github.com/TheAlgorithms/python/blob/master/graphs/tarjans_scc.py
  - IBM ICPE 2026 (arxiv 2511.10650): LLM agent 环检测 F1=0.72（混合语义）

设计:
  - 结构层（本模块）: 维护 agent 调用图，每条新边触发 Tarjan SCC 重算
  - 增量触发: add_edge() 后自动检测，SCC size > 1 即报环
  - 告警: CycleDetected 事件，含参与环的 agent 列表 + 路径
  - 复杂度: 每次 add_edge 后 O(V+E)，对 agent 图（通常 <100 节点）亚秒级

门禁: 结构环 F1 ≥99%（precision 100% + recall 100%）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from agent_trace.otel.attributes import GenAIAttr, GenAIHandoffType


@dataclass(frozen=True)
class CycleDetected:
    """环检测告警事件"""

    cycle: tuple[str, ...]
    detection_method: str = "tarjan_scc"

    @property
    def cycle_length(self) -> int:
        return len(self.cycle)


CycleCallback = Callable[[CycleDetected], None]


@dataclass
class CycleDetector:
    """Agent 调用图环检测器

    用法:
        detector = CycleDetector()
        detector.add_edge("agent_a", "agent_b")
        detector.add_edge("agent_b", "agent_c")
        detector.add_edge("agent_c", "agent_a")  # 触发 CycleDetected
        assert detector.has_cycle()

    增量策略:
        每次 add_edge 后，对新图跑 Tarjan SCC。
        若任一 SCC size > 1，则存在环。
        对 agent 图（典型 <100 节点），单次重算 <1ms。
    """

    _adj: dict[str, set[str]] = field(default_factory=dict)
    _on_cycle: CycleCallback | None = None
    _cycles: list[CycleDetected] = field(default_factory=list)

    def __init__(self, on_cycle: CycleCallback | None = None) -> None:
        self._adj = {}
        self._on_cycle = on_cycle
        self._cycles = []

    def add_edge(self, source: str, target: str) -> CycleDetected | None:
        """添加一条有向边 source → target

        返回: 若新增边导致出现环，返回 CycleDetected；否则返回 None
        """
        if source not in self._adj:
            self._adj[source] = set()
        if target not in self._adj:
            self._adj[target] = set()
        self._adj[source].add(target)
        return self._detect_and_notify()

    def add_handoff(
        self,
        source_agent: str,
        target_agent: str,
        handoff_type: str = GenAIHandoffType.DELEGATION,
    ) -> CycleDetected | None:
        """便捷方法：从 OTel handoff 属性添加边

        等价于 add_edge(source_agent, target_agent)，
        但语义上对应 gen_ai.handoff.* 事件。
        handoff_type 仅记录，不参与环检测逻辑。
        """
        return self.add_edge(source_agent, target_agent)

    def has_cycle(self) -> bool:
        """当前图是否存在至少一个环"""
        return len(self.find_cycles()) > 0

    def find_cycles(self) -> list[CycleDetected]:
        """找出所有 SCC（size > 1 即环）

        Tarjan SCC 保证: 一个 SCC size > 1 ⟺ 其中存在环。
        自环（source → source）也算环，size=1 但有自边。
        """
        sccs = _tarjan_scc(self._adj)
        cycles: list[CycleDetected] = []
        for scc in sccs:
            if len(scc) > 1:
                cycle_path = _extract_cycle_path(self._adj, scc)
                cycles.append(
                    CycleDetected(cycle=tuple(cycle_path), detection_method="tarjan_scc")
                )
            elif len(scc) == 1:
                node = next(iter(scc))
                if node in self._adj.get(node, set()):
                    cycles.append(
                        CycleDetected(
                            cycle=(node, node),
                            detection_method="tarjan_scc_self_loop",
                        )
                    )
        return cycles

    def get_all_cycles(self) -> list[CycleDetected]:
        """返回自启动以来所有检测到的环（含历史）"""
        return list(self._cycles)

    def clear(self) -> None:
        """清空图和告警历史"""
        self._adj.clear()
        self._cycles.clear()

    @property
    def node_count(self) -> int:
        return len(self._adj)

    @property
    def edge_count(self) -> int:
        return sum(len(neighbors) for neighbors in self._adj.values())

    def _detect_and_notify(self) -> CycleDetected | None:
        current_cycles = self.find_cycles()
        if not current_cycles:
            return None
        new_cycle = current_cycles[-1]
        self._cycles.extend(current_cycles)
        if self._on_cycle is not None:
            self._on_cycle(new_cycle)
        return new_cycle


def _tarjan_scc(adj: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 强连通分量算法

    复杂度: O(V + E)
    返回: SCC 列表，每个 SCC 是节点列表

    参考: https://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm
    """
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        index[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True

        for successor in adj.get(node, ()):
            if successor not in index:
                strongconnect(successor)
                lowlink[node] = min(lowlink[node], lowlink[successor])
            elif on_stack.get(successor, False):
                lowlink[node] = min(lowlink[node], index[successor])

        if lowlink[node] == index[node]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == node:
                    break
            result.append(scc)

    for node in adj:
        if node not in index:
            strongconnect(node)

    return result


def _extract_cycle_path(
    adj: dict[str, set[str]], scc: list[str]
) -> list[str]:
    """从 SCC 中提取一条具体的环路径

    Tarjan 只告诉我们哪些节点构成 SCC（存在环），
    但不直接给出环路径。这里用 DFS 在 SCC 子图内找一条回路。
    """
    scc_set = set(scc)
    if len(scc) == 1:
        return [scc[0], scc[0]] if scc[0] in adj.get(scc[0], set()) else [scc[0]]

    start = scc[0]
    path: list[str] = [start]
    visited: set[str] = {start}
    current = start

    while True:
        next_in_scc = None
        for neighbor in adj.get(current, ()):
            if neighbor in scc_set:
                if neighbor == start and len(path) > 1:
                    return path + [start]
                if neighbor not in visited:
                    next_in_scc = neighbor
                    break
        if next_in_scc is None:
            break
        path.append(next_in_scc)
        visited.add(next_in_scc)
        current = next_in_scc

    if path[0] in adj.get(path[-1], set()):
        return path + [path[0]]
    return path
