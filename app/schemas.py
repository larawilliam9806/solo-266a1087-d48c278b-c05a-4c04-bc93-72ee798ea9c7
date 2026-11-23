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


class CorrectionOptionResult(BaseModel):
    """一张可单独订正的单据：只改它一张即可让整批成立的总数整数区间。"""

    order_no: str
    current_total: int
    min_total: int = Field(ge=0, le=MAX_TOTAL)
    max_total: int = Field(ge=0, le=MAX_TOTAL)


class CorrectionPreviewResponse(BaseModel):
    season_id: str
    batch_no: int
    feasible: bool
    contradiction_proof: list[str]
    # 只含"改这一张就能让整批成立"的单据；空列表表示必须改多张。
    options: list[CorrectionOptionResult]


class CorrectionRequest(BaseModel):
    # 复核原件后所见的批号（乐观并发），必须等于当前生效批号。
    seen_batch: int = Field(ge=1)
    order_no: str = Field(min_length=1, max_length=64)
    new_total: int = Field(ge=0, le=MAX_TOTAL)

    @field_validator("order_no")
    @classmethod
    def _strip_no(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("单号不能为空")
        return v


class CorrectionResponse(BaseModel):
    season_id: str
    batch_no: int
    feasible: bool
    corrected_order_no: str
    new_total: int
    n_orders: int
    contradiction_proof: list[str]
    message: str = "订正已随新批号发布，新单据与核算结果已一同生效"
