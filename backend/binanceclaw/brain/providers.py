"""Model providers behind one interface.

* claude_code   — Claude subscription via the Claude Code CLI (``claude -p``), no API key.
                  Runs with **all tools disabled** and no MCP servers; the news step (``research``)
                  enables only WebSearch and WebFetch.
* openai        — OpenAI Chat Completions (API key).
* openai_compat — any OpenAI-compatible endpoint (DeepSeek, Qwen, Ollama, OpenRouter...).
* anthropic_api — Anthropic Messages API (API key).
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

import httpx

from ..config import ProviderSettings, Secrets


@dataclass
class Completion:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0


class ProviderError(RuntimeError):
    pass


Message = dict  # {"role": "user"|"assistant", "content": str}


class Provider:
    name = "base"
    default_model = ""

    def __init__(self, cfg: ProviderSettings, secrets: Secrets):
        self.cfg = cfg
        self.secrets = secrets
        self.model = cfg.model or self.default_model

    def complete(self, system: str, messages: list[Message], json_mode: bool = False) -> Completion:
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        return True, ""

    # 联网新闻: providers that can search the web themselves (see brain/news.py)
    web_search = False

    def research(self, system: str, prompt: str) -> Completion:
        """One call with web search on; the reply is the model's news digest."""
        raise ProviderError(f"{self.name} 不支持联网搜索")

    def with_model(self, model: str) -> Provider:
        """This provider calling ``model`` instead ("" or the same model = this provider)."""
        if not model or model == self.model:
            return self
        p = copy.copy(self)
        p.model = model
        return p


# --------------------------------------------------------------------------- Claude Code
class ClaudeCodeProvider(Provider):
    name = "claude_code"
    default_model = ""   # CLI default for the subscription

    def _binary(self) -> str | None:
        return os.environ.get("CLAUDE_BIN") or shutil.which("claude")

    def available(self):
        b = self._binary()
        if not b:
            return False, "未找到 claude 命令（Claude Code CLI）"
        if not (self.secrets.claude_code_oauth_token or os.path.exists(os.path.expanduser("~/.claude/.credentials.json"))
                or os.path.exists(os.path.expanduser("~/.claude.json"))):
            return False, "Claude Code 未登录：运行 `claude setup-token` 并把 token 写入 CLAUDE_CODE_OAUTH_TOKEN"
        return True, b

    web_search = True
    WEB_TOOLS = "WebSearch,WebFetch"

    def complete(self, system, messages, json_mode=False):
        # Claude Code -p takes a single prompt: flatten multi-turn chat into a transcript.
        if len(messages) == 1:
            prompt = messages[0]["content"]
        else:
            lines = [("用户" if m["role"] == "user" else "助理") + "：" + m["content"] for m in messages[:-1]]
            prompt = "【之前的对话】\n" + "\n\n".join(lines) + "\n\n【当前问题】\n" + messages[-1]["content"]
        return self._run(system, prompt, ["--tools", ""])

    def research(self, system, prompt):
        # Only the two web tools, pre-approved so -p never waits for a permission prompt
        return self._run(system, prompt, ["--tools", self.WEB_TOOLS, "--allowedTools", self.WEB_TOOLS])

    def _run(self, system: str, prompt: str, tools: list[str]) -> Completion:
        ok, b = self.available()
        if not ok:
            raise ProviderError(b)
        cmd = [b, "-p", "--output-format", "json", *tools, "--strict-mcp-config",
               "--no-session-persistence", "--system-prompt", system]
        if self.model:
            cmd += ["--model", self.model]
        # The CLI gets no exchange, Telegram or other provider credentials, even though it runs without tools
        hidden = {f.upper() for f in type(self.secrets).model_fields} - {"CLAUDE_CODE_OAUTH_TOKEN"}
        env = {k: v for k, v in os.environ.items() if k not in hidden}
        if self.secrets.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.secrets.claude_code_oauth_token
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="bclaw-") as cwd:   # empty dir: no project CLAUDE.md
            try:
                p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                   timeout=self.cfg.timeout_seconds, cwd=cwd, env=env)
            except subprocess.TimeoutExpired as e:
                raise ProviderError(f"Claude Code 超时（{self.cfg.timeout_seconds}s）") from e
        out = (p.stdout or "").strip()
        try:
            data = json.loads(out.splitlines()[-1]) if out else {}
        except json.JSONDecodeError:
            data = {}
        if p.returncode != 0 or data.get("is_error") or "result" not in data:
            msg = data.get("result") or (p.stderr or out)[-400:] or f"exit {p.returncode}"
            raise ProviderError(f"Claude Code 调用失败：{msg}")
        u = data.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                 "cost_usd_equiv": data.get("total_cost_usd")}
        model = ",".join((data.get("modelUsage") or {}).keys()) or self.model or "claude-code"
        return Completion(data["result"], model, usage, int((time.time() - t0) * 1000))


# --------------------------------------------------------------------------- OpenAI & compatible
class OpenAIProvider(Provider):
    name = "openai"
    default_model = "gpt-5.5"
    base_url = "https://api.openai.com/v1"

    def _key(self):
        return self.secrets.openai_api_key

    def _base(self):
        return self.base_url

    def available(self):
        return (bool(self._key()), "" if self._key() else "未配置 API Key")

    def complete(self, system, messages, json_mode=False):
        key = self._key()
        if not key and self.name == "openai":
            raise ProviderError("未配置 OPENAI_API_KEY")
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.cfg.temperature is not None and not self.model.startswith(("o", "gpt-5")):
            body["temperature"] = self.cfg.temperature
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        t0 = time.time()
        try:
            r = httpx.post(self._base().rstrip("/") + "/chat/completions", json=body, headers=headers,
                           timeout=self.cfg.timeout_seconds)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} 请求失败：{e}") from e
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        text = d["choices"][0]["message"].get("content") or ""
        return Completion(text, d.get("model", self.model), d.get("usage", {}), int((time.time() - t0) * 1000))


    web_search = True

    def research(self, system, prompt):
        """Responses API with the hosted web search tool."""
        key = self._key()
        if not key:
            raise ProviderError("未配置 OPENAI_API_KEY")
        body = {"model": self.model, "instructions": system, "input": prompt, "tools": [{"type": "web_search"}]}
        t0 = time.time()
        try:
            r = httpx.post(self._base().rstrip("/") + "/responses", json=body, headers={"Authorization": f"Bearer {key}"},
                           timeout=self.cfg.timeout_seconds)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name} 请求失败：{e}") from e
        if r.status_code >= 400:
            raise ProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        text = "".join(c.get("text", "") for o in d.get("output", []) if o.get("type") == "message"
                       for c in o.get("content", []) if c.get("type") == "output_text")
        return Completion(text, d.get("model", self.model), d.get("usage", {}), int((time.time() - t0) * 1000))


class OpenAICompatProvider(OpenAIProvider):
    name = "openai_compat"
    default_model = "deepseek-chat"
    web_search = False      # endpoints differ; news comes from the RSS feeds instead

    def research(self, system, prompt):
        return Provider.research(self, system, prompt)

    def _key(self):
        return self.secrets.openai_compat_api_key

    def _base(self):
        if not self.cfg.base_url:
            raise ProviderError("openai_compat 需要在设置中填写 base_url")
        return self.cfg.base_url

    def available(self):
        return (bool(self.cfg.base_url), "" if self.cfg.base_url else "未填写 base_url")


# --------------------------------------------------------------------------- Anthropic API
class AnthropicAPIProvider(Provider):
    name = "anthropic_api"
    default_model = "claude-opus-5-5"

    def available(self):
        k = self.secrets.anthropic_api_key
        return (bool(k), "" if k else "未配置 ANTHROPIC_API_KEY")

    web_search = True
    URL = "https://api.anthropic.com/v1/messages"

    def _headers(self):
        return {"x-api-key": self.secrets.anthropic_api_key, "anthropic-version": "2023-06-01"}

    def _post(self, body: dict) -> dict:
        try:
            r = httpx.post(self.URL, json=body, headers=self._headers(), timeout=self.cfg.timeout_seconds)
        except httpx.HTTPError as e:
            raise ProviderError(f"Anthropic 请求失败：{e}") from e
        if r.status_code >= 400:
            raise ProviderError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _text(d: dict) -> str:
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")

    def complete(self, system, messages, json_mode=False):
        if not self.secrets.anthropic_api_key:
            raise ProviderError("未配置 ANTHROPIC_API_KEY")
        # The system prompt is the same every round: cache it so repeat rounds start faster and cost less.
        body = {"model": self.model, "max_tokens": 4096, "messages": messages,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
        t0 = time.time()
        d = self._post(body)
        return Completion(self._text(d), d.get("model", self.model), d.get("usage", {}), int((time.time() - t0) * 1000))

    # Models with the dynamic-filtering web search; older ones (and Haiku) get the basic tool.
    _DYNAMIC = re.compile(r"claude-(fable|mythos|opus-(5|4-[678])|sonnet-(5|4-6))")

    def search_tool(self) -> dict:
        kind = "web_search_20260209" if self._DYNAMIC.match(self.model or "") else "web_search_20250305"
        return {"type": kind, "name": "web_search", "max_uses": 10}   # any site: no allowed/blocked domains

    def research(self, system, prompt):
        """Messages API with the server-side web search tool. A long search may pause the turn
        (stop_reason pause_turn): send the partial reply back so the server continues it."""
        if not self.secrets.anthropic_api_key:
            raise ProviderError("未配置 ANTHROPIC_API_KEY")
        messages: list = [{"role": "user", "content": prompt}]
        body = {"model": self.model, "max_tokens": 8000, "system": system, "tools": [self.search_tool()]}
        t0 = time.time()
        usage: dict = {}
        for _ in range(4):
            d = self._post({**body, "messages": messages})
            for k, v in (d.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
            if d.get("stop_reason") != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": d.get("content", [])}]
        return Completion(self._text(d), d.get("model", self.model), usage, int((time.time() - t0) * 1000))


PROVIDERS = {p.name: p for p in (ClaudeCodeProvider, OpenAIProvider, OpenAICompatProvider, AnthropicAPIProvider)}


def make_provider(cfg: ProviderSettings, secrets: Secrets) -> Provider:
    return PROVIDERS[cfg.name](cfg, secrets)
