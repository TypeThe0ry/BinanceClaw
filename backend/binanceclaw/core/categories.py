"""Coin categories as Binance shows them on its market pages (its "tags"), for display only.

Risk grouping is unrelated: that comes from the owner's target categories (config.Settings.categories).
A coin can carry several tags; ``primary()`` picks the one used to group it in the portfolio."""
from __future__ import annotations

# Binance's tag ids -> the names binance.com uses in Chinese. Unknown tags are shown as they come.
LABELS: dict[str, str] = {
    "Layer1_Layer2": "Layer 1 / Layer 2",
    "Meme": "Meme",
    "AI": "AI",
    "RWA": "RWA",
    "defi": "DeFi",
    "Gaming": "游戏",
    "Metaverse": "元宇宙",
    "Infrastructure": "基础设施",
    "Storage": "存储",
    "Payments": "支付",
    "NFT": "NFT",
    "liquid_staking": "流动性质押",
    "fan_token": "粉丝币",
    "pow": "POW",
    "pos": "POS",
    "BSC": "BNB Chain",
    "bnbchain": "BNB Chain",
    "Solana": "Solana",
    "Polkadot": "Polkadot",
    "Launchpool": "Launchpool",
    "Launchpad": "Launchpad",
    "Megadrop": "Megadrop",
    "HODLer_Airdrops": "HODLer 空投",
    "Seed": "种子",
    "Monitoring": "观察",
    "mining-zone": "挖矿",
}

# Order used to pick a coin's primary category: what the coin is before where it lives or how it launched.
PRIORITY = ["Layer1_Layer2", "Meme", "AI", "RWA", "defi", "Gaming", "Metaverse", "Infrastructure", "Storage", "Payments",
            "NFT", "liquid_staking", "fan_token", "pow", "pos", "BSC", "bnbchain", "Solana", "Polkadot",
            "Launchpool", "Launchpad", "Megadrop", "HODLer_Airdrops", "Seed", "Monitoring"]

STABLE = "stablecoin"         # our own: Binance does not tag stablecoins
UNTAGGED = "untagged"
EXTRA_LABELS = {STABLE: "稳定币", UNTAGGED: "未分类"}


def label(tag: str) -> str:
    return LABELS.get(tag) or EXTRA_LABELS.get(tag) or tag.replace("_", " ")


def rank(tag: str) -> int:
    return PRIORITY.index(tag) if tag in PRIORITY else len(PRIORITY)


def primary(tags: list[str]) -> str:
    return min(tags, key=lambda t: (rank(t), t)) if tags else UNTAGGED


def tags_for(token: str, index: dict[str, list[str]], stables) -> list[str]:
    if token in stables:
        return [STABLE]
    return list(index.get(token) or [])
