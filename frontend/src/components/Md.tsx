import Markdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

/**
 * Markdown renderer. Never renders raw HTML or images (an image URL in model output would make the
 * browser fetch an attacker-chosen address and could leak data). For untrusted text — anything a
 * model wrote — links are shown as inert text with their URL; only bundled docs (`trusted`) get
 * clickable links, and relative *.md links are routed to the in-app docs.
 */
export function Md({ text, compact = false, trusted = false }: { text: string; compact?: boolean; trusted?: boolean }) {
  return (
    <div className={`md ${compact ? "md-compact" : ""}`}>
      <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={["img"]} unwrapDisallowed
        components={{
          a: ({ href = "", children }) => {
            if (!trusted) {
              return <span className="text-fg-2">{children}{href && String(children) !== href ? <span className="text-fg-3"> ({href})</span> : null}</span>;
            }
            const m = /^(?:\.\.\/)?([\w-]+)\.md(#.*)?$/.exec(href);
            if (m) {
              const slug = m[1] === "SECURITY" ? "security" : m[1] === "CHANGELOG" ? "changelog" : m[1] === "README" ? "" : m[1];
              return <Link to={`/docs/${slug}${m[2] ?? ""}`}>{children}</Link>;
            }
            return /^https:\/\//.test(href) ? <a href={href} target="_blank" rel="noreferrer noopener">{children}</a> : <span>{children}</span>;
          },
        }}>
        {text}
      </Markdown>
    </div>
  );
}
