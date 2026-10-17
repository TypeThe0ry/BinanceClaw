"""binanceclaw serve | setup | doctor | password"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import re
import secrets as pysecrets
import stat
import sys
import time
from pathlib import Path

import httpx

from .config import DATA_DIR, Secrets

ENV_FILE = DATA_DIR / ".env"
KEYS = list(Secrets.model_fields)


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(values: dict[str, str], path: Path = ENV_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text("# BinanceClaw secrets — chmod 600, never commit\n"
                       + "".join(f"{k}={v}\n" for k, v in values.items() if v))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    finally:
        os.umask(old)


def load_env_into_process():
    for k, v in read_env().items():
        os.environ.setdefault(k, v)


def _ask_secret(label: str, current: str) -> str:
    hint = "（回车保留已有值）" if current else "（回车跳过）"
    v = getpass.getpass(f"{label}{hint}: ").strip()
    return v or current


# --------------------------------------------------------------------------- setup
def cmd_setup(_args):
    env = read_env()
    up = lambda k: k.upper()  # noqa: E731
    print("BinanceClaw 设置向导。所有输入都不会显示在屏幕上，保存在", ENV_FILE, "（权限 600）。\n")

    # 1. admin password
    from argon2 import PasswordHasher
    if not env.get("ADMIN_PASSWORD_HASH") or input("重设网页登录密码？[y/N] ").lower() == "y":
        while True:
            p1 = getpass.getpass("网页登录密码（至少 10 位）: ")
            if len(p1) < 10:
                print("太短了")
                continue
            if getpass.getpass("再输入一次: ") != p1:
                print("两次不一致")
                continue
            env["ADMIN_PASSWORD_HASH"] = PasswordHasher().hash(p1)
            break
    env.setdefault("SESSION_SECRET", pysecrets.token_hex(32))

    # 2. Binance
    print("\n币安 API Key：只开「读取」和「现货交易」，并绑定 IP 白名单。")
    env[up("binance_api_key")] = _ask_secret("Binance API Key", env.get("BINANCE_API_KEY", ""))
    env[up("binance_api_secret")] = _ask_secret("Binance API Secret", env.get("BINANCE_API_SECRET", ""))

    # 3. models
    print("\n模型（可都跳过，之后在网页设置里切换）：")
    env["CLAUDE_CODE_OAUTH_TOKEN"] = _ask_secret("Claude Code OAuth token（`claude setup-token` 生成；本机已登录可跳过）",
                                                 env.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
    env["OPENAI_API_KEY"] = _ask_secret("OpenAI API Key", env.get("OPENAI_API_KEY", ""))
    env["ANTHROPIC_API_KEY"] = _ask_secret("Anthropic API Key", env.get("ANTHROPIC_API_KEY", ""))
    env["OPENAI_COMPAT_API_KEY"] = _ask_secret("OpenAI 兼容接口 API Key（DeepSeek/通义等）", env.get("OPENAI_COMPAT_API_KEY", ""))

    # 4. Telegram
    if input("\n配置 Telegram 机器人？[y/N] ").lower() == "y":
        tok = _ask_secret("Bot token（@BotFather）", env.get("TELEGRAM_BOT_TOKEN", ""))
        chat = bind_telegram(tok)
        if chat:
            env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"] = tok, str(chat)

    write_env({k: v for k, v in env.items()})
    print("\n✅ 已保存。运行 `binanceclaw doctor` 自检，`binanceclaw serve` 启动。")


def bind_telegram(token: str) -> int | None:
    api = f"https://api.telegram.org/bot{token}"
    me = httpx.get(f"{api}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("token 无效")
        return None
    print(f"机器人 @{me['result']['username']}：请用你自己的 Telegram 给它发送 /start（120 秒内）…")
    deadline, offset = time.time() + 120, None
    while time.time() < deadline:
        r = httpx.post(f"{api}/getUpdates", json={"timeout": 20, **({"offset": offset} if offset else {})}, timeout=40).json()
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            m = u.get("message")
            if m and m["chat"]["type"] == "private":
                c = m["chat"]
                name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
                if input(f"收到 {name} @{c.get('username', '-')}，是你本人吗？[y/N] ").lower() == "y":
                    httpx.post(f"{api}/getUpdates", json={"offset": offset, "timeout": 0}, timeout=20)
                    httpx.post(f"{api}/sendMessage", json={"chat_id": c["id"], "text": "✅ 已绑定 BinanceClaw"}, timeout=20)
                    return c["id"]
    print("没有收到消息")
    return None


def cmd_password(_args):
    from argon2 import PasswordHasher
    env = read_env()
    p1 = getpass.getpass("新的网页登录密码（至少 10 位）: ")
    if len(p1) < 10 or getpass.getpass("再输入一次: ") != p1:
        sys.exit("密码太短或两次不一致")
    env["ADMIN_PASSWORD_HASH"] = PasswordHasher().hash(p1)
    env["SESSION_SECRET"] = pysecrets.token_hex(32)   # also logs out all sessions
    write_env(env)
    print("✅ 已更新，重启服务后生效")


# --------------------------------------------------------------------------- doctor
def cmd_doctor(_args):
    load_env_into_process()
    from .core.service import Service
    ok_all = True

    def row(ok, name, detail=""):
        nonlocal ok_all
        ok_all &= bool(ok) or ok is None
        mark = "✓" if ok else ("·" if ok is None else "✗")
        print(f"  {mark} {name:<18} {detail}")

    print("BinanceClaw doctor\n")
    if ENV_FILE.exists():
        mode = stat.S_IMODE(ENV_FILE.stat().st_mode)
        row(mode & 0o077 == 0, ".env 权限", oct(mode))
    else:
        row(False, ".env", "不存在，请运行 binanceclaw setup")
    svc = Service()
    sec = svc.secrets.configured()
    row(sec["admin_password_hash"], "网页登录密码")
    try:
        p = svc.client.prices(svc.settings.symbols())
        row(len(p) == len(svc.settings.symbols()), "币安行情", ", ".join(f"{k} {v:,.2f}" for k, v in p.items()))
    except Exception as e:
        row(False, "币安行情", str(e))
    if sec["binance_api_key"]:
        try:
            bal = svc.client.balances()
            row(True, "币安账户", f"{len(bal)} 种资产有余额")
        except Exception as e:
            row(False, "币安账户", str(e))
        chk = svc.live_check()
        row(chk["ok"], "实盘准入（Key 权限）", "全部符合" if chk["ok"] else "；".join(chk["blockers"]))
    else:
        row(None, "币安 Key", "未配置（只能用模拟盘）")
    ok, msg = svc.provider.available()
    row(ok, f"模型 {svc.provider.name}", msg if not ok else (svc.provider.model or "默认模型"))
    if sec["telegram_bot_token"]:
        try:
            me = httpx.get(f"https://api.telegram.org/bot{svc.secrets.telegram_bot_token}/getMe", timeout=15).json()
            row(me.get("ok"), "Telegram", "@" + me["result"]["username"] if me.get("ok") else me.get("description", ""))
        except Exception as e:
            row(False, "Telegram", str(e))
    else:
        row(None, "Telegram", "未配置")
    print(f"\n当前模式：{svc.settings.mode}")
    sys.exit(0 if ok_all else 1)


# --------------------------------------------------------------------------- logging
_SECRET_PATTERNS = [
    re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"),                 # Telegram bot token in URLs
    re.compile(r"(?i)(api[_-]?key|secret|token|signature|password)=([^&\s\"']+)"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),                          # OpenAI-style keys
]


class RedactFilter(logging.Filter):
    """Last line of defence: scrub anything that looks like a credential from every log line."""

    def filter(self, record):
        msg = record.getMessage()
        red = msg
        for p in _SECRET_PATTERNS:
            red = p.sub(lambda m: (m.group(1) + "=<redacted>") if m.lastindex else "<redacted>", red)
        if red != msg:
            record.msg, record.args = red, ()
        return True


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(RedactFilter())
    # httpx logs full request URLs at INFO (Telegram puts the bot token in the URL)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- serve
def cmd_serve(args):
    load_env_into_process()
    setup_logging()
    import uvicorn

    from .api.app import create_app
    from .core.service import Service
    from .notify.telegram import TelegramBot

    svc = Service()
    if not svc.secrets.session_secret:
        sys.exit("缺少 SESSION_SECRET：请先运行 binanceclaw setup")
    if svc.secrets.telegram_bot_token and svc.secrets.telegram_chat_id:
        TelegramBot(svc, svc.secrets.telegram_bot_token, svc.secrets.telegram_chat_id).start()
        svc.events.emit("alert", {"text": f"BinanceClaw 已启动（{'模拟盘' if svc.settings.mode == 'paper' else '实盘 🔴'}）"})
    if not args.no_scheduler:
        svc.start_scheduler()
    static = Path(args.static or os.environ.get("BINANCECLAW_STATIC", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    app = create_app(svc, static)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


def main():
    ap = argparse.ArgumentParser(prog="binanceclaw")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default=os.environ.get("BINANCECLAW_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("BINANCECLAW_PORT", "8686")))
    s.add_argument("--static", default=None)
    s.add_argument("--no-scheduler", action="store_true")
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("password").set_defaults(fn=cmd_password)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

