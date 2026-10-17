import type { ReactNode } from "react";

/** Minimal, safe Markdown: **bold**, `code`, # headings, - / 1. lists. No HTML is ever injected. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    out.push(t.startsWith("**")
      ? <strong key={i++} className="font-semibold text-ink">{t.slice(2, -2)}</strong>
      : <code key={i++} className="rounded bg-white/[0.07] px-1 text-[0.92em]">{t.slice(1, -1)}</code>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Md({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  return (
    <div className="space-y-1.5">
      {lines.map((raw, i) => {
        const line = raw.trimEnd();
        if (!line.trim()) return <div key={i} className="h-1" />;
        const h = /^#{1,4}\s+(.*)$/.exec(line);
        if (h) return <p key={i} className="pt-1 font-semibold text-ink">{inline(h[1])}</p>;
        const li = /^\s*([-*•]|\d+[.)])\s+(.*)$/.exec(line);
        if (li) return (
          <div key={i} className="flex gap-2 pl-1">
            <span className="shrink-0 text-ink-mute">{/\d/.test(li[1]) ? li[1] : "·"}</span>
            <span>{inline(li[2])}</span>
          </div>
        );
        return <p key={i}>{inline(line)}</p>;
      })}
    </div>
  );
}

