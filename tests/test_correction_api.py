"""订正 API 集成测试：试算查询、订正提交、批号并发与换交可用性。"""

import os
import tempfile
import threading

import pytest

_TMP = tempfile.mkdtemp(prefix="ticket-corr-test-")
os.environ["TICKET_DB_PATH"] = os.path.join(_TMP, "corr.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import database as db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    db.init_db()
    with TestClient(app) as c:
        yield c


SEASON = "S-CORR"
START, END = "2026-04-01", "2026-04-05"


def _submit(client, orders, seen_batch=None):
    body = {
        "season_id": SEASON,
        "start_date": START,
        "end_date": END,
        "orders": orders,
    }
    if seen_batch is not None:
        body["seen_batch"] = seen_batch
    return client.post("/api/seasons/submissions", json=body)


def test_contradictory_batch_preview_lists_correctable_orders(client):
    # 批次 1：可行批
    r = _submit(
        client,
        [{"order_no": "Z", "start_date": START, "end_date": START, "total": 3}],
    )
    assert r.status_code == 200 and r.json()["batch_no"] == 1

    # 批次 2：矛盾批（同区间两个总数）
    r = _submit(
        client,
        [
            {"order_no": "A", "start_date": "2026-04-01", "end_date": "2026-04-02", "total": 10},
            {"order_no": "B", "start_date": "2026-04-01", "end_date": "2026-04-02", "total": 8},
        ],
        seen_batch=1,
    )
    assert r.status_code == 200
    assert r.json()["feasible"] is False

    resp = client.get(f"/api/seasons/{SEASON}/batches/2/corrections")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["batch_no"] == 2 and data["feasible"] is False
    assert set(data["contradiction_proof"]) == {"A", "B"}
    by_no = {o["order_no"]: o for o in data["options"]}
    assert set(by_no) == {"A", "B"}
    assert by_no["A"]["current_total"] == 10
    assert (by_no["A"]["min_total"], by_no["A"]["max_total"]) == (8, 8)
    assert by_no["B"]["current_total"] == 8
    assert (by_no["B"]["min_total"], by_no["B"]["max_total"]) == (10, 10)


def test_preview_stale_batch_conflicts_and_feasible_batch_rejected(client):
    # 未来批号 / 过期批号
    resp = client.get(f"/api/seasons/{SEASON}/batches/99/corrections")
    assert resp.status_code == 409
    # 可行批不能试算订正
    resp = client.get(f"/api/seasons/{SEASON}/batches/1/corrections")
    assert resp.status_code == 409


def test_preview_unknown_season_and_batch_404(client):
    assert client.get("/api/seasons/NO-SUCH/batches/1/corrections").status_code == 404

    other = "S-CORR-EMPTY"
    r = client.post(
        "/api/seasons/submissions",
        json={
            "season_id": other,
            "start_date": START,
            "end_date": END,
            "orders": [],
        },
    )
    assert r.status_code == 200
    # 存在的季、不存在的批号
    assert client.get(f"/api/seasons/{other}/batches/7/corrections").status_code == 409


def test_correction_publishes_new_batch_with_accounting(client):
    # 按试算结论把 A 改成 8，只动这一张
    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 2, "order_no": "A", "new_total": 8},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["batch_no"] == 3
    assert data["feasible"] is True
    assert data["corrected_order_no"] == "A"
    assert data["new_total"] == 8
    assert data["n_orders"] == 2
    assert data["contradiction_proof"] == []

    q = client.get(f"/api/seasons/{SEASON}/accounting").json()
    assert q["batch_no"] == 3 and q["feasible"] is True
    totals = {o["order_no"]: o["total"] for o in q["orders"]}
    assert totals == {"A": 8, "B": 8}  # B 原样保留
    # 两天合计 8 的逐日上下限
    days = {d["day_index"]: d for d in q["days"]}
    assert days[1]["min_tickets"] == 0 and days[1]["max_tickets"] == 8
    assert days[2]["min_tickets"] == 0 and days[2]["max_tickets"] == 8


def test_correction_on_feasible_batch_rejected_and_preview_after_publish(client):
    # 当前批 3 已可行，不能再订正
    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 3, "order_no": "A", "new_total": 7},
    )
    assert resp.status_code == 409
    # 对已发布的可行批号试算也被拒绝
    assert client.get(f"/api/seasons/{SEASON}/batches/3/corrections").status_code == 409


def test_stale_seen_batch_keeps_effective_batch(client):
    # 再制造一个矛盾批（批次 4）
    r = _submit(
        client,
        [
            {"order_no": "A", "start_date": "2026-04-01", "end_date": "2026-04-02", "total": 10},
            {"order_no": "B", "start_date": "2026-04-01", "end_date": "2026-04-02", "total": 8},
        ],
        seen_batch=3,
    )
    assert r.status_code == 200 and r.json()["batch_no"] == 4

    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 3, "order_no": "A", "new_total": 8},  # 旧批号
    )
    assert resp.status_code == 409
    q = client.get(f"/api/seasons/{SEASON}/accounting").json()
    assert q["batch_no"] == 4 and q["feasible"] is False  # 沿用矛盾批 4


def test_correction_that_still_infeasible_is_rejected(client):
    # 批次 4 中 A/B 直接冲突：把 A 改成 9 后仍与 B=8 冲突
    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 4, "order_no": "A", "new_total": 9},
    )
    assert resp.status_code == 409
    q = client.get(f"/api/seasons/{SEASON}/accounting").json()
    assert q["batch_no"] == 4  # 未产生新批
    assert {o["order_no"]: o["total"] for o in q["orders"]} == {"A": 10, "B": 8}


def test_correction_unknown_order_404_and_validation_422(client):
    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 4, "order_no": "NOPE", "new_total": 8},
    )
    assert resp.status_code == 404

    # 超上限 / 负数 -> 422
    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 4, "order_no": "A", "new_total": 10**12 + 1},
    )
    assert resp.status_code == 422
    resp = client.post(
        f"/api/seasons/{SEASON}/corrections",
        json={"seen_batch": 4, "order_no": "A", "new_total": -1},
    )
    assert resp.status_code == 422


def test_submission_still_works_during_trial_and_concurrent_corrections(client):
    # 试算（只读、不持写锁）进行期间，原入口交单/订正仍可换交：
    # 并发对同一矛盾批号发起订正，恰好一个成功，其余批号过期。
    results: list[int] = []
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        r = client.post(
            f"/api/seasons/{SEASON}/corrections",
            json={"seen_batch": 4, "order_no": "A", "new_total": 8},
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(200) == 1
    assert results.count(409) == 5
    q = client.get(f"/api/seasons/{SEASON}/accounting").json()
    assert q["batch_no"] == 5 and q["feasible"] is True
    assert {o["order_no"]: o["total"] for o in q["orders"]} == {"A": 8, "B": 8}


def test_correction_keeps_every_other_order_unchanged(client):
    # 含三张单据的矛盾批：订正一张，其余两张（含区间与数值）逐字保留。
    season = "S-CORR-KEEP"
    body = {
        "season_id": season,
        "start_date": "2026-07-01",
        "end_date": "2026-07-04",
        "orders": [
            {"order_no": "A", "start_date": "2026-07-01", "end_date": "2026-07-03", "total": 10},
            {"order_no": "B", "start_date": "2026-07-01", "end_date": "2026-07-01", "total": 6},
            {"order_no": "C", "start_date": "2026-07-02", "end_date": "2026-07-03", "total": 3},
        ],
    }
    r = client.post("/api/seasons/submissions", json=body)
    assert r.status_code == 200 and r.json()["feasible"] is False

    preview = client.get(f"/api/seasons/{season}/batches/1/corrections").json()
    by_no = {o["order_no"]: o for o in preview["options"]}
    assert by_no["A"]["min_total"] == 9  # 6 + 3

    r = client.post(
        f"/api/seasons/{season}/corrections",
        json={"seen_batch": 1, "order_no": "A", "new_total": 9},
    )
    assert r.status_code == 200 and r.json()["batch_no"] == 2

    q = client.get(f"/api/seasons/{season}/accounting").json()
    orders = {o["order_no"]: o for o in q["orders"]}
    assert orders["A"]["total"] == 9
    assert orders["B"]["total"] == 6
    assert orders["C"]["total"] == 3
    # 区间首尾日原样保留
    assert orders["B"]["start_date"] == "2026-07-01" and orders["B"]["end_date"] == "2026-07-01"
    assert orders["C"]["start_date"] == "2026-07-02" and orders["C"]["end_date"] == "2026-07-03"


def test_no_single_fix_preview_returns_empty_options(client):
    season = "S-CORR-NONE"
    body = {
        "season_id": season,
        "start_date": "2026-08-01",
        "end_date": "2026-08-03",
        "orders": [
            {"order_no": "A", "start_date": "2026-08-01", "end_date": "2026-08-01", "total": 5},
            {"order_no": "B", "start_date": "2026-08-01", "end_date": "2026-08-01", "total": 6},
            {"order_no": "C", "start_date": "2026-08-03", "end_date": "2026-08-03", "total": 2},
            {"order_no": "D", "start_date": "2026-08-03", "end_date": "2026-08-03", "total": 4},
        ],
    }
    r = client.post("/api/seasons/submissions", json=body)
    assert r.status_code == 200 and r.json()["feasible"] is False

    preview = client.get(f"/api/seasons/{season}/batches/1/corrections").json()
    assert preview["options"] == []
    assert set(preview["contradiction_proof"])  # 证据仍在

    # 对任何一张单提交订正都必然仍无解 -> 409，沿用当前批
    for no, value in (("A", 6), ("C", 4)):
        r = client.post(
            f"/api/seasons/{season}/corrections",
            json={"seen_batch": 1, "order_no": no, "new_total": value},
        )
        assert r.status_code == 409
    q = client.get(f"/api/seasons/{season}/accounting").json()
    assert q["batch_no"] == 1 and q["feasible"] is False
