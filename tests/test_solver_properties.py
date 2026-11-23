"""随机性质测试：用独立的枚举/可行解构造方法交叉验证求解器。

策略
----
1. 随机生成非负整数日账 x，按随机区间汇总成单据；
2. 求解器必须判定可行，且给出的 min/max 区间包含真实日值；
3. 在小规模（日数小、出票量小）时枚举全部非负整数日账，
   验证求解器的 min/max 与枚举极值逐位相等；
4. 随机篡改某张单据的总数，检查求解器与独立约束检查器在
   "可行/矛盾"判定上完全一致；矛盾时证据单据子集必须本身就矛盾。
"""

import random

from app.solver import NEG, OrderInput, _build_edges, _floyd, solve


def _independent_feasible(n_days, orders):
    """独立检查器：从 0 号前缀节点跑最长路构造 p，逐条边验证。"""
    dist = _floyd(n_days + 1, _build_edges(n_days, orders))
    if any(dist[i, i] > 0 for i in range(n_days + 1)):
        return False, None
    p = [int(dist[0, k]) for k in range(n_days + 1)]
    for e in _build_edges(n_days, orders):
        if p[e.v] < p[e.u] + e.w:
            return False, None
    x = [p[k] - p[k - 1] for k in range(1, n_days + 1)]
    if any(v < 0 for v in x):
        return False, None
    for o in orders:
        if sum(x[o.l - 1 : o.r]) != o.total:
            return False, None
    return True, x


def _feasible_with_fixed_day(n_days, orders, day, value):
    """固定 x_day = value 后，加入单据 (day, day, value) 再判可行性。"""
    pinned = orders + [OrderInput("__pin__", day, day, value)]
    ok, _ = _independent_feasible(n_days, pinned)
    return ok


def _extrema_via_feasibility(n_days, orders, cap=6):
    """通过逐值可行性判定求每日 min/max；cap+1 仍可行则视为无上限。"""
    lo = [None] * n_days
    hi: list = [None] * n_days
    for day in range(1, n_days + 1):
        for v in range(cap + 1):
            if _feasible_with_fixed_day(n_days, orders, day, v):
                if lo[day - 1] is None:
                    lo[day - 1] = v
                hi[day - 1] = v
        if _feasible_with_fixed_day(n_days, orders, day, cap + 1):
            hi[day - 1] = None
    assert all(v is not None for v in lo)
    return lo, hi


def test_random_instances_against_independent_checker():
    rng = random.Random(20261123)
    for _ in range(300):
        n = rng.randint(1, 8)
        truth = [rng.randint(0, 6) for _ in range(n)]
        orders = []
        for _ in range(rng.randint(1, 6)):
            l = rng.randint(1, n)
            r = rng.randint(l, n)
            total = sum(truth[l - 1 : r])
            orders.append(OrderInput(f"O{l}-{r}-{total}-{rng.randint(0, 99999)}", l, r, total))

        result = solve(n, orders)
        ok, x = _independent_feasible(n, orders)
        assert ok and result.feasible
        assert x is not None
        for d in result.days:
            # 真实日账必须落在报告区间内
            assert d.min_tickets <= truth[d.day_index - 1]
            assert d.max_tickets is None or truth[d.day_index - 1] <= d.max_tickets
            assert d.min_tickets <= x[d.day_index - 1]
            assert d.max_tickets is None or x[d.day_index - 1] <= d.max_tickets
            assert d.min_tickets >= 0


def test_small_instances_match_brute_force_enumeration():
    rng = random.Random(424242)
    for _ in range(60):
        n = rng.randint(1, 4)
        truth = [rng.randint(0, 3) for _ in range(n)]
        orders = []
        for _ in range(rng.randint(1, 4)):
            l = rng.randint(1, n)
            r = rng.randint(l, n)
            orders.append(
                OrderInput(f"E{l}{r}{rng.randint(0, 10**6)}", l, r, sum(truth[l - 1 : r]))
            )
        result = solve(n, orders)
        assert result.feasible
        lo, hi = _extrema_via_feasibility(n, orders, cap=6)
        for d in result.days:
            i = d.day_index - 1
            assert d.min_tickets == lo[i]
            assert d.max_tickets == hi[i]


def test_tampered_orders_feasibility_agreement_and_proof_is_self_contradictory():
    rng = random.Random(777)
    for _ in range(300):
        n = rng.randint(2, 9)
        truth = [rng.randint(0, 5) for _ in range(n)]
        orders = []
        for k in range(rng.randint(2, 6)):
            l = rng.randint(1, n)
            r = rng.randint(l, n)
            orders.append(
                OrderInput(f"T{k}", l, r, sum(truth[l - 1 : r]))
            )
        # 篡改一张单据
        victim = rng.choice(orders)
        tampered = [
            OrderInput(o.order_no, o.l, o.r, o.total + rng.choice([-3, -1, 1, 2, 5]))
            if o.order_no == victim.order_no
            else o
            for o in orders
        ]
        tampered = [o for o in tampered if o.total >= 0]

        result = solve(n, tampered)
        independent_ok, _ = _independent_feasible(n, tampered)
        assert result.feasible == independent_ok
        if not result.feasible:
            # 证据必须非空、是单据号子集、且其本身就能证实矛盾
            assert result.contradiction_proof
            assert set(result.contradiction_proof) <= {o.order_no for o in tampered}
            sub = [o for o in tampered if o.order_no in result.contradiction_proof]
            sub_ok, _ = _independent_feasible(n, sub)
            assert not sub_ok
            # 极小性：去掉证据中任何一单后都不再矛盾
            for no in result.contradiction_proof:
                smaller = [o for o in sub if o.order_no != no]
                ok_smaller, _ = _independent_feasible(n, smaller)
                assert ok_smaller


def test_unbounded_is_reported_only_when_no_reverse_path():
    rng = random.Random(99)
    for _ in range(100):
        n = rng.randint(2, 10)
        orders = []
        for k in range(rng.randint(1, 4)):
            l = rng.randint(1, n)
            r = rng.randint(l, n)
            orders.append(OrderInput(f"U{k}", l, r, rng.randint(0, 20)))
        result = solve(n, orders)
        if not result.feasible:
            continue
        dist = _floyd(n + 1, _build_edges(n, orders))
        for d in result.days:
            back = dist[d.day_index, d.day_index - 1]
            if back == NEG:
                assert d.max_tickets is None
            else:
                assert d.max_tickets == int(-back)
