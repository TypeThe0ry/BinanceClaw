"""挂单盯盘: the model re-checks approved limit plans that are still waiting for their price.

For each plan it answers keep / cancel / reprice. The service enforces the rules: the amount never
changes, and only a safer price (buy lower, sell higher) is applied without the owner; a price that
would fill sooner becomes an approval request.
"""
from __future__ import annotations

import json

from .schema import PlanError, extract_json

SYSTEM = """你是 BinanceClaw 的挂单盯盘员。主人已批准下面这些现货挂单，它们还在等价格，没有成交。
你的任务：对照每一单当初的理由（reason）和"判断错了的信号"（invalidation），结合最新行情、指标和新闻
（包括 ETF 资金流、监管、安全事件、宏观数据等突发消息），判断这单还该不该继续挂着。

每一单只能选一个动作：
- keep：逻辑还成立，继续等。
- cancel：理由已不成立、invalidation 已触发，或出现了让这笔交易不再合理的突发事件。
- reprice：逻辑大体成立，但价位需要调整。金额不变，只改价格。
  买单把价格调低、卖单把价格调高，会直接生效；
  买单调高、卖单调低（更容易成交）只会提交给主人审批，除非有明确的新证据，不要这样做。

原则：
- 没有新信息就 keep，不要因为价格的正常波动来回改价。
- 价格已远离挂单价、挂单很可能到期也成交不了，本身不是撤单理由；除非原来的逻辑也变了。
- 新价格不能越过现价（买单必须低于现价，卖单必须高于现价），离现价不超过 50%。
- reason 用中文一句话，写清依据（引用具体数据或新闻）。

只输出一个 JSON 对象，不要其他文字：
{"orders": [{"id": "<挂单 id>", "action": "keep|cancel|reprice", "price": <reprice 时的新价格>, "reason": "..."}]}
"""


def build_prompt(context: dict) -> str:
    return "<data>\n" + json.dumps(context, ensure_ascii=False, default=str) + "\n</data>\n\n逐单给出判断。"


ACTIONS = ("keep", "cancel", "reprice")


def parse(text: str, ids: set[str]) -> list[dict]:
    """The model's verdicts for known plan ids (unknown ids and bad rows are dropped)."""
    obj = extract_json(text)
    rows = obj.get("orders")
    if not isinstance(rows, list):
        raise PlanError("缺少 orders 列表")
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        lid, action = str(r.get("id", "")), str(r.get("action", "")).lower()
        if lid not in ids or lid in seen or action not in ACTIONS:
            continue
        seen.add(lid)
        row = {"id": lid, "action": action, "reason": str(r.get("reason") or "")[:300]}
        if action == "reprice":
            try:
                row["price"] = float(r.get("price"))
            except (TypeError, ValueError):
                continue
            if not row["price"] > 0:
                continue
        out.append(row)
    return out
