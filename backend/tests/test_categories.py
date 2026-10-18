"""Coins grouped by Binance's own categories (tags)."""
from binanceclaw.core import categories as cats
from binanceclaw.exchange.binance_spot import ExchangeError

from .test_api import client  # noqa: F401  (fixture)


def test_primary_category_prefers_what_the_coin_is():
    assert cats.primary(["Solana", "pos", "Layer1_Layer2"]) == "Layer1_Layer2"
    assert cats.primary(["Payments", "pow"]) == "Payments"
    assert cats.primary([]) == cats.UNTAGGED
    assert cats.label("defi") == "DeFi" and cats.label("brand_new_tag") == "brand new tag"


def test_markets_carry_and_filter_by_category(svc):
    rows = {r["token"]: r for r in svc.markets(limit=200)}
    assert rows["SOL"]["tags"] == ["Layer1_Layer2", "Solana", "pos"] and rows["SOL"]["category"] == "Layer 1 / Layer 2"
    assert rows["QQQB"]["category"] == "未分类"
    assert {r["token"] for r in svc.markets(tag="Layer1_Layer2", limit=200)} == {"ETH", "SOL", "TON"}
    assert [c["tag"] for c in svc.categories()][:2] == ["Layer1_Layer2", "Payments"]
    assert next(c for c in svc.categories() if c["tag"] == "Layer1_Layer2")["count"] == 3


def test_categories_unavailable_degrade_to_untagged(svc):
    def blocked():
        raise ExchangeError("blocked")
    svc.client.asset_tags = blocked
    assert svc.token_tags() == {}
    assert svc.markets(limit=200)[0]["category"] == "未分类"


def test_overview_and_api(client):  # noqa: F811
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    ov = client.get("/api/overview").json()
    assert ov["binance_categories"]["BTC"] == {"primary": "支付", "tags": ["支付", "POW"]}
    assert ov["binance_categories"]["USDT"]["primary"] == "稳定币"
    assert client.get("/api/categories").json()[0]["label"] == "Layer 1 / Layer 2"
    assert {r["token"] for r in client.get("/api/markets?tag=Solana").json()} == {"SOL"}


def test_overview_categorises_futures_symbols(svc):
    svc.settings_store.update({"futures": {"symbols": ["SOL"]}})
    ov = svc.overview()
    assert ov["futures"]["symbols"] == ["SOL"]
    assert ov["binance_categories"]["SOL"]["primary"] == "Layer 1 / Layer 2"
