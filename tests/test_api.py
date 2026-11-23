"""API 集成测试：交单、乐观批号、核算查询、演出季隔离。"""

import os
import tempfile

import pytest

# 必须在导入 app 之前指定测试库路径。
_TMP = tempfile.mkdtemp(prefix="ticket-api-test-")
os.environ["TICKET_DB_PATH"] = os.path.join(_TMP, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import database as db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    db.init_db()
    with TestClient(app) as c:
        yield c


def _sub(season, start, end, orders, seen_batch=None):
    body = {
        "season_id": season,
        "start_date": start,
        "end_date": end,
        "orders": orders,
    }
    if seen_batch is not None:
        body["seen_batch"] = seen_batch
    return body


def test_first_submission_without_seen_batch(client):
    body = _sub(
        "S2026",
        "2026-01-01",
        "2026-01-03",
        [
            {"order_no": "A", "start_date": "2026-01-01", "end_date": "2026-01-01", "total": 4},
            {"order_no": "B", "start_date": "2026-01-02", "end_date": "2026-01-03", "total": 9},
        ],
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["batch_no"] == 1
    assert data["feasible"] is True

    q = client.get("/api/seasons/S2026/accounting").json()
    assert q["batch_no"] == 1
    assert q["feasible"] is True
    # 第 1 天锁死 4；第 2、3 天合计 9，单日 0..9。
    days = {d["day_index"]: d for d in q["days"]}
    assert days[1]["min_tickets"] == 4 and days[1]["max_tickets"] == 4
    assert days[2]["min_tickets"] == 0 and days[2]["max_tickets"] == 9
    assert days[3]["min_tickets"] == 0 and days[3]["max_tickets"] == 9
    assert [d["day_index"] for d in q["official_tickets"]] == [1]
    assert len(q["orders"]) == 2


def test_first_submission_with_seen_batch_rejected(client):
    body = _sub("S-NEW", "2026-03-01", "2026-03-02", [], seen_batch=1)
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 409
    assert client.get("/api/seasons/S-NEW/accounting").status_code == 404


def test_resubmit_requires_seen_batch_and_rejects_stale_batch(client):
    # 不带 seen_batch -> 409
    body = _sub("S2026", "2026-01-01", "2026-01-03", [])
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 409

    # 过期批号 -> 409，旧批继续生效
    body["seen_batch"] = 99
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 409
    q = client.get("/api/seasons/S2026/accounting").json()
    assert q["batch_no"] == 1  # 沿用当前批


def test_resubmit_with_correct_seen_batch_takes_effect(client):
    body = _sub(
        "S2026",
        "2026-01-01",
        "2026-01-03",
        [
            {"order_no": "A", "start_date": "2026-01-01", "end_date": "2026-01-01", "total": 4},
            {"order_no": "B", "start_date": "2026-01-02", "end_date": "2026-01-02", "total": 5},
            {"order_no": "C", "start_date": "2026-01-03", "end_date": "2026-01-03", "total": 4},
        ],
        seen_batch=1,
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 200
    assert resp.json()["batch_no"] == 2

    q = client.get("/api/seasons/S2026/accounting").json()
    assert q["batch_no"] == 2
    assert len(q["official_tickets"]) == 3  # 全部日值上下限相同


def test_contradictory_batch_is_stored_and_query_reports_proof(client):
    body = _sub(
        "S2026",
        "2026-01-01",
        "2026-01-03",
        [
            {"order_no": "A", "start_date": "2026-01-01", "end_date": "2026-01-02", "total": 10},
            {"order_no": "B", "start_date": "2026-01-01", "end_date": "2026-01-02", "total": 8},
        ],
        seen_batch=2,
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["feasible"] is False
    assert set(data["contradiction_proof"]) == {"A", "B"}

    q = client.get("/api/seasons/S2026/accounting").json()
    assert q["batch_no"] == 3
    assert q["feasible"] is False
    assert q["days"] == []
    assert set(q["contradiction_proof"]) == {"A", "B"}
    assert q["official_tickets"] == []
    # 单据本身仍存档
    assert {o["order_no"] for o in q["orders"]} == {"A", "B"}


def test_season_date_mismatch_rejected(client):
    body = _sub(
        "S2026",
        "2026-01-01",
        "2026-01-04",  # 与存档的 1-03 不一致
        [],
        seen_batch=3,
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 422


def test_validation_errors(client):
    # 超过 366 天
    body = _sub("S-BIG", "2026-01-01", "2027-01-03", [])
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 422

    # 单据超出季范围
    body = _sub(
        "S-BIG",
        "2026-01-01",
        "2026-01-05",
        [{"order_no": "X", "start_date": "2026-01-04", "end_date": "2026-01-08", "total": 1}],
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 422

    # 重复单号
    body = _sub(
        "S-BIG",
        "2026-01-01",
        "2026-01-05",
        [
            {"order_no": "X", "start_date": "2026-01-01", "end_date": "2026-01-01", "total": 1},
            {"order_no": "X", "start_date": "2026-01-02", "end_date": "2026-01-02", "total": 2},
        ],
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 422

    # 负数出票
    body = _sub(
        "S-BIG",
        "2026-01-01",
        "2026-01-05",
        [{"order_no": "X", "start_date": "2026-01-01", "end_date": "2026-01-01", "total": -1}],
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 422


def test_independent_season_can_submit_while_other_is_contradictory(client):
    body = _sub(
        "S-OTHER",
        "2026-06-01",
        "2026-06-02",
        [
            {"order_no": "Z", "start_date": "2026-06-01", "end_date": "2026-06-02", "total": 11},
        ],
    )
    resp = client.post("/api/seasons/submissions", json=body)
    assert resp.status_code == 200
    assert resp.json()["batch_no"] == 1

    q = client.get("/api/seasons/S-OTHER/accounting").json()
    assert q["batch_no"] == 1
    assert q["feasible"] is True
    days = {d["day_index"]: d for d in q["days"]}
    assert days[1]["min_tickets"] == 0
    assert days[1]["max_tickets"] == 11
    assert days[1]["date"] == "2026-06-01"
    assert days[1]["tight"] is False
    assert days[2]["max_tickets"] == 11


def test_season_listing_and_health(client):
    resp = client.get("/api/seasons")
    assert resp.status_code == 200
    ids = {s["season_id"]: s["current_batch"] for s in resp.json()}
    assert ids["S2026"] == 3
    assert ids["S-OTHER"] == 1

    assert client.get("/health").json() == {"status": "ok"}


def test_366_day_season_accepted_and_367_rejected(client):
    ok = _sub("S-LEAP", "2028-01-01", "2028-12-31", [])  # 闰年 366 天
    assert client.post("/api/seasons/submissions", json=ok).status_code == 200

    bad = _sub("S-LONG", "2027-01-01", "2028-01-02", [])  # 367 天
    assert client.post("/api/seasons/submissions", json=bad).status_code == 422
