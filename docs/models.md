# 模型

在设置里选择，保存后从下一轮开始生效。`连通测试` 会发一条最小请求，确认模型可用。

| 提供方 | 凭证 | 说明 |
|---|---|---|
| Claude 订阅 | `CLAUDE_CODE_OAUTH_TOKEN`，或挂载已登录的 `~/.claude` | 通过 Claude Code CLI，消耗订阅额度，不需要 API Key |
| OpenAI | `OPENAI_API_KEY` | 默认模型可以在设置里改 |
| OpenAI 兼容 | `OPENAI_COMPAT_API_KEY` + Base URL | DeepSeek、通义、OpenRouter、Ollama 等 |
| Anthropic API | `ANTHROPIC_API_KEY` | 按 API 计费 |

## 按任务分配模型

默认开启。不同的调用交给不同档位的模型：

| 任务 | 默认档位 | Claude 订阅 / Anthropic API 默认模型 |
|---|---|---|
| 投资决策（每一轮交易计划） | 决策 | `claude-opus-5-5` |
| 配置提案 | 决策 | `claude-opus-5-5` |
| 对话 | 决策 | `claude-opus-5-5` |
| 日报点评 | 轻量 | `claude-haiku-5-5` |
| 多空辩论（多头、空头、风控角色，见 [多空辩论](debate.md)） | 决策 | `claude-opus-5-5` |
| 新闻搜索（决策前联网搜集新闻，见 [联网新闻](news.md)） | 轻量 | `claude-haiku-5-5` |

- 每个任务都可以改成 `轻量`、`决策` 或 `主模型`（上面「模型」一栏填的那个）。
- 轻量模型和决策模型可以按提供方分别填写，留空就用内置默认。OpenAI 和 OpenAI 兼容没有内置默认，留空时用主模型。
- 关掉「按任务分配模型」后，所有调用都用主模型。升级前已经手动填了模型的，升级后这个开关默认关闭，行为不变。
- 主模型失败时，备用模型按同样的档位挑模型。
- `连通测试` 会把实际用到的每个模型都测一遍。
- 改动模型分配会影响决策，保存后会询问是否立即重跑一轮。

## Claude 订阅的两种接法

1. 在一台已登录的机器上运行 `claude setup-token`，把生成的 token 在 setup 里填进去。
2. 宿主机已经登录了 Claude Code：把 `~/.claude` 和 `~/.claude.json` 挂载进容器，参考 `docker-compose.claude.yml.example`。

## 安全

- 决策调用不提供任何工具，Claude Code 以 `--tools ""` 加 `--strict-mcp-config` 运行，工作目录为空。
- 新闻搜索调用只开 `WebSearch` 和 `WebFetch` 两个工具（`--tools "WebSearch,WebFetch"`），同样没有 MCP、工作目录为空、拿不到交易所和其他密钥；它只返回新闻摘要，不能下单。Anthropic API 和 OpenAI 用各自的服务端联网搜索工具。
- 对话调用只把账户快照作为数据传入，模型只能回复文字。
