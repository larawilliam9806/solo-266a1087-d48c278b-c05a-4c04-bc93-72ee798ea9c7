"""并发交单测试：同一演出季多线程同时交单，乐观批号保证线性化。"""

import os
import tempfile
import threading

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="ticket-api-conc-")
os.environ["TICKET_DB_PATH"] = os.path.join(_TMP, "conc.db")

from app.main import app  # noqa: E402
from app import database as db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    db.init_db()
    with TestClient(app) as c:
        yield c


def test_parallel_submissions_only_one_wins_per_batch(client):
    season = "S-CONC"
    start, end = "2026-02-01", "2026-02-05"

    # 首批：先建立批次 1
    resp = client.post(
        "/api/seasons/submissions",
        json={"season_id": season, "start_date": start, "end_date": end, "orders": []},
    )
    assert resp.status_code == 200 and resp.json()["batch_no"] == 1

    results: list[int] = []
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        r = client.post(
            "/api/seasons/submissions",
            json={
                "season_id": season,
                "start_date": start,
                "end_date": end,
                "seen_batch": 1,  # 8 个线程都只认批次 1
                "orders": [
                    {
                        "order_no": f"W{i}",
                        "start_date": start,
                        "end_date": start,
                        "total": i,
                    }
                ],
            },
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 恰好一个成功（200），其余全部批号过期（409）
    assert sorted(results).count(200) == 1
    assert sorted(results).count(409) == 7

    q = client.get(f"/api/seasons/{season}/accounting").json()
    assert q["batch_no"] == 2  # 只前进了一批
    assert len(q["orders"]) == 1


def test_concurrent_different_seasons_do_not_block(client):
    errors = []

    def submit(season):
        r = client.post(
            "/api/seasons/submissions",
            json={
                "season_id": season,
                "start_date": "2026-05-01",
                "end_date": "2026-05-02",
                "orders": [],
            },
        )
        if r.status_code != 200:
            errors.append((season, r.status_code, r.text))

    threads = [threading.Thread(target=submit, args=(f"P{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
