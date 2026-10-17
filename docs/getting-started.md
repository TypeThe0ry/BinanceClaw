# 快速开始

## 安装

```bash
git clone https://github.com/TypeThe0ry/BinanceClaw.git && cd BinanceClaw
docker compose build
docker compose run --rm -it binanceclaw setup
docker compose run --rm binanceclaw doctor
docker compose up -d
```

打开 `http://127.0.0.1:8686`，用 setup 里设置的密码登录。

## 设置向导做什么

`binanceclaw setup` 依次询问以下内容。所有输入都不回显，保存到 `data/.env`，权限为 600：

1. 网页登录密码，至少 10 位，以 argon2 哈希保存。
2. 币安 API Key 和 Secret。可以跳过，跳过后只能用模拟盘。
3. 模型凭证：Claude Code token、OpenAI、Anthropic、兼容接口，都可以跳过。
4. Telegram 机器人。会让你给机器人发 `/start` 来绑定你的账号。

已有的值直接按回车保留。只改密码用 `binanceclaw password`。

## 自检

`binanceclaw doctor` 逐项检查以下内容，全部 ✓ 才说明可以开启实盘：

- `.env` 权限
- 登录密码
- 币安行情
- 币安账户
- 实盘准入
- 模型
- Telegram

## 远程访问

默认只监听本机。推荐用 Tailscale：

```bash
tailscale serve --bg --tcp=8686 tcp://127.0.0.1:8686
```

也可以在 `docker-compose.override.yml` 里加一个局域网地址，参考 `docker-compose.claude.yml.example`。
