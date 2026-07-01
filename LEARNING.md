# Agent Trace 学习指南

> 本文档梳理 Agent Trace 项目涉及的全部知识点：概念、算法、数据结构、工程实践。
> 适合有 Python 基础、但不熟悉图论或分布式系统的开发者。

---

## 目录

1. [多智能体系统的三类结构性故障](#1-多智能体系统的三类结构性故障)
2. [图论基础](#2-图论基础)
3. [循环依赖检测：Tarjan SCC 算法](#3-循环依赖检测tarjan-scc-算法)
4. [死锁检测：WFG + 增量 DFS](#4-死锁检测wfg--增量-dfs)
5. [上下文膨胀预测：三层 EMA](#5-上下文膨胀预测三层-ema)
6. [异常检测：5 特征加权投票](#6-异常检测5-特征加权投票)
7. [OpenTelemetry GenAI 语义规范](#7-opentelemetry-genai-语义规范)
8. [SQLite WAL 模式](#8-sqlite-wal-模式)
9. [Web UI 技术栈](#9-web-ui-技术栈)
10. [软件工程实践](#10-软件工程实践)

---

## 1. 多智能体系统的三类结构性故障

单 Agent 系统的故障是"模型答错了"或"工具调失败了"，看日志就能定位。多 Agent 系统把这些简单的"单步错误"放大成三种子系统级别的"结构性"问题，单看任何一行日志都看不出毛病，必须从整体结构上才能判断。

### 1.1 循环依赖（Circular Dependency）

**定义**：Agent A 把任务委派给 B，B 又委派给 C，C 又把任务踢回 A，形成一个环。系统不是报错也不是退出，而是在环里无限循环。

**具体例子**：

```
Planner    -> Researcher    -> Writer
  ^                              |
  +------------------------------+
```

Planner 把研究任务交给 Researcher，Researcher 写完初稿交给 Writer，Writer 又把润色任务踢回 Planner。三个 Agent 看似都在工作，但每一轮都只是把球传出去，永远不会有人真正输出"成品"。

**为什么有害**：每次循环都要调一次大模型，token 烧得很快但系统不报错也不超时，运维同事看监控以为一切正常，等发现时账单可能已经爆了。

> **重点讲解：循环依赖**
>
> 三个部门互相踢皮球，A 说"这事归 B 管"，B 说"归 C 管"，C 说"归 A 管"，公文在三个部门之间转了 100 圈没有任何一个部门真办事。从外面看，三部门都有"工作记录"，监控看着正常，但产出为零。
>
> **记忆口诀**：踢皮球转圈圈，token 烧完没产出
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | 循环依赖会报错 | 静默死循环，不报错 |
>
> **一句话总结**：环里每个节点都"在工作"，但没有任何节点在"做实事"。

### 1.2 死锁（Deadlock）

**定义**：两个或多个 Agent 互相等待对方释放自己需要的资源，谁都动不了，整个系统静默挂起。

**具体例子**：

```
Agent A 持有 db_connection,  等待 file_lock
Agent B 持有 file_lock,      等待 db_connection
```

A 占了数据库连接要拿文件锁，但文件锁在 B 手里；B 占了文件锁要拿数据库连接，但数据库连接在 A 手里。两边都拿不到第二把锁，谁都不会释放自己已有的。

**为什么有害**：死锁最大的问题是"静默挂起"。系统不会抛异常（异常路径没人走），不会打错误日志（业务代码看不到死锁），只是永远停在那里等一个永远不会发生的事件。

**死锁的四个必要条件**（缺一不可）：

1. 互斥：资源一次只能被一个 Agent 持有。
2. 持有并等待：Agent 在持有资源的同时，还去申请别的资源。
3. 不可抢占：资源只能由持有者主动释放，不能强制夺走。
4. 循环等待：存在一个 Agent 链，每个 Agent 都在等下一个 Agent 释放资源。

打断其中任意一个条件，死锁就不会发生。Agent Trace 用的是第 4 条：检测"循环等待"是否出现。

> **重点讲解：死锁**
>
> 两个人过独木桥，A 从南边上，B 从北边上，在桥中间相遇。A 说"你退回去让我先过"，B 说"你退回去让我先过"。谁都不退，谁都过不去，杵在桥中间永远。外人远远看去，桥上安静站着两个人，没有争吵，没有异常，但谁都没到对岸。
>
> **记忆口诀**：独木桥上互不让，静默挂死不报错
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | 死锁会触发超时异常 | 静默挂起，无异常无日志 |
>
> **一句话总结**：互相等对方先放手，结果就是一起等到天荒地老。

### 1.3 上下文膨胀（Context Bloat）

**定义**：Agent 之间的对话轮次一多，累积的 token 超过模型上下文窗口，上下文被截断，模型的推理质量断崖式下降。

**具体例子**：30 轮多 Agent 对话之后，累积 token 达到 12 万，超过了 GPT-4o 的 128k 窗口，模型开始"忘记"前文设定，工具调用也错乱起来。

**为什么有害**：模型不会报错，而是悄悄"降智"。它可能开始重复说车轱辘话、忘记工具名、把已完成的子任务重新做一遍。表面上系统在运行，输出也有，但内容质量已经不可用。

**与"token 多"的区别**：不是 token 多就一定膨胀，要看是否超出窗口且影响推理。关键指标是"占窗口的比例"和"逼近窗口的速度"。

> **重点讲解：上下文膨胀**
>
> 让一个人背 50 页会议纪要然后答问题，前 10 页清清楚楚，到第 40 页时前面的内容开始模糊，到第 50 页时他已经忘了开头讲什么开始胡说。模型上下文窗口就是那个人的脑容量，token 就是会议纪要的页数，超出容量后不是"存不下"，而是"前面的被挤掉了"，模型像失忆了一样。
>
> **记忆口诀**：窗口就那么大，塞满了就忘前头
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | 超出窗口会报错 | 静默截断前文，模型开始"失忆"胡说 |
>
> **一句话总结**：模型不是"装不下就罢工"，而是"装不下就失忆"。

---

## 2. 图论基础

Agent Trace 的核心算法都建立在图论上。把 Agent 之间的委派、等待、调用关系画出来，就是一张"有向图"。

### 2.1 有向图（Directed Graph）

**节点（vertex）**代表实体，可以是 Agent、进程、文件、数据库表等。**有向边（edge）**代表关系，从 A 指向 B 表示"A 委派给 B"或"A 依赖 B"。在 Agent Trace 里，节点就是 Agent 的 ID，边就是委派关系，方向是"委派者 → 被委派者"。

代码上一般用邻接表表示：

```python
adj: dict[str, set[str]] = {
    "planner": {"researcher"},
    "researcher": {"writer"},
    "writer": {"planner"},
}
```

### 2.2 环（Cycle）

**环的定义**：从某个节点出发，沿着有向边走若干步，能回到出发节点。委派图里有环 = 存在循环依赖。环的长度可以是 2（两个 Agent 互踢），也可以是 3、4，甚至自环（自己委派给自己）。

**自环**是一种特殊情况：节点 A 有指向自己的边。`A -> A` 算一次循环依赖，Tarjan 会单独处理。

### 2.3 强连通分量（Strongly Connected Component, SCC）

**定义**：有向图中的一个节点子集，子集内任意两个节点都互相可达。形式化说，SCC 是极大（不能再扩大）的子集，使得子集内任意 u, v 都满足 `u → v` 且 `v → u`。

**SCC 与环的关系**：SCC 的大小大于 1 一定包含环；SCC 的大小等于 1 但节点有自环，那也包含环；只有"大小为 1 且无自环"的 SCC 不包含环。

**为什么用 SCC 而不是单纯找环**：SCC 一次扫描就能定位所有环路，不重复不遗漏。如果只在图里"找一条环"，朴素 DFS 会报告一大堆重复的环（同一个 SCC 里 DFS 的不同路径会算成不同环）。SCC 把"含环的子图整体"切出来，再在子图里挑一条代表性环路径即可。

Tarjan 算法的核心任务就是：一次 DFS 找出图中所有的 SCC，然后只挑 size > 1 的 SCC 报为环。

> **重点讲解：强连通分量 SCC**
>
> SCC 像社交圈里的"互粉群"，微博里你关注了某人，那人 also 关注了你，你们就互粉了。把互粉的人放一起，如果群里任意两个人都互粉（直接或间接），这个群就是一个 SCC。大小为 1 的 SCC 是"单机玩家"（只关注自己或不关注任何人），大小 >1 的 SCC 是"互粉群"。在 Agent 委派图里，"互粉群"就是循环依赖的温床。
>
> **记忆口诀**：SCC 是互粉群，群大于 1 就有环
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | 有环就有 SCC | SCC >= 2 个节点才算有环（单节点自环除外） |
>
> **一句话总结**：SCC 是"互相能到的小团体"，团里 >1 人就有环。

---

## 3. 循环依赖检测：Tarjan SCC 算法

### 3.1 算法背景

Robert Tarjan 在 1972 年提出 Tarjan SCC 算法。算法基于深度优先搜索（DFS），一次遍历就能找出有向图里的所有强连通分量，时间复杂度是 O(V + E)，在线性时间内达到理论下限，是最优解。

### 3.2 算法原理

Tarjan 算法维护三个数据结构：

- `index[node]`：节点在 DFS 中被首次发现的"时间戳"，每个节点唯一。
- `lowlink[node]`：从该节点出发，能回溯到的最小时间戳。等于 `min(index[node], index[任意 DFS 树上的后继], lowlink[任意 DFS 树上的后继])`。
- 一个栈 `stack`：保存当前 DFS 路径上所有"还在 SCC 内"的节点。

DFS 遍历时，规则如下：

1. 进入一个节点，给它分配 `index` 和 `lowlink`（初值相同），压栈。
2. 遍历每条出边：
   - 如果后继没访问过，递归 DFS 后继，回溯时用后继的 lowlink 更新当前节点的 lowlink。
   - 如果后继在栈里（即已经在当前 DFS 路径上），用后继的 index 更新当前节点的 lowlink。
3. 当一个节点的 `lowlink == index` 时，说明这个节点是某个 SCC 的"根"，从栈顶一直弹到该节点，弹出的所有节点构成一个 SCC。

直觉上，`lowlink` 记录的是"通过子树，我能回到的最早的祖先"，如果回到的最早祖先就是我自己，那以我为根的这棵 DFS 子树里的所有节点构成了一个孤立的强连通块。

> **重点讲解：Tarjan lowlink**
>
> 想象你在走迷宫，每到一个路口就贴一个编号贴纸（index）。lowlink 就是"我从这个路口出发，通过 DFS 子树，能回到的编号最小的那个路口"。如果你能回到的最早路口就是你自己（lowlink == index），说明你是一个环的入口，栈里你之上的节点和你组成了一个 SCC（互达小团体）。如果你能回到更早的路口（lowlink < index），说明你只是别人那个环里的一员，不是入口。
>
> **记忆口诀**：lowlink 是能回到的最早祖先，等于自己就是环入口
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | lowlink 记最小 index | 记的是"通过子树能回溯到的最小 index" |
>
> **一句话总结**：lowlink 看的是"子树内能回去多远"，不是"全局最小"。

### 3.3 为什么不用普通 DFS

普通 DFS 也能找环，但有问题：同一个 SCC 里 DFS 的不同遍历顺序可能产生很多条不同的环路径（本质是同一个 SCC 内的不同走法）；朴素的"打标记找环"对自环、双向边、长环、短环要分别写分支，代码复杂；某些图上朴素算法要遍历多次，Tarjan 一次扫描就完成。

Tarjan 的优势是：一次 DFS 找出所有 SCC，O(V + E) 时间，O(V) 空间，每个 SCC 只报一次。

### 3.4 代码实现

`src/agent_trace/detectors/cycle_detector.py` 里的核心是 `_tarjan_scc` 函数：

```python
def _tarjan_scc(adj: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan 强连通分量算法, 复杂度 O(V + E)"""
    index_counter = [0]      # 用列表是为了在闭包里能修改
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        # 1. 给节点分配时间戳, 压栈
        index[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True

        # 2. 遍历所有出边
        for successor in adj.get(node, ()):
            if successor not in index:
                # 树边: 递归 DFS
                strongconnect(successuccessor)
                lowlink[node] = min(lowlink[node], lowlink[successor])
            elif on_stack.get(successor, False):
                # 后向边 / 横跨边: 后继在栈里, 用后继的 index 更新
                lowlink[node] = min(lowlink[node], index[successor])

        # 3. 如果 lowlink == index, 自己是 SCC 根, 弹栈收一个 SCC
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
```

关键步骤解释：

- `index_counter` 用列表装一个 int 是 Python 闭包修改 nonlocal 变量的常见技巧。
- `on_stack` 判断后继是否在"当前 DFS 路径"上。如果不在（比如已属于一个已完成的 SCC），就不能用它的 index 来更新 lowlink。
- 弹栈逻辑用 `while True` 加 break，是因为 SCC 里有多少个节点事先未知，必须一直弹到遇到根节点本身为止。

`CycleDetector.find_cycles` 拿到 Tarjan 的结果后，再做一次过滤：

```python
def find_cycles(self) -> list[CycleDetected]:
    sccs = _tarjan_scc(self._adj)
    cycles: list[CycleDetected] = []
    for scc in sccs:
        if len(scc) > 1:
            # size > 1 的 SCC 必含环
            cycle_path = _extract_cycle_path(self._adj, scc)
            cycles.append(CycleDetected(cycle=tuple(cycle_path), ...))
        elif len(scc) == 1:
            node = next(iter(scc))
            # size == 1 时还要看是不是自环
            if node in self._adj.get(node, set()):
                cycles.append(CycleDetected(cycle=(node, node), ...))
    return cycles
```

`_extract_cycle_path` 在 SCC 子图里再跑一次简单 DFS，挑一条能回到起点的具体路径，方便 UI 展示和日志记录。

### 3.5 准确率门控

为了保证算法可靠，Agent Trace 用 50 张图作为基准（25 张含环、25 张无环 DAG），跑出 F1 = 1.0000。零误报（错报一个没有环的图为有环）也零漏报（有环的图都被检出）。

门控的实施方式是基准脚本跑完所有图后比较预测和真实标签，F1 不达 1.0 不允许合并代码。

---

## 4. 死锁检测：WFG + 增量 DFS

### 4.1 等待图（Wait-For Graph, WFG）

**WFG 定义**：节点是 Agent，边是"A 正在等 B 释放资源"。边的方向是"等待者 → 被等待者"。

**WFG 与资源分配图（RAG）的区别**：

- RAG 节点既包含进程又包含资源，边表示"持有"和"请求"两种关系。
- WFG 只保留 Agent 节点，把"通过资源产生的等待"压缩成一条 Agent 到 Agent 的边。WFG 关注的是"死锁是否存在"这个问题的最小信息。

**WFG 中有环 = 存在死锁**。这是 Coffman 等人 1971 年在《System Deadlocks》里证明的。

> **重点讲解：等待图 WFG**
>
> 把 WFG 想象成一张"讨债关系图"。A 借了 B 的书没还，反过来说是 B 在等 A 还书才能借给别人。在 WFG 里，边 A->B 的意思是"A 在等 B 释放资源"。如果这张讨债图里出现一个环（A 等 B，B 等 C，C 等 A），那就是死锁，三个人互相等，谁都要不回来。WFG 的精妙在于不需要知道资源是什么、有多少个，只需要知道"谁在等谁"，有环就是死锁。
>
> **记忆口诀**：等待图里找环，有环就是死锁
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | 要追踪资源的具体状态 | 只需"谁等谁"的拓扑关系 |
>
> **一句话总结**：死锁的本质是"等待的传递闭包里有环"，资源细节无关。

### 4.2 算法原理

Agent Trace 的做法是：

1. 每次 Agent 通过 `request` 申请一个被占用的资源，就在 WFG 加一条从申请者到持有者的有向边。
2. 加边后立即以申请者为起点跑 DFS，检测是否能走回自己。
3. 能走回 = 出现了环 = 死锁，立即告警。

这种"加边后立即检测"的策略叫**增量检测**。相比"周期性扫描全图"，它的好处是即时、精确；坏处是每次状态变化都要重算。但 Agent Trace 里的 Agent 数量通常 < 100 个，单次 DFS 远小于 1 毫秒，所以可以放心做增量。

### 4.3 为什么不用超时检测

"Agent A 等 B 的资源超过 30 秒"这种超时检测有两个问题：

1. 慢。告警延迟至少等于超时阈值，而死锁可能在第 1 秒就出现了。
2. 漏报。如果业务把超时设成 60 秒而资源 30 秒后就死锁了，告警在 60 秒才到，中间 30 秒内系统都是挂的。

WFG 是结构化检测，看到死锁模式就直接告警，不依赖时间假设。

### 4.4 代码实现

`src/agent_trace/detectors/deadlock_detector.py` 里的核心逻辑是 `request` 方法：

```python
def request(self, agent_id: str, resource_id: str) -> DeadlockDetected | None:
    """agent 请求资源, 资源被占则建立 WFG 边并检测死锁"""
    holder = self._resource_holder.get(resource_id)
    if holder is None:
        # 资源空闲, 直接获取
        self.acquire(agent_id, resource_id)
        return None
    if holder == agent_id:
        return None
    # 资源被他人持有, 加 WFG 边: agent_id -> holder
    self._wait_for.setdefault(agent_id, set()).add(holder)
    return self._detect_and_notify(agent_id, resource_id, holder)
```

DFS 找环的逻辑：

```python
def _dfs_find_cycle(wait_for: dict[str, set[str]], start: str) -> list[str] | None:
    path: list[str] = [start]
    visited: set[str] = {start}

    def dfs(node: str) -> list[str] | None:
        for neighbor in wait_for.get(node, ()):
            # 走到 start 自身, 找到环
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
```

注意这里的 `path.pop()` 和 `visited.discard(neighbor)` 是**回溯**：DFS 在子树没找到环时，要把节点从当前路径里撤出来，否则后续遍历会以为它还在路径上。

**`acquire` 和 `request` 的区别**：

- `acquire(agent, resource)`：尝试获取空闲资源。返回 `True` 表示成功，返回 `False` 表示资源已被占。它不会建立 WFG 边。
- `request(agent, resource)`：请求资源。资源空闲就自动 acquire；资源被占就加 WFG 边并检测死锁。

调用方的语义不同：业务用 `request` 表示"我现在就要用它，等不到就卡住"，所以请求者要承担可能的死锁；用 `acquire` 表示"先问问有没有，没有就算了"，不会参与等待关系。

### 4.5 准确率门控

基准是 20 个场景（10 个真死锁 + 10 个真安全），要求 precision = 1.0、recall = 1.0。即不允许把安全的场景误报为死锁，也不允许漏报真实的死锁。

---

## 5. 上下文膨胀预测：三层 EMA

### 5.1 EMA（指数移动平均）概念

**公式**：

```
EMA_t = alpha * x_t + (1 - alpha) * EMA_{t-1}
```

其中 `x_t` 是当前时刻的真实值，`alpha` 是平滑系数（0 到 1 之间）。展开看：

```
EMA_t = alpha * x_t + alpha * (1 - alpha) * x_{t-1} + alpha * (1-alpha)^2 * x_{t-2} + ...
```

**与简单移动平均（SMA）的区别**：SMA 给窗口里所有数据同样的权重；EMA 给近期数据更高的权重，越早的数据权重按指数衰减。`alpha` 越大，模型越敏感（更依赖最近一次观测）；`alpha` 越小，模型越平滑（更看长期趋势）。

**预测**：单步预测可以用 `EMA_t` 作为下一时刻的预测。多步预测加上趋势修正：`EMA_t + slope * steps_ahead`，其中 `slope = EMA_t - EMA_{t-1}`。

### 5.2 三层 EMA 设计

代码里 EMA 的实现其实只用了**单层** EMA（`state.ema`），但围绕它组成了三层管道：

1. **L1 精确计数**：`tiktoken` 计算当前轮次产生的 token 数。
2. **L2 趋势跟踪**：用 EMA 平滑每步的 token 增量，并附带 `prev_ema` 计算斜率。
3. **L3 阈值告警**：累积 token 超过 context window 的 50%/75%/90%/95% 时分别触发 INFO / WARNING / ERROR / CRITICAL 告警。

之所以叫"三层"，是因为这三层功能上独立又串行：第一层做事实、第二层做趋势、第三层做决策。这种"事实-趋势-决策"的分层思想在监控系统里很常见。

> **重点讲解：EMA 三层**
>
> 三层 EMA 就像天气预报的三个时间尺度。第一层是"看天"，今天比昨天热还是冷（短期增量）；第二层是"看周"，这周整体是升温还是降温趋势（中期趋势）；第三层是"看月"，这个月会不会来寒潮（长期预测）。光看今天不够，会被单日异常误导；光看月不够，反应太慢。三层叠在一起才既灵敏又稳定。EMA 比简单平均好在"昨天的天气权重比上上周大"，近期变化更敏感。
>
> **记忆口诀**：一层看天二层看周三层看月，近大远小
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | 三层是三个独立告警 | 三层叠加判断，短期看增量、中期看趋势、长期做预测 |
>
> **一句话总结**：事实 + 趋势 + 决策，三层各管一摊，合起来才准。

### 5.3 tiktoken 的作用

`tiktoken` 是 OpenAI 官方发布的 tokenizer 库，能精确计算一段文本在 GPT 系列模型里被切成多少个 token。Agent Trace 默认用 `cl100k_base` 编码，对应 GPT-4 / GPT-4o 的切分方式。

**为什么需要精确计数**：用字符数除以 4 之类的估算在中文、代码、数学公式上误差极大，可能差出 30% 以上。`tiktoken` 的误差通常在 1% 以内，这正好是门控要求的 MAE 1.0% 的来源。

### 5.4 为什么不用固定阈值

不同模型的上下文窗口差异巨大：

- GPT-3.5: 4k 或 16k
- GPT-4: 8k 或 32k
- GPT-4o: 128k
- Claude 3.5 Sonnet: 200k

不同任务的 token 分布也不同（短 QA vs 长文档总结 vs 多轮工具调用）。如果用 "token > 100k 告警" 这种固定阈值，对小窗口模型太晚，对大窗口模型太早。

`ContextBloatDetector` 在初始化时接收 `context_window` 参数，所有阈值都是相对值（占窗口的 50%/75%/90%/95%），所以无论用哪个模型都能自适应。

### 5.5 代码实现

`src/agent_trace/detectors/context_bloat.py` 的核心是 `track_tokens` 和 `_check_thresholds`：

```python
def track_tokens(self, agent_id: str, tokens: int) -> ContextBloatAlert | None:
    state = self._states.get(agent_id)
    if state is None:
        state = _AgentState()
        self._states[agent_id] = state

    # EMA 更新: prev_ema 留作下一轮算 slope
    state.prev_ema = state.ema
    if not state.history:
        state.ema = float(tokens)
    else:
        # 标准 EMA 公式
        state.ema = self._alpha * tokens + (1 - self._alpha) * state.ema

    state.history.append(tokens)
    state.current_tokens += tokens

    return self._check_thresholds(agent_id, state)
```

`predict` 方法用 EMA 趋势做前向预测：

```python
def predict(self, agent_id: str, steps_ahead: int = 1) -> int:
    state = self._states.get(agent_id)
    if state is None or not state.history:
        return 0
    slope = state.ema - state.prev_ema
    predicted_increment = state.ema + slope * steps_ahead
    predicted_total = state.current_tokens + max(0, predicted_increment) * steps_ahead
    return max(0, int(predicted_total))
```

**注意**：`predicted_increment` 是"每步的预计增量"，所以乘以 `steps_ahead` 得到 N 步后总共新增多少 token，加到 `current_tokens` 上得到 N 步后的总量。

`_check_thresholds` 用了"告警去重"机制：

```python
for level in sorted(BloatLevel, key=lambda l: -l.value):  # 从 CRITICAL 倒序
    threshold = self._thresholds[level]
    if utilization >= threshold and level not in state.fired_levels:
        triggered_level = level
        break
state.fired_levels.add(triggered_level)  # 同级别不重复触发
```

`fired_levels` 是一个 set，记录"已经触发过的告警级别"。这样在 60% 占用率时触发一次 WARNING 后，即使后几步还在 60% 上下波动，也不会重复触发 WARNING。但占用率升到 90% 时会触发新的 ERROR 告警。

### 5.6 准确率门控

基准是 100 步的模拟实验：

- 预测误差 MAE = 1.0%（EMA 在线性增长场景下几乎完美预测）。
- 告警召回率 100%（所有应当告警的时机都没漏）。

---

## 6. 异常检测：5 特征加权投票

### 6.1 特征工程

5 个特征各有意义，对应 Agent 系统里不同维度的"病征"：

| 特征 | 含义 | 来源 |
|---|---|---|
| `token_growth_rate` | 单步 token 增长率，`abs(curr - prev) / prev` | 反映 token 是否在爆炸式增长 |
| `span_error_rate` | error span 数 / 总 span 数 | 反映工具调用或推理失败的比例 |
| `handoff_depth` | agent handoff 链最大深度 | 反映委派关系是否过深（> 5 往往有问题）|
| `cycle_alert_count` | M3/M4 检测到的环/死锁事件数 | 反映结构性问题 |
| `context_utilization` | 当前 context 占用率 | 反映是否快撑爆窗口 |

**为什么选这 5 个**：它们都来自已经存在的可观测数据（OTel span、detector 告警），不需要额外的监控系统接入；每条特征对应一种已知的失败模式（token 爆炸、错误率高、调用过深、结构死锁、上下文撑爆），合起来覆盖多 Agent 系统的主要病征。

### 6.2 加权投票机制

每个特征有一个**触发阈值**和**权重**。当特征值超过阈值就计为"触发"，把它的权重累加到 `triggered_weight`。总分 = `triggered_weight / total_weight`。

| 特征 | 阈值 | 权重 |
|---|---|---|
| token_growth_rate | 0.3 | 0.25 |
| span_error_rate | 0.2 | 0.20 |
| handoff_depth | 5.0 | 0.20 |
| cycle_alert_count | 1.0 | 0.20 |
| context_utilization | 0.85 | 0.15 |

权重和是 1.0。`score >= 0.5` 判定为异常。意思是只要"最严重的两个特征（token 增长 + 错误率）"或"任一严重特征 + 上下文撑爆"等组合出现，就足以报警。

### 6.3 为什么不用机器学习

多 Agent 系统的故障是**结构性的**（环、死锁、上下文溢出），不是统计分布的偏移。这些故障的发生机制是清晰的：

- 出现环 = 代码里有循环委派。
- 出现死锁 = 资源获取顺序设计有问题。
- 上下文溢出 = 累计 token 超过窗口。

用机器学习模型去学这些"病"，有两个问题：

1. 需要训练数据，但真实生产里这些故障稀少且标注成本高。
2. ML 模型是概率性的，错误不可避免（漏报 1%、误报 1%），而结构化算法 + 规则可以做到 0%。

加权投票本质上是一个"显式的、可解释的、零训练的规则模型"，参考了 Random Forest 的"多特征投票"思想（PLAN.md M7），但去掉了训练部分，只保留投票。

> **重点讲解：结构化算法 vs ML**
>
> 查死锁和查循环依赖，本质是"图里有没有环"，这是数学问题，不是概率问题。就像"3 是不是质数"，要么是要么不是，没有"70% 是质数"。结构化算法（Tarjan、WFG）解的是确定性问题，有数学证明，所以能到 100%。而机器学习解的是概率问题（"这个用户 80% 可能会买"），天然有误报和漏报。用 ML 去检测死锁，就像用天气预报去回答"明天太阳会不会升起"，能用，但何必呢。
>
> **记忆口诀**：图里找环是数学题，不是概率题
>
> **对比**：
> | 误区 | 正解 |
> |---|---|
> | ML 一定比规则准 | 确定性问题用结构化算法，100% 准，ML 做不到 |
>
> **一句话总结**：杀鸡用牛刀不是不能，是浪费；确定性题用确定解。

### 6.4 代码实现

`src/agent_trace/detectors/anomaly_detector.py` 的 `evaluate` 方法：

```python
def evaluate(
    self,
    agent_id: str,
    token_history: Sequence[int] | None = None,
    span_total: int = 0,
    span_errors: int = 0,
    handoff_depth: int = 0,
    cycle_alerts: int = 0,
    context_tokens: int = 0,
) -> AnomalyResult:
    token_history = token_history or []
    growth_rate = self._compute_growth_rate(token_history)
    error_rate = _safe_divide(float(span_errors), float(span_total))
    ctx_util = _safe_divide(float(context_tokens), float(self._context_window))

    features = (
        self._make_feature("token_growth_rate", growth_rate),
        self._make_feature("span_error_rate", error_rate),
        self._make_feature("handoff_depth", float(handoff_depth)),
        self._make_feature("cycle_alert_count", float(cycle_alerts)),
        self._make_feature("context_utilization", ctx_util),
    )

    total_weight = sum(f.weight for f in features)
    triggered_weight = sum(f.weight for f in features if f.is_triggered)
    score = _safe_divide(triggered_weight, total_weight)
    triggered_names = tuple(f.name for f in features if f.is_triggered)

    return AnomalyResult(
        agent_id=agent_id,
        is_anomaly=score >= 0.5,
        score=score,
        features=features,
        triggered_features=triggered_names,
    )
```

`AnomalyResult` 是 frozen dataclass，调用方拿到结果后能直接看出：

- 是否异常（`is_anomaly`）
- 异常分数（`score`）
- 哪些特征触发了（`triggered_features`）

这种"模型 + 解释"的形式对调试特别有用。看到告警时直接看 `triggered_features` 就能知道是哪几个指标在报警。

### 6.5 准确率门控

基准是 100 个场景（50 个异常 + 50 个正常），跑出 F1 = 1.0000。

---

## 7. OpenTelemetry GenAI 语义规范

### 7.1 OpenTelemetry 是什么

OpenTelemetry（OTel）是 CNCF 下的可观测性标准，统一了 trace（追踪）、metric（指标）、log（日志）三类数据的采集和导出格式。一套应用只要按 OTel 规范输出数据，就能被各种后端（Jaeger、Tempo、Honeycomb 等等）接收。

**核心概念**：

- **Trace**：一次完整请求的"调用链"，从入口到出口的所有步骤串成一条链。
- **Span**：Trace 里的一步操作，是 trace 的基本单位，有开始时间、结束时间、属性、状态。
- **Attribute**：Span 上的键值对，记录这一步的元数据（模型名、token 数、错误类型等）。

### 7.2 GenAI v1.41 规范

OTel 主规范定义了通用的 span 类型（HTTP、DB、RPC 等），但 LLM / Agent 场景有自己的语义（"调用模型"、"执行工具"、"委派给另一个 Agent"），通用规范覆盖不到。GenAI 子规范专门为这类场景扩展了标准 span 类型和属性。

版本 v1.41 引入的关键扩展：

- `invoke_agent_client` 与 `invoke_agent_internal` 的拆分：前者是跨进程远程调用（API），后者是框架内 in-process 调用（LangGraph 节点之间）。
- 完整的 `gen_ai.handoff.*` 属性族（source / target / reason / type / timestamp），用于追踪 Agent 之间的委派关系。
- 推理 token（reasoning tokens）的独立计数。

**为什么需要标准化**：不同框架（LangGraph、CrewAI、AutoGen）的 Agent 交互方式不一样，没有标准就互不兼容。规范让大家用相同的属性名（比如 `gen_ai.agent.id`），跨框架的可观测性工具才能工作。

### 7.3 5 种 span 类型

`src/agent_trace/otel/emitter.py` 实现了 5 类 span，每类都有专门的数据类约束输入：

| Span 名 | SpanKind | 数据类 | 必填属性 |
|---|---|---|---|
| `create_agent` | CLIENT | `CreateAgentData` | operation, provider, agent_id, agent_name |
| `invoke_agent_client` | CLIENT | `InvokeAgentData` | operation, provider |
| `invoke_agent_internal` | INTERNAL | `InvokeAgentData` | operation, provider |
| `invoke_workflow` | INTERNAL | `InvokeWorkflowData` | operation, provider, workflow_id |
| `execute_tool` | INTERNAL | `ExecuteToolData` | operation, provider, tool_name |

`create_agent` 记录一个 Agent 实例的创建。`invoke_agent_client` 记录跨进程的 Agent 调用（如 HTTP API）。`invoke_agent_internal` 记录进程内的 Agent 调用（如 LangGraph 节点间）。`invoke_workflow` 记录多步工作流的启动。`execute_tool` 记录工具调用（search、calculator 等）。

所有 5 类 span 都通过 `AgentSpanEmitter` 的 `@contextmanager` 方法发射，确保即使业务代码抛异常，span 也会被正确 end 并打上 error 信息：

```python
@contextmanager
def create_agent(self, data: CreateAgentData) -> Iterator[Span]:
    attributes = {
        GenAIAttr.OPERATION_NAME: GenAIOperation.CREATE_AGENT,
        GenAIAttr.PROVIDER_NAME: data.provider_name,
        GenAIAttr.AGENT_ID: data.agent_id,
        GenAIAttr.AGENT_NAME: data.agent_name,
    }
    with self._tracer.start_as_current_span(
        name=GenAIOperation.CREATE_AGENT,
        kind=SpanKind.CLIENT,
        attributes=attributes,
    ) as span:
        try:
            yield span
        except Exception as exc:
            self._record_error(span, exc)
            raise

@staticmethod
def _record_error(span: Span, exc: Exception) -> None:
    span.set_status(Status(StatusCode.ERROR, str(exc)))
    span.set_attribute(GenAIAttr.ERROR_TYPE, type(exc).__name__)
    span.record_exception(exc)
```

`with` 块退出时无论是否异常都会调 `_record_error`（如果抛了异常），并重新 `raise` 把异常传给上层。这避免了"span 开了没关"的资源泄漏。

---

## 8. SQLite WAL 模式

### 8.1 为什么用 SQLite

传统可观测性工具（Langfuse、Phoenix、Helicone）依赖 Postgres、ClickHouse 这类外部数据库，意味着：

- 要装数据库服务。
- 要配账号、权限、网络。
- 团队里有人要运维这套基础设施。

Agent Trace 选了 SQLite：

- 零基础设施：Python 标准库自带 `sqlite3`，不需要额外装。
- 嵌入式：库就是文件，复制就走。
- 个人和小团队友好：`pip install agent-trace` 立即可用。

代价是不支持大规模并发写，但 Agent Trace 的典型场景（一个开发本地调试、或一个小团队看 trace）写并发低，SQLite 完全够用。

### 8.2 WAL 模式原理

**WAL = Write-Ahead Logging**。

默认（rollback journal）模式下，写事务会先把原页复制到 journal 文件，然后改主库。读事务会被写事务阻塞，因为写事务持有排他锁。

WAL 模式下，写事务把新内容追加到单独的 `*.db-wal` 文件，主库文件不动。读事务从主库读，读到的内容加上 WAL 里的追加内容得到最新视图。**读不阻塞写，写不阻塞读**，并发性能大幅提升。

定期会有 checkpoint 动作把 WAL 里的内容合并回主库。崩溃恢复时 SQLite 会重放 WAL。

### 8.3 在项目中的应用

`src/agent_trace/storage/sqlite_backend.py` 的初始化代码：

```python
def __init__(self, db_path: str) -> None:
    self._lock = threading.Lock()
    self._conn = sqlite3.connect(db_path, check_same_thread=False)
    self._conn.row_factory = sqlite3.Row
    # 关键三行 PRAGMA
    self._conn.execute("PRAGMA journal_mode=WAL")
    self._conn.execute("PRAGMA synchronous=NORMAL")
    self._conn.execute("PRAGMA foreign_keys=ON")
    self._conn.executescript(_SCHEMA_SQL)
    self._conn.commit()
```

四个 PRAGMA 各自的作用：

- `journal_mode=WAL`：切换到 WAL 模式。
- `synchronous=NORMAL`：WAL 模式下 NORMAL 足够安全（FULL 会落盘 fsync，NORMAL 不强制），性能比 FULL 好一截。
- `foreign_keys=ON`：SQLite 默认不开启外键约束（为了向后兼容），必须显式打开。
- `row_factory = sqlite3.Row`：让查询结果像字典一样可按列名访问。

`_lock` 是一个 `threading.Lock`，包了所有 CRUD 方法（用 `@_locked` 装饰器），用来在多线程写时串行化。`check_same_thread=False` 让连接可以被多个线程访问，但实际写操作要靠 `_lock` 保护。

schema 用了三表设计（`traces`、`observations`、`scores`），参考了 Langfuse 的简化模型。`CREATE INDEX IF NOT EXISTS` 给常用查询字段建了索引（`trace_id`、`parent_observation_id`、`agent_id`、`operation_name`、`session_id` 等）。

---

## 9. Web UI 技术栈

### 9.1 d3-flame-graph 火焰图原理

火焰图由 Brendan Gregg 在 2011 年提出，原本用于性能 profiling（CPU 时间、off-CPU 时间等），Agent Trace 把它改造成 token 视角。

**结构**：

- 横轴：累积值（这里是 token 数）。每个矩形的宽度 = 该步骤及其所有子步骤的 token 累积消耗。
- 纵轴：调用深度。顶层是入口，每嵌套一层加一行。
- 颜色：自身消耗（不包含子树），用暖色梯度，浅黄到深红。越红代表这一步自己烧的 token 越多。

**核心不变式**：

```
父节点 value >= 子节点 value 之和
```

这是火焰图能正确渲染的前提。代码里 `_build_flame_tree` 显式保证了这一点：

```python
def build_node(obs: Any) -> dict[str, Any]:
    name = f"{obs.agent_id or 'unknown'}:{obs.operation_name or 'op'}"
    self_tokens = (obs.input_tokens or 0) + (obs.output_tokens or 0)
    children = [build_node(c) for c in by_parent.get(obs.id, [])]
    # 父 value = 自身 token + 所有子孙累积
    value = self_tokens + sum(c["value"] for c in children)
    if value == 0:
        value = 1
    return {"name": name, "value": value, "self": self_tokens, "children": children}
```

`self` 字段额外提供"自身 token"，前端用它做颜色映射。如果只用 `value` 做颜色，所有父节点都会因为"包含子树"而显示成深色，淹没真正的热点。

**读图技巧**：

- 父节点又宽又浅 = 容器型 Agent，委派多但自己不算。
- 子节点又宽又深红 = 真热点，单步 token 爆表。
- 找到深红色的叶子，往上点一层看父节点是否也是深红色（说明"被父节点逼着烧"，是协调问题）；如果父节点浅黄色（说明"自己额外烧"），是 Agent 自身问题。

### 9.2 cytoscape.js 图可视化

`cytoscape.js` 是一个图论可视化库，能把节点和边渲染到画布上。

**布局选择**：Agent Trace 用 circle layout（所有节点均匀分布在一个圆周上），不用 force-directed（弹簧力学模拟）。

**为什么用 circle**：

1. 稳定。同一组节点用 circle layout 每次结果完全一致，force-directed 会有微小抖动。多 Agent 系统调试时需要"对比两次运行"，抖动会让人看不清变化。
2. 易读。圆形分布让所有节点对之间的距离大致相等，没有 force-directed 里那种"扎堆"和"拉成线"的视觉混乱。
3. 性能。force-directed 要跑物理模拟（O(N²) 或 O(N log N) 迭代），circle 是 O(N) 一遍排好。

边的颜色用于提示：检测出的环边用红色，普通委派用灰色。

### 9.3 FastAPI + WebSocket

**FastAPI**：基于 Starlette 和 Pydantic 的现代 Python Web 框架，核心是异步（async/await）。Agent Trace 用它提供 REST API 和 WebSocket。

**REST 端点**（在 `src/agent_trace/web/app.py`）：

- `GET /api/traces`：列出所有 trace（分页）。
- `GET /api/traces/{id}`：单个 trace 详情。
- `GET /api/traces/{id}/graph`：cytoscape 格式的 agent 调用图。
- `GET /api/traces/{id}/flame`：d3-flame-graph 格式的 span 树。
- `GET /api/health`：健康检查。

**WebSocket 端点**：

- `WS /ws/stream`：实时接收 trace 事件。客户端连上后，服务器可以主动推送新 trace / 新告警。

**为什么用 WebSocket 而不是轮询**：

1. 实时。WebSocket 推送延迟在毫秒级，轮询至少 1 秒（多数是 5~10 秒）。
2. 省资源。轮询即使没新事件也发请求；WebSocket 只在有事时推送。
3. 简化前端。前端订阅一次，不用写 setInterval 循环。

代码里的 `broadcast` 协程维护一个 `ws_clients: set[WebSocket]`，新事件来了就 `send_text` 给所有连接，断开的客户端在 `WebSocketDisconnect` 时从 set 里移除。

---

## 10. 软件工程实践

### 10.1 准确率门控

**Precision（精确率）**：在所有被判为正例的样本里，真正是正例的比例。`P = TP / (TP + FP)`。衡量"误报"。

**Recall（召回率）**：在所有真正的正例里，被检出的比例。`R = TP / (TP + FN)`。衡量"漏报"。

**F1**：P 和 R 的调和平均。`F1 = 2 * P * R / (P + R)`。只有 P 和 R 都高时 F1 才高。

**MAE（平均绝对误差）**：预测值与真实值绝对差的平均。`MAE = mean(|y_pred - y_real|)`。回归任务用。

**为什么 100% 门控重要**：Agent Trace 是"病理学诊断"工具，告警是给开发者看的。误报会浪费开发者时间（去查一个没问题的图），漏报会让真实故障在生产里裸奔。这两类错误都不可接受，所以门控要求 P = 1.0、R = 1.0、F1 = 1.0。

**门控如何实施**：

1. 每个模块有独立的基准脚本（`benchmarks/` 目录下）。
2. 基准脚本生成 N 个测试场景（正例 + 负例各一半）。
3. 跑算法，统计 TP/FP/FN，计算 P/R/F1。
4. 不达标时 CI 失败，PR 不能合并。

### 10.2 类型安全

**Python type hints**：函数签名里标注参数和返回类型。`def request(self, agent_id: str, resource_id: str) -> DeadlockDetected | None:` 表示入参是字符串，返回值可能是 `DeadlockDetected` 或 `None`。

**frozen dataclass**：`@dataclass(frozen=True)` 创建不可变对象。一旦实例化，所有字段都不能再赋值。`CycleDetected` 和 `DeadlockDetected` 都是 frozen，告警事件生成后就不允许被业务代码悄悄改掉。

```python
@dataclass(frozen=True)
class CycleDetected:
    cycle: tuple[str, ...]
    detection_method: str = "tarjan_scc"
```

**为什么用 frozen**：

1. 防止"上游改了告警对象，下游拿到的不是原始告警"这种隐蔽 bug。
2. frozen 实例是可哈希的（hashable），可以放进 set 或当 dict key，做去重。
3. 表达"这个对象是事实，不是状态"的语义。

### 10.3 测试实践

**148 个测试**覆盖 4 个检测模块 + Web API + OTel emitter，每个模块有独立基准：

- 环检测：50 张图（25 环 + 25 DAG），F1 = 1.0000。
- 死锁检测：20 场景（10 死锁 + 10 安全），P = R = 1.0。
- 上下文膨胀：100 步实验，MAE = 1.0%，告警 100% 召回。
- 异常检测：100 场景（50 异常 + 50 正常），F1 = 1.0000。
- Web 延迟：4 端点组合，平均 24.9 毫秒（< 500 毫秒）。

**edge case 覆盖**：空图、单节点、自环、双向边、长链、并发请求、释放后再请求、跨多个 Agent 的死锁等。

**不通过门控的代码不允许合并**：每个 PR 必须让所有门控测试通过，这把"准确性"变成代码的硬约束，不是事后追责的口号。跑测试用 `pytest tests/ -v`，跑单个模块用 `pytest tests/test_xxx.py -v -s`。

---

## 总结

Agent Trace 把多 Agent 系统的三类结构性故障（循环依赖、死锁、上下文膨胀）映射到了图论和统计上的成熟算法：循环依赖用 Tarjan SCC（O(V+E)）；死锁用 WFG + 增量 DFS；上下文膨胀用 tiktoken + EMA + 4 级阈值；异常检测用 5 特征加权投票。工程上用 OpenTelemetry GenAI v1.41 标准化 span、SQLite WAL 嵌入式存储、FastAPI + WebSocket 实时推送、d3-flame-graph + cytoscape.js 做可视化。所有算法通过 100% 准确率门控，可靠性是"看代码就能保证"的，不是"看运气"。
