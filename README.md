# 剧院出票日报补档 API

剧院只保留出票汇总单（按统计区间记录出票总数），且统计日期多有重叠。
本服务按**演出季整批交单**，把汇总单建模为差分约束，补算每日出票量的
最低/最高可能值，矛盾时给出可单独证实矛盾的一组单号。

- 框架：FastAPI + Pydantic v2
- 存档：SQLite（WAL 模式，数据落 `/data` 卷）
- 部署：Docker / Docker Compose
- 核算：NumPy 向量化全点对最长路（Floyd），单季最多 366 天

## 目录结构

```
app/
  main.py        FastAPI 路由、乐观批号并发控制、事务边界
  solver.py      差分约束建模、最长路核算、矛盾证据提取
  database.py    SQLite schema 与数据访问
  schemas.py     请求/响应模型与校验
tests/           单元、随机性质、API 集成、并发测试（27 个）
Dockerfile
docker-compose.yml
```

## 启动

```bash
docker compose up -d --build
# 文档：http://localhost:8000/docs
```

本地开发（不使用 Docker）：

```bash
pip install -r requirements-dev.txt
TICKET_DB_PATH=./data/tickets.db uvicorn app.main:app --reload
pytest
```

## 业务规则与接口约定

### 交单 `POST /api/seasons/submissions`

按演出季整批交单，请求体：

```json
{
  "season_id": "S2026",
  "start_date": "2026-03-01",
  "end_date": "2026-03-05",
  "seen_batch": 1,
  "orders": [
    {"order_no": "A01", "start_date": "2026-03-01", "end_date": "2026-03-03", "total": 120},
    {"order_no": "A02", "start_date": "2026-03-01", "end_date": "2026-03-01", "total": 40}
  ]
}
```

- 填本季起止日，跨度（首尾均计入）**最多 366 天**；
- 每单填单号、季内统计首尾日、出票总数（非负整数）；
- **首次交单不填 `seen_batch`**；再次交单必须携带当前生效批号；
- 所见批号仍有效时，新单与核算结果在同一事务中**一同生效**，
  批号 +1；批号已过期则返回 `409`，**沿用当前批**，数据不变；
- 单据本身互相矛盾不算交单失败：矛盾批照常存档生效（批号前进），
  核算查询给出矛盾证据；
- 参数/日期非法（超 366 天、单据超出季范围、单号重复、负数等）返回 `422`。

### 核算查询 `GET /api/seasons/{season_id}/accounting`

返回当前生效批号、该批单据、逐日核算结果、矛盾证据：

- `batch_no`：当前生效批号；
- 每日给出 `min_tickets` / `max_tickets`，**`max_tickets = null` 表示无上限**；
- `official_tickets`：**正式供数，只列上下限相同的日值**；
- `feasible = false` 时 `contradiction_proof` 给出能**单独证实矛盾**的
  一组单号（该子集自身已矛盾，且去掉其中任意一单即不再矛盾）。

### 辅助接口

- `GET /api/seasons`：各演出季当前批号与可行性一览；
- `GET /health`：存活探针。

## 核算原理

设季内第 d 日出票量为非负整数 x_d，前缀和 p_0=0、p_k=Σx。

| 业务含义 | 约束 | 最长路边 |
| --- | --- | --- |
| 出票量非负 | p_d − p_{d−1} ≥ 0 | d−1 → d，权 0 |
| 单据 [l,r] 共 t 张 | p_r − p_{l−1} = t | l−1 → r 权 t；r → l−1 权 −t |

全点对最长路之后：

- 出现 `dist[i][i] > 0`（正环）⇔ 约束无解 ⇔ 单据矛盾；
- 日最低出票量 = `dist[d−1][d]`；
- 日最高出票量 = `−dist[d][d−1]`，若 d → d−1 不可达则**无上限**；
- 上下限相等即该日被单据唯一确定，进入正式供数。

整数性保证：单据约束矩阵是连续 1 矩阵（interval matrix），属于全幺模
矩阵，线性松弛可行域的极点全为整数，因此"非负整数日账"的可行性与
逐日极值与最长路（线性松弛）结论完全一致，不会出现 LP 有解但整数无解。

矛盾证据提取：边 u→v（权 w）落在某个正环上当且仅当
`w + dist[v][u] > 0`；收集这些边所属单据后，再逐单尝试移除并重新判定，
得到包含意义下极小的矛盾单据组。

## 并发与一致性

- 每个演出季一把进程内互斥锁，同季交单串行化；不同演出季互不阻塞，
  核算期间其他演出季仍可正常交单；
- 锁内开启 SQLite `BEGIN IMMEDIATE` 事务，先核对所见批号再整批写入，
  单据与核算结论原子提交；
- 核算查询只读最新已生效批次，交单失败时查询结果不受影响。
