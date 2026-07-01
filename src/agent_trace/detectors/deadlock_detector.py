# src/agent_trace/detectors/deadlock_detector.py
"""死锁检测模块 — Wait-For Graph + 增量 DFS

算法来源:
  -经典 WFG 死锁检测: Coffman 1971, 《System Deadlocks》ACM Computing Surveys
  - 增量 DFS (Tangle 模式): 每次添加等待边后从请求者出发 DFS, O(V+E)
  - 死锁四条件: 互斥 + 持有并等待 + 不可剥夺 + 循环等待

与 M3 环检测的区别:
  - M3: agent 调用图中的循环依赖（handoff 关系）
  - M4: agent 资源等待图中的死锁（resource hold/wait 关系）
  WFG 边语义: A→B 表示 "A 正在等待 B 释放资源"

门禁: precision 100% + recall 100%（20 场景: 10 死锁 + 10 正常）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

DeadlockCallback = Callable[["DeadlockDetected"], None]


@dataclass(frozen=True)
class DeadlockDetected:
    """死锁检测告警事件"""

    cycle: tuple[str, ...]
    resource_chain: tuple[tuple[str, str], ...]
    detection_method: str = "wfg_incremental_dfs"

    @property
    def cycle_length(self) -> int:
        return len(self.cycle)


@dataclass
class DeadlockDetector:
    """Agent 资源等待死锁检测器

    用法:
        detector = DeadlockDetector()
        detector.acquire("agent_a", "res_1")
        detector.acquire("agent_b", "res_2")
        detector.request("agent_a", "res_2")  # a 等 b 释放 res_2
        detector.request("agent_b", "res_1")  # b 等 a 释放 res_1 → 死锁
        assert detector.has_deadlock()

    模型:
        - resource_holder: resource_id → agent_id (谁持有资源)
        - agent_resources: agent_id → set(resource_id) (agent 持有哪些资源)
        - wait_for: agent_id → set(agent_id) (WFG 邻接表)
    """

    _resource_holder: dict[str, str] = field(default_factory=dict)
    _agent_resources: dict[str, set[str]] = field(default_factory=dict)
    _wait_for: dict[str, set[str]] = field(default_factory=dict)
    _on_deadlock: DeadlockCallback | None = None
    _deadlocks: list[DeadlockDetected] = field(default_factory=list)

    def __init__(self, on_deadlock: DeadlockCallback | None = None) -> None:
        self._resource_holder = {}
        self._agent_resources = {}
        self._wait_for = {}
        self._on_deadlock = on_deadlock
        self._deadlocks = []

    def acquire(self, agent_id: str, resource_id: str) -> bool:
        """agent 尝试获取空闲资源

        返回: True 若获取成功（资源空闲），False 若资源已被占用
        """
        if resource_id in self._resource_holder:
            return False
        self._resource_holder[resource_id] = agent_id
        self._agent_resources.setdefault(agent_id, set()).add(resource_id)
        self._clear_wait_edges_for_resource(agent_id, resource_id)
        return True

    def request(self, agent_id: str, resource_id: str) -> DeadlockDetected | None:
        """agent 请求资源

        若资源空闲: 直接获取
        若资源被占用: 添加等待边 agent→holder, 触发增量 DFS 检测死锁

        返回: 若导致死锁返回 DeadlockDetected，否则 None
        """
        holder = self._resource_holder.get(resource_id)
        if holder is None:
            self.acquire(agent_id, resource_id)
            return None
        if holder == agent_id:
            return None
        self._wait_for.setdefault(agent_id, set()).add(holder)
        return self._detect_and_notify(agent_id, resource_id, holder)

    def release(self, agent_id: str, resource_id: str) -> bool:
        """agent 释放资源

        返回: True 若释放成功，False 若 agent 未持有该资源
        释放后清除因等待此资源而产生的所有 WFG 边
        """
        if self._resource_holder.get(resource_id) != agent_id:
            return False
        del self._resource_holder[resource_id]
        self._agent_resources.get(agent_id, set()).discard(resource_id)
        self._clear_wait_edges_for_resource(agent_id, resource_id)
        return True

    def has_deadlock(self) -> bool:
        """当前 WFG 是否存在死锁环"""
        return len(self.find_deadlocks()) > 0

    def find_deadlocks(self) -> list[DeadlockDetected]:
        """找出当前 WFG 中所有死锁环"""
        deadlocks: list[DeadlockDetected] = []
        visited_global: set[str] = set()

        for start in self._wait_for:
            if start in visited_global:
                continue
            cycle = _dfs_find_cycle(self._wait_for, start)
            if cycle is not None:
                chain = _build_resource_chain(self._wait_for, self._agent_resources, cycle)
                deadlocks.append(
                    DeadlockDetected(
                        cycle=tuple(cycle),
                        resource_chain=tuple(chain),
                    )
                )
                visited_global.update(cycle)
        return deadlocks

    def get_all_deadlocks(self) -> list[DeadlockDetected]:
        """返回自启动以来所有检测到的死锁（含历史）"""
        return list(self._deadlocks)

    def clear(self) -> None:
        self._resource_holder.clear()
        self._agent_resources.clear()
        self._wait_for.clear()
        self._deadlocks.clear()

    @property
    def agent_count(self) -> int:
        all_agents = set(self._wait_for.keys())
        all_agents.update(self._agent_resources.keys())
        return len(all_agents)

    @property
    def resource_count(self) -> int:
        return len(self._resource_holder)

    @property
    def wait_edge_count(self) -> int:
        return sum(len(neighbors) for neighbors in self._wait_for.values())

    def _clear_wait_edges_for_resource(self, agent_id: str, resource_id: str) -> None:
        waiters_to_clear: set[str] = set()
        for waiter, holders in self._wait_for.items():
            holders_to_remove = {
                h for h in holders
                if resource_id in self._agent_resources.get(h, set()) or h == agent_id
            }
            if holders_to_remove and waiter == agent_id:
                continue
            holders -= holders_to_remove
            if not holders and waiter != agent_id:
                waiters_to_clear.add(waiter)
        for w in waiters_to_clear:
            if w in self._wait_for and not self._wait_for[w]:
                del self._wait_for[w]

    def _detect_and_notify(
        self, requester: str, resource_id: str, holder: str
    ) -> DeadlockDetected | None:
        cycle = _dfs_find_cycle(self._wait_for, requester)
        if cycle is None:
            return None
        chain = _build_resource_chain(self._wait_for, self._agent_resources, cycle)
        event = DeadlockDetected(
            cycle=tuple(cycle),
            resource_chain=tuple(chain),
        )
        self._deadlocks.append(event)
        if self._on_deadlock is not None:
            self._on_deadlock(event)
        return event


def _dfs_find_cycle(
    wait_for: dict[str, set[str]], start: str
) -> list[str] | None:
    """从 start 出发 DFS 寻找回到 start 的环

    返回: 环路径（含起点首尾），或 None
    """
    path: list[str] = [start]
    visited: set[str] = {start}

    def dfs(node: str) -> list[str] | None:
        for neighbor in wait_for.get(node, ()):
            if neighbor == start and len(path) >= 1:
                return path + [start]
            if neighbor not in visited:
                visited.add(neighbor)
                path.append(neighbor)
                result = dfs(neighbor)
                if result is not None:
                    return result
                path.pop()
                visited.discard(neighbor)
        return None

    return dfs(start)


def _build_resource_chain(
    wait_for: dict[str, set[str]],
    agent_resources: dict[str, set[str]],
    cycle: list[str],
) -> list[tuple[str, str]]:
    """构建资源链: [(waiter, resource_being_waited_for), ...]

    cycle: [a, b, c, a] → a 等 b 持有的某资源, b 等 c 持有的某资源, ...
    """
    chain: list[tuple[str, str]] = []
    for i in range(len(cycle) - 1):
        waiter = cycle[i]
        holder = cycle[i + 1]
        waited_resource = _find_waited_resource(wait_for, agent_resources, waiter, holder)
        chain.append((waiter, waited_resource))
    return chain


def _find_waited_resource(
    wait_for: dict[str, set[str]],
    agent_resources: dict[str, set[str]],
    waiter: str,
    holder: str,
) -> str:
    """找出 waiter 等待 holder 释放的具体资源"""
    holder_resources = agent_resources.get(holder, set())
    if not holder_resources:
        return "<unknown>"
    return next(iter(holder_resources))
