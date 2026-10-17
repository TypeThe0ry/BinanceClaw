"""Live-trading gate: refuse to enter live mode unless the API key is locked down."""
from __future__ import annotations

# (field in /sapi/v1/account/apiRestrictions, required value, human label)
REQUIRED = [
    ("enableReading", True, "读取"),
    ("enableSpotAndMarginTrading", True, "现货交易"),
    ("ipRestrict", True, "IP 白名单"),
    ("enableWithdrawals", False, "提现"),
    ("enableFutures", False, "合约"),
    ("enableMargin", False, "杠杆借贷"),
    ("enableInternalTransfer", False, "内部划转"),
    ("permitsUniversalTransfer", False, "万向划转"),
    ("enableVanillaOptions", False, "期权"),
    ("enablePortfolioMarginTrading", False, "统一账户"),
]

CONFIRM_PHRASE = "我确认开启实盘"


def check_restrictions(r: dict) -> list[dict]:
    """Return one row per requirement: {label, field, expected, actual, ok}."""
    rows = []
    for fld, want, label in REQUIRED:
        actual = r.get(fld)
        rows.append({"label": label, "field": fld, "expected": want, "actual": actual, "ok": actual is want})
    return rows


def blockers(r: dict) -> list[str]:
    out = []
    for row in check_restrictions(r):
        if not row["ok"]:
            state = "开启" if row["actual"] else "关闭"
            need = "开启" if row["expected"] else "关闭"
            out.append(f"「{row['label']}」当前为{state}，需要{need}")
    return out
