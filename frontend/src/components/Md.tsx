import Markdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

/** Markdown renderer (no raw HTML). Relative *.md links are routed to the in-app docs. */
export function Md({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className={`md ${compact ? "md-compact" : ""}`}>
      <Markdown remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href = "", children }) => {
            const m = /^(?:\.\.\/)?([\w-]+)\.md(#.*)?$/.exec(href);
            if (m) {
              const slug = m[1] === "SECURITY" ? "security" : m[1] === "README" ? "" : m[1];
              return <Link to={`/docs/${slug}${m[2] ?? ""}`}>{children}</Link>;
            }
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
          },
        }}>
        {text}
      </Markdown>
    </div>
  );
}
