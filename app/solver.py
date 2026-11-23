"""出票汇总单核算引擎（差分约束 / 全点对最长路）。

建模
----
设演出季共 ``n_days`` 天，第 d 日的出票量为非负整数 x_d，前缀和

    p_0 = 0, p_k = x_1 + ... + x_k  (1 <= k <= n_days)

约束：

1. 出票量非负且为整数：x_d = p_d - p_{d-1} >= 0
   -> 最长路边 (d-1 -> d, 权 0)
2. 一张单据表示区间 [l, r]（季内序号，均计入）出票总数为 t：
   p_r - p_{l-1} = t
   -> 边 (l-1 -> r, 权 t) 与边 (r -> l-1, 权 -t)

最长路观点：边 u -> v 权 w 表示 p_v >= p_u + w。
全点对最长路跑完后：

* 出现 dist[i][i] > 0（正环）<=> 约束组无解 <=> 单据互相矛盾；
* min(x_d) = dist[d-1][d]（直连的 0 权边保证至少为 0）；
* max(x_d) = -dist[d][d-1]，若 d 到 d-1 不可达则无上限；
* 上下限相等即该日出票量被单据唯一确定，可作为正式供数。

整数性：单据约束矩阵是连续 1 矩阵（interval matrix），属于全幺模矩阵，
线性松弛的可行域极点全为整数，故非负整数账与松弛问题的结论完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# 不可达哨兵。路径权值上界：单据总数上限 1e12、最多 366 张单据，
# 任何真实路径都远小于 1e18，用 int64 安全。
NEG = np.int64(-(10**18))


@dataclass(frozen=True)
class OrderInput:
    """一张交单（l/r 为季内 1 基序号，首尾均计入）。"""

    order_no: str
    l: int
    r: int
    total: int


@dataclass(frozen=True)
class Edge:
    u: int
    v: int
    w: int
    owner: Optional[str] = None  # 单据号；None 表示非负性结构边


@dataclass(frozen=True)
class DayBound:
    day_index: int  # 季内 1 基序号
    min_tickets: int
    max_tickets: Optional[int]  # None 表示无上限

    @property
    def tight(self) -> bool:
        return self.max_tickets is not None and self.min_tickets == self.max_tickets


@dataclass(frozen=True)
class SolveResult:
    feasible: bool
    days: list[DayBound] = field(default_factory=list)
    # 能单独证实矛盾的一组单据号（包含意义下极小）；可行时为空。
    contradiction_proof: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CorrectionOption:
    """一张可单独订正的单据：只改它一张时总数可取的整数闭区间。"""

    order_no: str
    current_total: int
    min_total: int
    max_total: int


def _floyd(n_nodes: int, edges: list[Edge]) -> np.ndarray:
    """全点对最长路，返回距离矩阵（不可达为 NEG）。"""
    dist = np.full((n_nodes, n_nodes), NEG, dtype=np.int64)
    np.fill_diagonal(dist, np.int64(0))
    for e in edges:
        if e.w > dist[e.u, e.v]:
            dist[e.u, e.v] = np.int64(e.w)

    for k in range(n_nodes):
        col = dist[:, k].copy()
        row = dist[k, :].copy()
        # 两端都必须可达，避免 NEG + 有限值产生伪路径。
        reachable = (col != NEG)[:, None] & (row != NEG)[None, :]
        cand = col[:, None] + row[None, :]
        upd = reachable & (cand > dist)
        # 注意：不能屏蔽对角线更新——正环正是通过 dist[i][i] > 0 暴露的。
        dist = np.where(upd, cand, dist)
    return dist


def _build_edges(n_days: int, orders: list[OrderInput]) -> list[Edge]:
    edges: list[Edge] = [Edge(i - 1, i, 0) for i in range(1, n_days + 1)]
    for o in orders:
        edges.append(Edge(o.l - 1, o.r, o.total, o.order_no))
        edges.append(Edge(o.r, o.l - 1, -o.total, o.order_no))
    return edges


def _is_feasible(n_days: int, orders: list[OrderInput]) -> tuple[bool, np.ndarray]:
    dist = _floyd(n_days + 1, _build_edges(n_days, orders))
    return not bool(np.any(np.diag(dist) > 0)), dist


def _compressed_floyd(
    n_days: int,
    subset: list[OrderInput],
    extra_nodes: Optional[set[int]] = None,
) -> tuple[np.ndarray, dict[int, int]]:
    """在只含相关前缀节点的压缩图上跑最长路。

    只保留 0、n_days、各单据端点（及 extra_nodes），非负性的 0 权边
    压缩为相邻保留节点之间的跨接边。路径权值与原图一致（中间边均为 0），
    但节点规模随单据数缩约，Floyd 更快。返回距离矩阵与原节点->压缩下标。
    """
    touched = {0, n_days}
    for o in subset:
        touched.update((o.l - 1, o.r))
    if extra_nodes:
        touched.update(extra_nodes)
    nodes = sorted(touched)
    index = {node: i for i, node in enumerate(nodes)}
    m = len(nodes)

    edges: list[Edge] = []
    # 子图上的非负性结构边：相邻前缀节点之间的 0 权边（含跨接）。
    for a, b in zip(nodes, nodes[1:]):
        edges.append(Edge(index[a], index[b], 0))
    for o in subset:
        edges.append(Edge(index[o.l - 1], index[o.r], o.total, o.order_no))
        edges.append(Edge(index[o.r], index[o.l - 1], -o.total, o.order_no))

    return _floyd(m, edges), index


def _subset_feasible(n_days: int, subset: list[OrderInput]) -> bool:
    """只在候选单据子集上判可行性：Floyd 规模随子集缩约快速下降。"""
    if not subset:
        return True
    dist, _ = _compressed_floyd(n_days, subset)
    return not bool(np.any(np.diag(dist) > 0))


def _contradiction_proof(
    n_days: int, orders: list[OrderInput], dist: np.ndarray
) -> list[str]:
    """找出能单独证实矛盾的一组单号，并贪心极小化。"""
    by_no = {o.order_no: o for o in orders}
    candidates: set[str] = set()
    # 边 (u->v, w) 在某个正环上 <=> w + dist[v][u] > 0。
    for o in orders:
        u1, v1, w1 = o.l - 1, o.r, o.total
        u2, v2, w2 = o.r, o.l - 1, -o.total
        for u, v, w in ((u1, v1, w1), (u2, v2, w2)):
            back = dist[v, u]
            if back != NEG and w + int(back) > 0:
                candidates.add(o.order_no)
                break

    # 贪心缩约：逐个尝试移除，移除后仍矛盾则永久剔除，
    # 得到包含意义下极小的矛盾单据组。检查始终只在候选子集上进行。
    # 按单号排序保证输出确定，不受集合迭代顺序（哈希随机化）影响。
    proof = sorted(candidates)
    for no in list(proof):
        subset = [by_no[n] for n in proof if n != no]
        if not _subset_feasible(n_days, subset):
            proof.remove(no)
    return proof


def solve(n_days: int, orders: list[OrderInput]) -> SolveResult:
    """对一批单据做完整核算。"""
    feasible, dist = _is_feasible(n_days, orders)
    if not feasible:
        proof = _contradiction_proof(n_days, orders, dist)
        return SolveResult(feasible=False, contradiction_proof=proof)

    days: list[DayBound] = []
    for d in range(1, n_days + 1):
        min_v = int(dist[d - 1, d])
        back = dist[d, d - 1]
        max_v = None if back == NEG else int(-back)
        days.append(DayBound(day_index=d, min_tickets=min_v, max_tickets=max_v))
    return SolveResult(feasible=True, days=days)


def correction_options(
    n_days: int,
    orders: list[OrderInput],
    proof: list[str],
    max_total: int,
) -> list[CorrectionOption]:
    """列出所有"只改一张就能让整批成立"的单据及其总数可填整数区间。

    试算规则
    --------
    * 只评估已存矛盾证据 ``proof`` 中的单据：不在证据里的单据不可能
      单独承担矛盾（其边不在任何正环上），证据之外再加单据不会增加
      本函数的核算次数，每次试算都保留其余全部单据；
    * 对候选单 o（区间 [l,r]），取除 o 之外所有单据构成的"余下图"，
      在压缩图上跑一次最长路。余下图仍有正环 => 改 o 一张无救，跳过；
    * 余下图可行时，新总数 t 必须同时满足
          p_r - p_{l-1} = t
      的两个半空间约束：
          t >= 每条 (l-1) -> r 路径权值的最大值 dist[a][b]（a=l-1, b=r）
          t <= -dist[b][a]（b=r 到 a=l-1 的最长路取负）
      不可达方向不产生界；再与非负/总数上限取交，得到整数闭区间。

    整数性同样由区间约束矩阵的全幺模性保证：区间内每个整数 t 都对应
    一个非负整数日账，不会出现松弛可行而整数无解。
    """
    by_no = {o.order_no: o for o in orders}
    options: list[CorrectionOption] = []

    # 按单号排序试算，保证 options 顺序确定，不依赖外部集合顺序。
    for no in sorted(proof):
        target = by_no[no]
        others = [o for o in orders if o.order_no != no]
        a, b = target.l - 1, target.r

        dist, index = _compressed_floyd(n_days, others, extra_nodes={a, b})
        ia, ib = index[a], index[b]

        # 余下图自身仍矛盾：只改这一张无法让整批成立。
        if bool(np.any(np.diag(dist) > 0)):
            continue

        lower = 0 if dist[ia, ib] == NEG else int(dist[ia, ib])
        upper = max_total if dist[ib, ia] == NEG else int(-dist[ib, ia])
        lower = max(lower, 0)
        upper = min(upper, max_total)
        if lower <= upper:
            options.append(
                CorrectionOption(
                    order_no=no,
                    current_total=target.total,
                    min_total=lower,
                    max_total=upper,
                )
            )

    return options
