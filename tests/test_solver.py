"""核算引擎单元测试。"""

from app.solver import DayBound, OrderInput, solve


def _bounds(result):
    return {d.day_index: (d.min_tickets, d.max_tickets) for d in result.days}


def test_empty_orders_zero_lower_unbounded_upper():
    r = solve(5, [])
    assert r.feasible
    for d in r.days:
        assert d.min_tickets == 0
        assert d.max_tickets is None
        assert not d.tight


def test_single_order_pins_total_and_days_inside_bounds():
    # 3 天合计 10 张：每天 0..10，整体固定但单日不紧。
    r = solve(3, [OrderInput("A", 1, 3, 10)])
    assert r.feasible
    assert _bounds(r) == {1: (0, 10), 2: (0, 10), 3: (0, 10)}
    assert r.contradiction_proof == []


def test_partition_orders_pin_every_day():
    # 日账被逐天锁定：[1,1]=4, [2,2]=7, [3,3]=2
    r = solve(
        3,
        [
            OrderInput("A", 1, 1, 4),
            OrderInput("B", 2, 2, 7),
            OrderInput("C", 3, 3, 2),
        ],
    )
    assert r.feasible
    assert _bounds(r) == {1: (4, 4), 2: (7, 7), 3: (2, 2)}
    assert all(d.tight for d in r.days)


def test_partial_overlap_redundant_consistency():
    # 重叠汇总：[1,3]=10 与 [1,2]=4 => 第 3 天必为 6
    r = solve(
        3,
        [OrderInput("A", 1, 3, 10), OrderInput("B", 1, 2, 4)],
    )
    assert r.feasible
    bounds = _bounds(r)
    assert bounds[3] == (6, 6)
    assert bounds[1] == (0, 4)
    assert bounds[2] == (0, 4)


def test_unbounded_day_outside_all_orders():
    r = solve(4, [OrderInput("A", 1, 2, 5)])
    assert r.feasible
    bounds = _bounds(r)
    assert bounds[1] == (0, 5)
    assert bounds[2] == (0, 5)
    assert bounds[3] == (0, None)
    assert bounds[4] == (0, None)


def test_contradiction_two_orders_direct_conflict():
    # 同一区间两个不同总数，直接矛盾，两单都要在证据里。
    r = solve(2, [OrderInput("A", 1, 2, 10), OrderInput("B", 1, 2, 8)])
    assert not r.feasible
    assert set(r.contradiction_proof) == {"A", "B"}


def test_contradiction_overlapping_cycle():
    # [1,3]=10, [1,1]=6, [2,3]=3 => 6+3=9 != 10，三单共同构成正环。
    r = solve(
        3,
        [
            OrderInput("A", 1, 3, 10),
            OrderInput("B", 1, 1, 6),
            OrderInput("C", 2, 3, 3),
        ],
    )
    assert not r.feasible
    assert set(r.contradiction_proof) == {"A", "B", "C"}


def test_contradiction_proof_excludes_irrelevant_order():
    # D 与矛盾无关（独立区间），证据里不应出现；且任意真子集都不再矛盾。
    r = solve(
        5,
        [
            OrderInput("A", 1, 2, 10),
            OrderInput("B", 1, 2, 8),
            OrderInput("D", 4, 5, 3),
        ],
    )
    assert not r.feasible
    assert set(r.contradiction_proof) == {"A", "B"}


def test_zero_total_order_pins_days_to_zero():
    r = solve(3, [OrderInput("A", 1, 3, 0)])
    assert r.feasible
    assert all(b == (0, 0) for b in _bounds(r).values())


def test_negative_not_allowed_violates_nonnegativity():
    # [1,2]=5 与 [1,1]=8 => 第 2 天必须为 -3，违反非负整数账。
    r = solve(
        2,
        [OrderInput("A", 1, 2, 5), OrderInput("B", 1, 1, 8)],
    )
    assert not r.feasible
    assert set(r.contradiction_proof) == {"A", "B"}


def test_daybound_tight_flag():
    d = DayBound(1, 5, 5)
    assert d.tight
    assert not DayBound(1, 0, 5).tight
    assert not DayBound(1, 0, None).tight
