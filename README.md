# BinanceClaw

**让 AI 管理你的币安账户（现货、可选的 U 本位合约和活期理财），风控由代码把关。**
An AI-driven Binance **spot** portfolio manager: a model of your choice decides, a deterministic risk engine enforces hard limits, you approve anything large — from a web dashboard or Telegram.

> ⚠️ 这是自托管的个人工具，不是投资建议。加密资产波动剧烈，AI 会犯错。先用模拟盘跑足够久，再决定是否开启实盘，并且只投入你能承受损失的资金。
> Not financial advice. Run in paper mode first. Use at your own risk — see [SECURITY.md](SECURITY.md).

---

## 它做什么

```
 每 N 分钟 ─► 行情 + 账户快照 ─► AI 模型（只输出 JSON 计划，无任何工具）
                                        │
                                        ▼
                          风控引擎（纯代码，模型无法绕过）
             ┌──────────────┬──────────────┬───────────────┐
             ▼              ▼              ▼               ▼
         ≤ 自动额度      > 自动额度       超出限制         暂停条件
         自动执行     网页/Telegram 审批   截断或拒绝      不调用模型
             └──────────────┴──────┬───────┘
                                   ▼
                 审计记录：输入 · 提示词 · 原始输出 · 每条风控检查 · 成交
```

- **多种模型**：Claude 订阅（通过 Claude Code CLI，不需要 API Key）、OpenAI、任意 OpenAI 兼容接口（DeepSeek、通义、Ollama、OpenRouter…）、Anthropic API。网页里一键切换。
- **资金管理**：锁定资产（AI 看不到、不能用）、AI 投入预算；AI 自主分配（默认可交易全部现货资产，可选白名单或黑名单）或手动分配（你定比例，偏离超过阈值自动再平衡）；目标配置可以自己设、让 AI 生成，或直接用一句话让 AI 修改（应用前只是提案）。
- **风险预设**：保守 / 均衡 / 积极，一键切换，随时在网页或 Telegram 干预。
- **盈亏与提醒**：按平均成本法算每个币的成本和盈亏，并和「全买 BTC」对比；价格提醒可以手动设，也可以由 AI 根据它的判断设置。
- **硬风控**：币种白名单、只买不卖（可选开启卖出，但永远需要人工批准）、单笔上限、每日笔数与金额上限、每日自动执行总额（防止拆单绕过审批）、稳定币下限、类别区间上限、BTC 7 日跌幅自动暂停、行情异常自动暂停、紧急停止。所有数值都有不可突破的上下限。
- **全市场现货**：可从币安现货的全部资产（含代币化股票）里搜索选择，列表实时来自币安；暂停交易的交易对（如 TON）也能选，恢复交易后自动可交易。
- **合约（可选）**：USDⓈ-M 永续，逐仓、杠杆上限、强制交易所止损、单笔亏损上限，开仓默认要审批，见 [docs/futures.md](docs/futures.md)。
- **理财（默认开启）**：闲置资产自动申购所有可申购的活期产品（随时可赎回），买卖前自动赎回，见 [docs/earn.md](docs/earn.md)。
- **危险设置二次确认**：改风控、资金、合约、理财等设置要再确认一次，服务端校验确认令牌，见 [docs/decisions-and-risk.md](docs/decisions-and-risk.md#危险设置要二次确认)。
- **币安分类**：选资产和资产页按币安自己的币种分类（Layer 1 / Layer 2、Meme、AI、DeFi……）筛选和分组。
- **实盘准入**：开启实盘前自动读取币安 Key 权限，开着提现就拒绝；没用到的合约、杠杆、划转权限默认也要求关闭，并要求输入确认短语。
- **模拟盘**：真实行情 + 虚拟资金，和实盘走完全相同的决策与风控流程。
- **网页仪表盘**：总览、AI 决策流（完整"思考过程"）、审批、K 线与成交、对话、日报、设置。深色玻璃风格，桌面和手机都能用，SSE 实时刷新。
- **Telegram**：审批按钮、成交与暂停通知、每日日报、`/status /run /pause /resume /kill`，直接发文字就能和模型对话（只读）。

## 快速开始（Docker）

```bash
git clone https://github.com/TypeThe0ry/BinanceClaw.git && cd BinanceClaw
docker compose build
docker compose run --rm -it binanceclaw setup     # 设置网页密码、币安 Key、模型、Telegram（输入不回显）
docker compose run --rm binanceclaw doctor        # 自检
docker compose up -d                              # http://127.0.0.1:8686
```

默认只监听 `127.0.0.1`。想从手机访问，推荐 Tailscale（`tailscale serve --bg --tcp=8686 tcp://127.0.0.1:8686`），或在 `docker-compose.override.yml` 里加一个局域网地址，参考 `docker-compose.claude.yml.example`。

### 使用 Claude 订阅

二选一：
1. 在任意已登录的机器上运行 `claude setup-token`，把得到的 token 在 `setup` 里填入 `CLAUDE_CODE_OAUTH_TOKEN`；
2. 宿主机已登录 Claude Code 时，把 `~/.claude` 和 `~/.claude.json` 挂载进容器（见 `docker-compose.claude.yml.example`）。

决策调用以 `--tools ""`、`--strict-mcp-config`、空工作目录运行：模型拿不到任何工具，只能返回 JSON。

### 不用 Docker

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e .
cd ../frontend && npm ci && npm run build
cd ../backend && BINANCECLAW_DATA=../data .venv/bin/binanceclaw setup
BINANCECLAW_DATA=../data .venv/bin/binanceclaw serve      # --host / --port 可选
```

## 币安 API Key

在币安「API 管理」创建（建议用独立子账户）：

| 权限 | 设置 |
|---|---|
| 读取 | ✅ 开启 |
| 现货交易 | ✅ 开启 |
| IP 白名单 | ✅ 只填服务器的公网出口 IP |
| 提现 / 合约 / 杠杆 / 万向划转 / 内部划转 / 期权 | ❌ 全部关闭 |

不满足时，模拟盘照常可用，但设置页的「开启实盘」会保持锁定并列出原因。

如果你清楚风险、需要保留合约、杠杆或划转权限（例如同一把 Key 还给别的工具用），可以在设置页逐项勾选「允许保留」。BinanceClaw 本身不会调用这些接口；**提现和 IP 白名单永远强制检查，不能豁免。**

## 配置

在网页「设置」里修改（保存到 `data/settings.json`）：

| 项 | 默认 | 说明 |
|---|---|---|
| 目标配置 | BTC 60%（50–70%）· ETH 5%（3–8%）· 美股代币 10%（5–15%）· 稳定币 25%（15–35%） | AI 的锚；区间上限由风控强制 |
| 单笔上限 / 自动执行上限 | $800 / $200 | 超过自动额度的进入审批 |
| 每日自动执行总额 | $400 | 防止把大单拆成多笔小单 |
| 每日笔数 / 总额 | 3 笔 / $2,400 | |
| 稳定币下限 | 15% | 买入后不得低于此比例 |
| BTC 7 日跌幅暂停线 | 25% | 触发后暂停，`/resume` 恢复 |
| 决策方式 | AI 自主实时 | AI 自己定下次复查时间，价格波动、关键价位、止损止盈、成交、余额变化会实时唤醒它；最短间隔 5 分钟、最长 240 分钟、每天最多 48 次调用 |
| 审批有效期 | 30 分钟 | |

密钥只放在 `data/.env`（权限 600），由 `binanceclaw setup` 写入，网页上只显示"已配置"。

## 开发

```bash
cd backend && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest -q && .venv/bin/ruff check .
cd frontend && npm run dev     # 代理到 127.0.0.1:8686
```

测试覆盖风控每条硬限制、对抗性的 AI 输出（卖出、非白名单、合约交易对、超额、拆单、多余字段、非 JSON）、审批的并发与过期、实盘准入、日志脱敏。

## 结构

```
backend/binanceclaw/
  exchange/   binance_spot.py（现货 / 活期理财 / 现货↔合约划转）· binance_futures.py（U 本位永续）· broker.py · futures_broker.py（实盘 / 模拟盘）
  brain/      providers.py · prompt.py · schema.py（严格 JSON 结构）
  risk/       engine.py（硬限制，纯函数）· gate.py（实盘准入）
  core/       service.py（决策管道、执行、审批、调度）· events.py
  notify/     telegram.py
  api/        app.py（FastAPI，登录、REST、SSE）
  cli.py      serve · setup · doctor · password
frontend/     Vite + React + TypeScript + Tailwind + Framer Motion
```

## License

MIT
