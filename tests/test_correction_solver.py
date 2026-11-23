"""订正试算引擎单元测试与随机性质测试。

覆盖：
* 手工矛盾实例的可订正单号与整数区间（含无界方向、上限截断）；
* 无单可救（需同时改多张）时返回空；
* 随机篡改实例：区间内每个整数取值整批可行，越界取值整批仍矛盾；
* 核算次数不变量：已存矛盾证据之外再增加单据，试算的 Floyd 次数不增加。
"""

import random

import app.solver as solver_mod
from app.solver import OrderInput, correction_options, solve

CAP = 10**12


def test_direct_conflict_each_order_pinned_to_the_other_total():
    orders = [OrderInput("A", 1, 2, 10), OrderInput("B", 1, 2, 8)]
    result = solve(2, orders)
    assert not result.feasible
    options = correction_options(2, orders, result.contradiction_proof, CAP)
    assert {o.order_no for o in options} == {"A", "B"}
    by_no = {o.order_no: o for o in options}
    assert (by_no["A"].min_total, by_no["A"].max_total) == (8, 8)
    assert (by_no["B"].min_total, by_no["B"].max_total) == (10, 10)
    assert by_no["A"].current_total == 10
    assert by_no["B"].current_total == 8


def test_three_order_cycle_each_order_has_unique_fix():
    # [1,3]=10, [1,1]=6, [2,3]=3：6+3=9 != 10
    orders = [
        OrderInput("A", 1, 3, 10),
        OrderInput("B", 1, 1, 6),
        OrderInput("C", 2, 3, 3),
    ]
    result = solve(3, orders)
    assert not result.feasible
    options = correction_options(3, orders, result.contradiction_proof, CAP)
    by_no = {o.order_no: o for o in options}
    assert (by_no["A"].min_total, by_no["A"].max_total) == (9, 9)
    assert (by_no["B"].min_total, by_no["B"].max_total) == (7, 7)
    assert (by_no["C"].min_total, by_no["C"].max_total) == (4, 4)


def test_open_ended_interval_and_lower_bound_zero():
    # [1,3]=5 与 [1,1]=6 矛盾：A 至少要 6（无上界），B 最多 5。
    orders = [OrderInput("A", 1, 3, 5), OrderInput("B", 1, 1, 6)]
    result = solve(3, orders)
    options = correction_options(3, orders, result.contradiction_proof, CAP)
    by_no = {o.order_no: o for o in options}
    assert (by_no["A"].min_total, by_no["A"].max_total) == (6, CAP)
    assert (by_no["B"].min_total, by_no["B"].max_total) == (0, 5)


def test_cap_clamps_the_upper_bound():
    orders = [OrderInput("A", 1, 3, 5), OrderInput("B", 1, 1, 6)]
    result = solve(3, orders)
    options = correction_options(3, orders, result.contradiction_proof, 7)
    by_no = {o.order_no: o for o in options}
    assert by_no["A"].max_total == 7
    assert by_no["B"].max_total == 5


def test_two_independent_contradiction_groups_no_single_fix():
    orders = [
        OrderInput("A", 1, 1, 5),
        OrderInput("B", 1, 1, 6),
        OrderInput("C", 3, 3, 2),
        OrderInput("D", 3, 3, 4),
    ]
    result = solve(4, orders)
    assert not result.feasible
    # 任一单据被移除后，另一组矛盾仍在，因此没有可单独订正的单。
    assert correction_options(4, orders, result.contradiction_proof, CAP) == []


def test_irrelevant_orders_are_kept_in_trial_and_bound_the_interval():
    # 矛盾单 A/B，另有一张与它们重叠的可行约束单 C 参与区间收窄。
    # [1,2]=10 与 [1,2]=8 矛盾；C: [1,1]=3 保留 => A/B 改值后仍须 >= 3。
    orders = [
        OrderInput("A", 1, 2, 10),
        OrderInput("B", 1, 2, 8),
        OrderInput("C", 1, 1, 3),
    ]
    result = solve(2, orders)
    assert set(result.contradiction_proof) == {"A", "B"}
    options = correction_options(2, orders, result.contradiction_proof, CAP)
    by_no = {o.order_no: o for o in options}
    # A 改成 t 后与 C 共存：t 至少 3（第 1 天已占 3），且等于 B 的 8 才消直接冲突。
    assert (by_no["A"].min_total, by_no["A"].max_total) == (8, 8)
    assert (by_no["B"].min_total, by_no["B"].max_total) == (10, 10)
    # 反例：若试算时误删 C，A 改成 8 仍可行（数值碰巧相同），这里再用一个
    # 能体现 C 约束的实例：C 把区间下界抬高。
    orders2 = [
        OrderInput("A", 1, 2, 4),   # 与 B=8 冲突，且 A<C 的 3? 不冲突
        OrderInput("B", 1, 2, 8),
        OrderInput("C", 1, 1, 5),
    ]
    result2 = solve(2, orders2)
    options2 = correction_options(2, orders2, result2.contradiction_proof, CAP)
    by2 = {o.order_no: o for o in options2}
    # B 必须改成 A 的 4，但 [1,2] 总数不能低于第 1 天的 5 => 区间下界为 5，
    # 而 A=4 与 C=5 本身也冲突，故 B 无可行值（不出现在选项里）。
    assert "B" not in by2
    # A 需要同时 >= 5（C 的约束）且 = B 的 8 才能消除直接冲突。
    assert (by2["A"].min_total, by2["A"].max_total) == (8, 8)


def test_proof_excludes_orders_outside_positive_cycles():
    orders = [
        OrderInput("A", 1, 2, 10),
        OrderInput("B", 1, 2, 8),
        OrderInput("D", 4, 5, 3),
    ]
    result = solve(5, orders)
    options = correction_options(5, orders, result.contradiction_proof, CAP)
    assert {o.order_no for o in options} == {"A", "B"}


def test_random_instances_interval_values_are_feasible_and_outliers_are_not():
    rng = random.Random(20261123)
    for _ in range(300):
        n = rng.randint(2, 8)
        truth = [rng.randint(0, 5) for _ in range(n)]
        orders = []
        for k in range(rng.randint(2, 6)):
            l = rng.randint(1, n)
            r = rng.randint(l, n)
            orders.append(
                OrderInput(f"T{k}", l, r, sum(truth[l - 1 : r]))
            )
        victim = rng.choice(orders)
        tampered = [
            OrderInput(
                o.order_no, o.l, o.r,
                max(0, o.total + rng.choice([-4, -2, -1, 1, 2, 5])),
            )
            if o.order_no == victim.order_no
            else o
            for o in orders
        ]
        result = solve(n, tampered)
        if result.feasible:
            continue

        options = correction_options(n, tampered, result.contradiction_proof, CAP)
        by_no = {o.order_no: o for o in options}

        # 证据之外的单据永远不会成为可单独订正的单。
        assert set(by_no) <= set(result.contradiction_proof)

        for opt in options:
            base = next(o for o in tampered if o.order_no == opt.order_no)
            # 区间端点与内部抽样取值都必须让整批可行。
            candidates = {opt.min_total, opt.max_total}
            if opt.max_total > opt.min_total:
                candidates.add(rng.randint(opt.min_total, opt.max_total))
            for t in candidates:
                trial = [
                    OrderInput(o.order_no, o.l, o.r, t)
                    if o.order_no == opt.order_no
                    else o
                    for o in tampered
                ]
                assert solve(n, trial).feasible, (opt, t)

            # 越界一个单位（仍在合法输入域内）必须仍然矛盾。
            if opt.min_total > 0:
                trial = [
                    OrderInput(o.order_no, o.l, o.r, opt.min_total - 1)
                    if o.order_no == opt.order_no
                    else o
                    for o in tampered
                ]
                assert not solve(n, trial).feasible
            if opt.max_total < CAP:
                trial = [
                    OrderInput(o.order_no, o.l, o.r, opt.max_total + 1)
                    if o.order_no == opt.order_no
                    else o
                    for o in tampered
                ]
                assert not solve(n, trial).feasible

            # 区间外的当前值确实是矛盾批上的旧值（未被试算改写）。
            assert base.total == opt.current_total


def test_accounting_runs_do_not_grow_when_orders_outside_proof_are_added():
    # 基线矛盾批：A/B 直接冲突；D 是独立区间单据，不进证据。
    base = [
        OrderInput("A", 1, 2, 10),
        OrderInput("B", 1, 2, 8),
        OrderInput("D", 4, 5, 3),
    ]
    proof = solve(5, base).contradiction_proof
    assert set(proof) == {"A", "B"}

    real_floyd = solver_mod._floyd
    counts = {"n": 0}

    def counting_floyd(n_nodes, edges):
        counts["n"] += 1
        return real_floyd(n_nodes, edges)

    solver_mod._floyd = counting_floyd
    try:
        counts["n"] = 0
        opts_base = correction_options(5, base, proof, CAP)
        base_runs = counts["n"]

        # 证据之外再增加两张单据（包括与矛盾区间重叠、会参与最长路的单）。
        enriched = base + [
            OrderInput("E", 2, 3, 2),
            OrderInput("F", 4, 4, 1),
        ]
        enriched_proof = solve(5, enriched).contradiction_proof
        assert set(enriched_proof) == {"A", "B"}  # 证据不变
        counts["n"] = 0
        opts_enriched = correction_options(5, enriched, enriched_proof, CAP)
        enriched_runs = counts["n"]
    finally:
        solver_mod._floyd = real_floyd

    # 试算核算次数只由证据单数决定，不随证据之外的单据增加。
    assert enriched_runs == base_runs == len(proof)
    # 增加的单据确实参与了试算（区间可能被收窄），但没有带来额外核算。
    assert {o.order_no for o in opts_base} == {o.order_no for o in opts_enriched}
