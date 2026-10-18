"""SQLite persistence (SQLModel). Every AI cycle is stored in full — this is the audit trail
the dashboard renders as the model's "thinking"."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import JSON, Column, event
from sqlmodel import Field, Session, SQLModel, create_engine, select


def _id() -> str:
    return uuid.uuid4().hex[:12]


class Cycle(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    mode: str
    trigger: str = "schedule"                 # schedule | manual
    status: str = "running"                   # running | done | skipped | error
    provider: str = ""
    model: str = ""
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))   # input snapshot
    prompt: str = ""
    raw_output: str = ""
    plan: Optional[dict] = Field(default=None, sa_column=Column(JSON))    # validated TradePlan
    risk: list = Field(default_factory=list, sa_column=Column(JSON))      # per-action verdicts
    summary: str = ""
    error: str = ""
    duration_ms: int = 0
    usage: dict = Field(default_factory=dict, sa_column=Column(JSON))
    timing: dict = Field(default_factory=dict, sa_column=Column(JSON))   # ms per stage: market, account, model, execute


class Order(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    day: str = Field(index=True)
    mode: str
    symbol: str
    side: str
    quote_usd: float
    qty: float = 0
    price: float = 0
    fee: float = 0
    status: str                               # filled | submitted | failed | rejected
    via: str                                  # auto | approved
    client_order_id: str = ""
    cycle_id: str = ""
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Approval(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    created: float = Field(default_factory=time.time)
    expires: float
    cycle_id: str = ""
    symbol: str
    side: str
    quote_usd: float
    reason: str = ""
    invalidation: str = ""
    status: str = "pending"                   # pending | approved | rejected | expired | failed
    resolved_by: str = ""                     # telegram | web | system
    result: str = ""
    tg_message_id: Optional[int] = None
    kind: str = "spot"                        # spot | futures
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))   # futures: the full action


class AiQuestion(SQLModel, table=True):
    """A question the AI put to the owner. The answer (or the timeout outcome) is handed to the next round."""
    id: str = Field(default_factory=_id, primary_key=True)
    created: float = Field(default_factory=time.time, index=True)
    expires: float
    cycle_id: str = ""
    topic: str = ""
    text: str
    options: list = Field(default_factory=list, sa_column=Column(JSON))   # [{label, detail}]
    recommended: int = 0
    status: str = Field(default="pending", index=True)   # pending | answered | expired | cancelled
    answer: Optional[int] = None              # chosen option; on timeout with on_timeout=auto, the recommended one
    resolved_by: str = ""                     # web | telegram | timeout | system
    resolved_at: Optional[float] = None
    fed_cycle: str = ""                       # the round the outcome was handed to
    tg_message_id: Optional[int] = None


class PriceAlert(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    created: float = Field(default_factory=time.time)
    symbol: str
    op: str                                   # below | above
    price: float
    note: str = ""
    source: str = "manual"                    # manual | ai | telegram
    status: str = Field(default="active", index=True)   # active | triggered | cancelled
    triggered_at: Optional[float] = None
    trigger_price: Optional[float] = None


class FuturesPosition(SQLModel, table=True):
    """One row per position BinanceClaw opened. In paper mode this *is* the ledger; in live mode
    it holds what the exchange does not (stop/take-profit levels, reasons) and is synced from it."""
    id: str = Field(default_factory=_id, primary_key=True)
    mode: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str                                 # LONG | SHORT
    qty: float
    entry: float
    margin: float
    leverage: int
    stop: float
    take_profit: Optional[float] = None
    stop_algo_id: str = ""
    tp_algo_id: str = ""
    status: str = Field(default="open", index=True)    # open | closed
    opened: float = Field(default_factory=time.time)
    closed: Optional[float] = None
    close_price: Optional[float] = None
    realized: float = 0                       # realised P&L (after fees) of closed parts
    fees: float = 0
    close_reason: str = ""
    cycle_id: str = ""
    reason: str = ""
    invalidation: str = ""


class FuturesTrade(SQLModel, table=True):
    id: str = Field(default_factory=_id, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    day: str = Field(index=True)
    mode: str
    position_id: str = ""
    symbol: str
    action: str                               # OPEN_LONG | OPEN_SHORT | CLOSE | REDUCE | SET_STOP | STOP_HIT | TP_HIT | LIQUIDATED
    qty: float = 0
    price: float = 0
    margin: float = 0
    pnl: float = 0
    fee: float = 0
    status: str = "filled"                    # filled | failed | unknown
    via: str = "auto"                         # auto | approved | manual | exchange
    client_order_id: str = ""
    cycle_id: str = ""
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))


class EquitySnapshot(SQLModel, table=True):
    """Account total over time, for the home page's 1D / 7D / 30D / 90D / 1Y valuation chart."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    mode: str = Field(index=True)
    total: float                              # USD value of the whole account (incl. locked / unselected assets)
    source: str = "recorder"                  # recorder (periodic) | cycle (imported decision rounds) | binance (daily account snapshot)


class PaperBalance(SQLModel, table=True):
    asset: str = Field(primary_key=True)
    free: float = 0


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    channel: str                              # web | telegram
    role: str                                 # user | assistant
    text: str
    provider: str = ""


class Report(SQLModel, table=True):
    day: str = Field(primary_key=True)
    ts: float = Field(default_factory=time.time)
    text: str


class KV(SQLModel, table=True):
    k: str = Field(primary_key=True)
    v: str


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        SQLModel.metadata.create_all(self.engine)
        self._add_missing_columns()
        self.write_lock = threading.RLock()

    def _add_missing_columns(self):
        """Tiny forward-only migration: add columns that newer versions declare (create_all never alters)."""
        with self.engine.begin() as conn:
            for table in SQLModel.metadata.sorted_tables:
                have = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info('{table.name}')")}
                for col in table.columns:
                    if col.name in have:
                        continue
                    default = getattr(col.default, "arg", None)
                    ddl = f"ALTER TABLE \"{table.name}\" ADD COLUMN \"{col.name}\" {col.type.compile(self.engine.dialect)}"
                    if isinstance(default, bool):
                        ddl += f" DEFAULT {int(default)}"
                    elif isinstance(default, (int, float)):
                        ddl += f" DEFAULT {default}"
                    elif isinstance(default, str):
                        ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                    elif str(col.type) == "JSON":
                        ddl += " DEFAULT '{}'"
                    conn.exec_driver_sql(ddl)

    def session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # ---- generic helpers ----
    def add(self, obj):
        with self.write_lock, self.session() as s:
            s.add(obj)
            s.commit()
            s.refresh(obj)
            return obj

    def get(self, model, key):
        with self.session() as s:
            return s.get(model, key)

    def all(self, stmt):
        with self.session() as s:
            return list(s.exec(stmt))

    # ---- kv ----
    def kv_get(self, k: str, default: Any = None) -> Any:
        row = self.get(KV, k)
        return json.loads(row.v) if row else default

    def kv_set(self, k: str, v: Any):
        with self.write_lock, self.session() as s:
            row = s.get(KV, k) or KV(k=k, v="null")
            row.v = json.dumps(v)
            s.add(row)
            s.commit()

    # ---- domain queries ----
    def orders_for_day(self, day: str) -> list[Order]:
        return self.all(select(Order).where(Order.day == day, Order.status.in_(("filled", "submitted", "unknown"))))

    def pending_approvals(self) -> list[Approval]:
        return self.all(select(Approval).where(Approval.status == "pending").order_by(Approval.created))

    def claim_approval(self, aid: str, new_status: str, by: str) -> Optional[Approval]:
        """Atomically pending -> new_status. Returns the row only for the single winner."""
        with self.write_lock, self.session() as s:
            a = s.get(Approval, aid)
            if not a or a.status != "pending":
                return None
            a.status = new_status
            a.resolved_by = by
            s.add(a)
            s.commit()
            s.refresh(a)
            return a

    def pending_questions(self) -> list[AiQuestion]:
        return self.all(select(AiQuestion).where(AiQuestion.status == "pending").order_by(AiQuestion.created))

    def claim_question(self, qid: str, new_status: str, by: str, answer: Optional[int]) -> Optional[AiQuestion]:
        """Atomically pending -> answered / expired. Returns the row only for the single winner."""
        with self.write_lock, self.session() as s:
            q = s.get(AiQuestion, qid)
            if not q or q.status != "pending":
                return None
            q.status, q.resolved_by, q.answer, q.resolved_at = new_status, by, answer, time.time()
            s.add(q)
            s.commit()
            s.refresh(q)
            return q

    def update(self, _table, _key, /, **fields):
        with self.write_lock, self.session() as s:
            row = s.get(_table, _key)
            if row is None:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            s.add(row)
            s.commit()
            s.refresh(row)
            return row
