import { useEffect } from "react";
import { NavLink, useLocation, useParams } from "react-router-dom";
import { Md } from "../components/Md";
import { Empty, Page } from "../components/ui";

const files = import.meta.glob("../../../docs/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const security = import.meta.glob("../../../SECURITY.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const changelog = import.meta.glob("../../../CHANGELOG.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

const DOCS: Record<string, string> = {};
for (const [path, text] of Object.entries(files)) DOCS[path.split("/").pop()!.replace(/\.md$/, "")] = text;
for (const text of Object.values(security)) DOCS.security = text;
for (const text of Object.values(changelog)) DOCS.changelog = text;

const ORDER: [string, string][] = [
  ["", "目录"], ["getting-started", "快速开始"], ["dashboard", "界面指南"], ["modes", "模式一览"], ["decisions-and-risk", "AI 决策与风控"], ["capital-and-allocation", "资金与分配"], ["risk-presets", "风险预设"], ["debate", "多空辩论"],
  ["futures", "合约"], ["earn", "理财"], ["performance", "绩效与回测"], ["arena", "模型竞技场"], ["approvals", "审批"], ["live-mode", "模拟盘与实盘"], ["models", "模型"], ["telegram", "Telegram"], ["faq", "常见问题"],
  ["security", "安全"], ["changelog", "更新日志"],
];

export default function Docs() {
  const { slug = "" } = useParams();
  const { hash } = useLocation();
  const text = DOCS[slug || "README"];
  useEffect(() => {
    if (!hash) { window.scrollTo({ top: 0 }); return; }
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView({ block: "start" });
  }, [slug, hash]);
  return (
    <Page className="grid gap-8 md:grid-cols-[180px_1fr]">
      <nav className="no-scrollbar flex gap-1 overflow-x-auto md:sticky md:top-28 md:flex-col md:self-start">
        {ORDER.map(([k, label]) => (
          <NavLink key={k} to={`/docs/${k}`} end
            className={({ isActive }) => `shrink-0 rounded-md px-3 py-1.5 text-[14px] ${isActive || (k === "" && !slug) ? "bg-hover text-fg" : "text-fg-2 hover:text-fg"}`}>
            {label}
          </NavLink>
        ))}
      </nav>
      <article className="min-w-0 max-w-[760px]">{text ? <Md text={text} trusted /> : <Empty text="找不到这篇文档" />}</article>
    </Page>
  );
}
