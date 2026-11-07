"""Model Markdown -> Telegram HTML, so chat replies in Telegram look like the web 对话 page.

Telegram's HTML mode has bold, italic, strike, code, pre, blockquote and links, but no headings,
tables or text colour. Headings become bold lines, tables become an aligned monospace block (or one
line per row when too wide for a phone), and the web's colours (credibility, buy / sell) become
coloured emoji. Like the web page, links the model writes are shown as text with their URL; only the
source list gets clickable links.
"""
from __future__ import annotations

import html
import re
import unicodedata

MAX = 4000
PRE_WIDTH = 34          # widest monospace table that still fits a phone screen without wrapping

_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_TAG = re.compile(r"<[^>]+>")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def width(s: str) -> int:
    """Display width in a monospace font: CJK and emoji take two cells."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 0 if unicodedata.combining(c) else 1 for c in s)


def _plain(s: str) -> str:
    """Inline Markdown removed (for monospace table cells)."""
    s = _LINK.sub(lambda m: m.group(1), s)
    s = _BOLD.sub(lambda m: m.group(1) or m.group(2), s)
    s = _STRIKE.sub(lambda m: m.group(1), s)
    s = _ITALIC.sub(lambda m: m.group(1) or m.group(2), s)
    return s.replace("`", "")


def inline(s: str) -> str:
    """One line of Markdown as Telegram HTML (input is raw text; escaping happens here)."""
    out, pos = [], 0
    for m in _CODE.finditer(s):
        out.append(_inline_text(s[pos:m.start()]))
        out.append(f"<code>{esc(m.group(1))}</code>")
        pos = m.end()
    out.append(_inline_text(s[pos:]))
    return "".join(out)


def _inline_text(s: str) -> str:
    s = esc(s)
    # same as the web page: a model-written link is inert text followed by its address
    s = _LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", s)
    s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", s)
    return _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def table(rows: list[str]) -> str:
    """A Markdown table: aligned monospace block when it fits a phone, otherwise one block per row."""
    grid = [_cells(r) for r in rows if not _TABLE_SEP.match(r)]
    if not grid:
        return ""
    n = max(len(r) for r in grid)
    grid = [r + [""] * (n - len(r)) for r in grid]
    plain = [[_plain(c) for c in r] for r in grid]
    widths = [max(width(r[i]) for r in plain) for i in range(n)]
    if sum(widths) + 2 * (n - 1) <= PRE_WIDTH:
        def fmt(r):
            return "  ".join(c + " " * (widths[i] - width(c)) for i, c in enumerate(r)).rstrip()
        lines = [fmt(plain[0]), "─" * min(PRE_WIDTH, sum(widths) + 2 * (n - 1))] + [fmt(r) for r in plain[1:]]
        return "<pre>" + esc("\n".join(lines)) + "</pre>"
    head, out = grid[0], []
    for r in grid[1:] or [grid[0]]:
        first = f"<b>{inline(r[0])}</b>" if r[0] else ""
        rest = " · ".join(f"{esc(_plain(head[i]))} {inline(c)}" if head[i] and r is not head else inline(c)
                          for i, c in enumerate(r[1:], 1) if c)
        out.append("\n".join(x for x in (first, rest) if x))
    return "\n\n".join(out)


def md_to_html(md: str) -> str:
    """The model's Markdown reply as Telegram HTML."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        f = _FENCE.match(line)
        if f:
            body = []
            i += 1
            while i < len(lines) and not (lines[i].strip().startswith(f.group(1))):
                body.append(lines[i])
                i += 1
            i += 1
            lang = f' class="language-{f.group(2)}"' if f.group(2) else ""
            out.append(f"<pre><code{lang}>{esc(chr(10).join(body))}</code></pre>" if lang else f"<pre>{esc(chr(10).join(body))}</pre>")
            continue
        if line.strip().startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(table(rows))
            continue
        if _QUOTE.match(line):
            body = []
            while i < len(lines) and _QUOTE.match(lines[i]):
                body.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            inner = md_to_html("\n".join(body)).replace("<blockquote>", "").replace("</blockquote>", "")
            out.append(f"<blockquote>{inner}</blockquote>")
            continue
        i += 1
        if _HR.match(line):
            out.append("──────────")
        elif m := _HEADING.match(line):
            out.append(f"<b>{inline(m.group(1))}</b>")
        elif m := _BULLET.match(line):
            depth = len(m.group(1).replace("\t", "  ")) // 2
            item = m.group(2)
            box = re.match(r"\[( |x|X)\]\s+(.*)", item)
            mark, item = (("☑️" if box.group(1).lower() == "x" else "⬜️"), box.group(2)) if box else ("•" if depth == 0 else "◦", item)
            out.append("    " * depth + f"{mark} {inline(item)}")
        elif m := _ORDERED.match(line):
            depth = len(m.group(1).replace("\t", "  ")) // 2
            out.append("    " * depth + f"{m.group(2)}. {inline(m.group(3))}")
        else:
            out.append(inline(line.strip()) if line.strip() else "")
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def credibility(v: int) -> str:
    """Same thresholds and colours as the web badge (green ≥ 80, amber ≥ 50, red below)."""
    return ("🟢" if v >= 80 else "🟡" if v >= 50 else "🔴") + f" 可信度 {v}%"


def sources_html(sources: list[dict]) -> str:
    lines = ["📎 <b>来源</b>"]
    for s in sources:
        name = esc(str(s.get("name") or ""))
        url = str(s.get("url") or "")
        if url.startswith(("https://", "http://")):
            name = f'<a href="{html.escape(url, quote=True)}">{name}</a>'
        parts = [f"[{s.get('n')}]", name]
        if s.get("title"):
            parts.append(f"《{esc(str(s['title']))}》")
        if s.get("time"):
            parts.append(f"<i>{esc(str(s['time']))}</i>")
        if s.get("credibility") is not None:
            parts.append("· " + credibility(int(s["credibility"])))
        lines.append(" ".join(parts))
    return "\n".join(lines)


def strip(text: str) -> str:
    """Telegram HTML back to plain text (fallback when Telegram rejects the markup)."""
    return html.unescape(_TAG.sub("", text))


def split(text: str, limit: int = MAX) -> list[str]:
    """Split HTML into messages at blank lines / line ends, never inside a <pre> or <blockquote>."""
    if len(text) <= limit:
        return [text]
    blocks, cur, depth = [], [], 0
    for ln in text.split("\n"):
        cur.append(ln)
        depth += ln.count("<pre") + ln.count("<blockquote") - ln.count("</pre>") - ln.count("</blockquote>")
        if depth <= 0:
            blocks.append("\n".join(cur))
            cur, depth = [], 0
    if cur:
        blocks.append("\n".join(cur))
    chunks, buf = [], ""
    for b in blocks:
        if len(b) > limit:                     # one huge block: send it as plain text pieces
            if buf:
                chunks.append(buf)
                buf = ""
            p = esc(strip(b))
            chunks += [p[i:i + limit] for i in range(0, len(p), limit)]
            continue
        if buf and len(buf) + 1 + len(b) > limit:
            chunks.append(buf)
            buf = b
        else:
            buf = f"{buf}\n{b}" if buf else b
    if buf:
        chunks.append(buf)
    return chunks
