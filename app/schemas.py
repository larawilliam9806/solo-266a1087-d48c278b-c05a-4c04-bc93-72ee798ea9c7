"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator

MAX_SEASON_DAYS = 366
MAX_TOTAL = 10**12


class OrderPayload(BaseModel):
    order_no: str = Field(min_length=1, max_length=64)
    start_date: date
    end_date: date
    total: int = Field(ge=0, le=MAX_TOTAL)

    @field_validator("order_no")
    @classmethod
    def _strip_no(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("单号不能为空")
        return v


class SubmissionRequest(BaseModel):
    season_id: str = Field(min_length=1, max_length=64)
    start_date: date
    end_date: date
    # 所见批号（乐观并发）；首次交单不填（None）。
    seen_batch: Optional[int] = Field(default=None, ge=1)
    orders: list[OrderPayload] = Field(default_factory=list)


class DayResult(BaseModel):
    day_index: int
    date: date
    min_tickets: int
    max_tickets: Optional[int]  # None 表示无上限
    tight: bool


class OrderResult(BaseModel):
    order_no: str
    start_date: date
    end_date: date
    total: int


class SubmissionResponse(BaseModel):
    season_id: str
    batch_no: int
    feasible: bool
    n_orders: int
    contradiction_proof: list[str]
    # 交单失败（批号过期等）时不返回本模型，而是 4xx；成功即随新批一同生效。
    message: str = "新单据与核算结果已一同生效"


class AccountingResponse(BaseModel):
    season_id: str
    start_date: date
    end_date: date
    batch_no: int
    feasible: bool
    contradiction_proof: list[str]
    orders: list[OrderResult]
    days: list[DayResult]
    # 正式供数：只列上下限相同的日值。
    official_tickets: list[DayResult]


class SeasonSummary(BaseModel):
    season_id: str
    start_date: date
    end_date: date
    current_batch: int
    feasible: bool
