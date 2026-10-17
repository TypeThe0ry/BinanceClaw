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

# Permissions the owner may knowingly leave enabled (BinanceClaw never calls these endpoints).
# Reading, spot trading, IP whitelist and — above all — withdrawals can never be waived.
WAIVABLE = {"enableFutures", "enableMargin", "enableInternalTransfer", "permitsUniversalTransfer",
            "enableVanillaOptions", "enablePortfolioMarginTrading"}

CONFIRM_PHRASE = "我确认开启实盘"


# Enabled features that need a permission switched ON: futures trading needs the futures
# permission and spot<->futures transfers (universal transfer, MAIN_UMFUTURE/UMFUTURE_MAIN only).
FEATURE_NEEDS = {"futures": ("enableFutures", "permitsUniversalTransfer")}


def needed(features: list[str]) -> set[str]:
    return {f for feat in features for f in FEATURE_NEEDS.get(feat, ())}


def check_restrictions(r: dict, allow: list[str] | None = None, need: set[str] | None = None) -> list[dict]:
    """Return one row per requirement: {label, field, expected, actual, ok, waived, waivable}."""
    allow_set = set(allow or []) & WAIVABLE
    need = (need or set()) & WAIVABLE
    rows = []
    for fld, want, label in REQUIRED:
        if fld in need:
            want = True
        actual = r.get(fld)
        ok = actual is want
        waived = not ok and fld in allow_set and actual is True
        rows.append({"label": label, "field": fld, "expected": want, "actual": actual,
                     "ok": ok or waived, "waived": waived, "waivable": fld in WAIVABLE})
    return rows


def blockers(r: dict, allow: list[str] | None = None, need: set[str] | None = None) -> list[str]:
    out = []
    for row in check_restrictions(r, allow, need):
        if not row["ok"]:
            state = "开启" if row["actual"] else "关闭"
            need = "开启" if row["expected"] else "关闭"
            out.append(f"「{row['label']}」当前为{state}，需要{need}")
    return out
