"""The only shape the model is allowed to answer in. Anything else = no trade."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    side: Literal["BUY", "SELL"]
    symbol: str = Field(min_length=2, max_length=20)
    quote_usd: float = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=1, max_length=1500)
    invalidation: str = Field(min_length=1, max_length=800, description="什么情况说明这个判断错了")


class AlertSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=2, max_length=20)
    op: Literal["below", "above"]
    price: float = Field(gt=0)
    note: str = Field(default="", max_length=200)


class FuturesAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["OPEN_LONG", "OPEN_SHORT", "CLOSE", "REDUCE", "SET_STOP"]
    symbol: str = Field(min_length=2, max_length=20)
    margin_usd: float | None = Field(default=None, gt=0, le=1_000_000, description="OPEN：投入保证金（USDT）")
    leverage: int | None = Field(default=None, ge=1, le=125, description="OPEN：杠杆倍数")
    stop_loss: float | None = Field(default=None, gt=0, description="OPEN 必填；SET_STOP 的新止损价")
    take_profit: float | None = Field(default=None, gt=0)
    fraction: float | None = Field(default=None, gt=0, le=1, description="REDUCE：减仓比例")
    reason: str = Field(min_length=1, max_length=1500)
    invalidation: str = Field(min_length=1, max_length=800)


class TradePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=3000)
    market_view: str = Field(default="", max_length=3000)
    actions: list[Action] = Field(default_factory=list, max_length=10)
    futures: list[FuturesAction] = Field(default_factory=list, max_length=5)
    alerts: list[AlertSpec] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)


JSON_SCHEMA = TradePlan.model_json_schema()

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class PlanError(ValueError):
    pass


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = _FENCE.search(text)
    candidate = m.group(1) if m else text
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise PlanError("输出中没有 JSON 对象")
        candidate = candidate[start:end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise PlanError(f"JSON 解析失败：{e}") from e
    if not isinstance(obj, dict):
        raise PlanError("JSON 顶层必须是对象")
    return obj


def parse_plan(text: str) -> TradePlan:
    obj = extract_json(text)
    try:
        return TradePlan.model_validate(obj)
    except ValidationError as e:
        raise PlanError("计划不符合结构：" + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])) from e
