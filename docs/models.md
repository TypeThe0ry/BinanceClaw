# 模型

在设置里选择，保存后从下一轮开始生效。`连通测试` 会发一条最小请求，确认模型可用。

| 提供方 | 凭证 | 说明 |
|---|---|---|
| Claude 订阅 | `CLAUDE_CODE_OAUTH_TOKEN`，或挂载已登录的 `~/.claude` | 通过 Claude Code CLI，消耗订阅额度，不需要 API Key |
| OpenAI | `OPENAI_API_KEY` | 默认模型可以在设置里改 |
| OpenAI 兼容 | `OPENAI_COMPAT_API_KEY` + Base URL | DeepSeek、通义、OpenRouter、Ollama 等 |
| Anthropic API | `ANTHROPIC_API_KEY` | 按 API 计费 |

## Claude 订阅的两种接法

1. 在一台已登录的机器上运行 `claude setup-token`，把生成的 token 在 setup 里填进去。
2. 宿主机已经登录了 Claude Code：把 `~/.claude` 和 `~/.claude.json` 挂载进容器，参考 `docker-compose.claude.yml.example`。

## 安全

- 决策调用不提供任何工具，Claude Code 以 `--tools ""` 加 `--strict-mcp-config` 运行，工作目录为空。
- 对话调用只把账户快照作为数据传入，模型只能回复文字。
