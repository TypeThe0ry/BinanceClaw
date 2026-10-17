# Security & risk model

BinanceClaw lets a language model propose trades on a real exchange account. Treat it accordingly.

## Disclaimer

This project is not financial advice and comes with no warranty (see LICENSE). Models make mistakes,
markets move fast, and software has bugs. Start in paper mode, keep limits small, and only fund the
account with money you can afford to lose.

## What the model can and cannot do

| | |
|---|---|
| Decision calls | No tools at all (Claude Code runs with `--tools ""`, no MCP servers, empty working directory; API providers get a plain chat request). The only output that matters is a JSON plan validated against a strict schema (`extra="forbid"`). |
| Chat calls | Read-only: the model receives a data snapshot and can only reply with text. It cannot place orders or change settings. |
| Execution | Only the risk engine (`risk/engine.py`) turns a plan into orders, and only within hard limits. Every order is re-checked against fresh data right before it is sent. |
| Prompt injection | Market data and history are passed inside `<data>` tags and labelled as data. Even if a model is manipulated, the worst it can do is propose actions that the risk engine still caps or rejects. |

## Hard limits (enforced in code)

Symbol whitelist (spot, quote = USDT) · buy-only by default (sells always need a human) · per-order cap ·
daily order count · daily notional · daily auto-execute total (prevents splitting a large order to dodge
approval) · stablecoin floor · category band ceiling · max 3 actions per cycle · BTC 7-day drawdown pause ·
pause after repeated market-data anomalies · kill switch. Every limit is clamped to `HARD_BOUNDS` in
`config.py`, so neither the UI nor a hand-edited file can set something reckless.

## Live-mode gate

Switching to live requires a confirmation phrase **and** a passing check of
`/sapi/v1/account/apiRestrictions`: reading + spot trading on, IP whitelist on, and withdrawals,
futures, margin, internal transfer, universal transfer, options and portfolio margin **off**.
The exchange client has no withdrawal, transfer, margin or futures endpoints at all.
The owner may knowingly waive futures, margin, internal/universal transfer, options and portfolio-margin
in Settings (`live_gate_allow`); BinanceClaw still never calls those endpoints, but a leaked key would then
carry those powers. Withdrawals, the IP whitelist, reading and spot trading can never be waived.

## Secrets

* Stored only in `data/.env` (mode 600), written by `binanceclaw setup` with hidden input.
* Never returned by the API (the UI only sees booleans), never put on command lines.
* All log output passes through a redaction filter; `httpx` request logging is disabled because
  Telegram puts the bot token in the URL.
* If a secret ever leaks (logs, screenshots, chat), revoke it at the source: Binance API management,
  `@BotFather → /revoke`, your model provider's console.

## Network

The container listens on `127.0.0.1` by default. Prefer Tailscale for remote access. The dashboard uses
a single argon2-hashed password, a signed httpOnly `SameSite=Strict` cookie and login rate-limiting;
if you expose it beyond a trusted network, put it behind HTTPS.

## Reporting

Please open a private security advisory on GitHub rather than a public issue.
