import { MagnifyingGlass } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

type PagePath = "/settings" | "/config";

/**
 * Sections of each page with extra search words, so a search on one page also finds what lives on the other.
 * The current page is searched from what is actually rendered (section titles and row labels), so a section
 * added without an entry here still shows up in its own page's category bar and search.
 */
const INDEX: Record<PagePath, { title: string; words: string }[]> = {
  "/settings": [
    { title: "交易模式", words: "实盘 模拟盘 开启实盘 API 权限 提现 重置" },
    { title: "AI 模型", words: "提供方 模型 备用模型 Base URL 连通测试 按任务分配模型 Claude OpenAI Anthropic" },
    { title: "多空辩论", words: "辩论 多头 空头 风控 决策模型 角色 轮数 共识" },
    { title: "节奏", words: "决策方式 自主实时 固定间隔 复查间隔 最短间隔 最长不决策 调用上限 波动唤醒 审批有效期 日报时间" },
    { title: "理财", words: "活期 申购 赎回 稳定币 现货保留 最小划转 Simple Earn" },
    { title: "Telegram 推送", words: "推送频率 即时 定时汇总 免打扰 通知 消息" },
    { title: "外观", words: "主题 浅色 深色 跟随系统" },
    { title: "连接", words: "币安 API Telegram token key OpenAI Anthropic 已配置" },
    { title: "已登录设备", words: "设备 登录 撤销" },
  ],
  "/config": [
    { title: "风险预设", words: "保守 均衡 积极 自定义" },
    { title: "交易风格与观点", words: "交易风格 短线 中期 长期 观点 看法" },
    { title: "资产与分配", words: "分配方式 AI 自主分配 手动分配 可交易范围 白名单 黑名单 再平衡 偏离阈值 单币占比" },
    { title: "目标配置", words: "目标 比例 区间 分类 投资备注" },
    { title: "资金管理", words: "预算 投入 锁定 资金" },
    { title: "风控", words: "单笔上限 自动执行 每日笔数 每日金额 稳定币下限 BTC 暂停线 允许卖出 限额" },
    { title: "免审批", words: "免审批模式 审批 卖出 合约开仓" },
    { title: "AI 提问", words: "提问 回答期限 超时" },
    { title: "合约", words: "永续 杠杆 保证金 止损 做空 自动开仓" },
  ],
};
const OTHER: Record<PagePath, { path: PagePath; label: string }> = {
  "/settings": { path: "/config", label: "配置" },
  "/config": { path: "/settings", label: "设置" },
};

interface Hit { key: string; label: string; where: string; go: () => void }

const titleOf = (s: Element) => s.querySelector("h2")?.textContent?.trim() ?? "";
const norm = (s: string) => s.toLowerCase().replace(/\s+/g, "");

function flash(el: HTMLElement) {
  el.scrollIntoView?.({ behavior: "smooth", block: "start" });
  el.animate?.([{ backgroundColor: "rgb(var(--c-hover))" }, { backgroundColor: "transparent" }], { duration: 1600 });
}

/** Sticky bar under the 设置 / 配置 title: search box plus one link per section of the current page. */
export function SettingsJump({ current }: { current: PagePath }) {
  const ref = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  const loc = useLocation();
  const [sections, setSections] = useState<HTMLElement[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const jumped = useRef("");

  // The page's sections are this bar's siblings (SettingsNav renders it straight into the page);
  // rescan when they change (data loads, a section toggles).
  useEffect(() => {
    const host = ref.current?.parentElement;
    if (!host) return;
    const scan = () => {
      const list = (Array.from(host.children) as HTMLElement[]).filter((s) => s.tagName === "SECTION" && titleOf(s));
      list.forEach((s) => { if (!s.id) s.id = `s-${titleOf(s)}`; s.style.scrollMarginTop = "150px"; });
      setSections((prev) => (prev.length === list.length && prev.every((p, i) => p === list[i]) ? prev : list));
    };
    scan();
    const mo = new MutationObserver(scan);
    mo.observe(host, { childList: true });
    return () => mo.disconnect();
  }, []);

  // Arriving with #<section id or title> (from the other page's search, or a link like /config#futures).
  useEffect(() => {
    const h = decodeURIComponent(loc.hash.slice(1));
    if (!h || jumped.current === h) return;
    const el = sections.find((s) => s.id === h || titleOf(s) === h);
    if (el) { jumped.current = h; flash(el); }
  }, [loc.hash, sections]);

  const hits: Hit[] = [];
  const nq = norm(q);
  if (nq) {
    for (const s of sections) {
      const t = titleOf(s);
      if (norm(t).includes(nq)) hits.push({ key: s.id, label: t, where: "", go: () => flash(s) });
      s.querySelectorAll<HTMLElement>("[data-setting]").forEach((row, i) => {
        const label = row.firstElementChild?.textContent?.trim() ?? "";
        if (label && norm(label).includes(nq)) hits.push({ key: `${s.id}-${i}`, label, where: t, go: () => flash(row) });
      });
    }
    const other = OTHER[current];
    for (const e of INDEX[other.path]) {
      if (norm(e.title).includes(nq) || norm(e.words).includes(nq)) {
        hits.push({ key: `${other.path}-${e.title}`, label: e.title, where: other.label, go: () => nav(`${other.path}#${encodeURIComponent(e.title)}`) });
      }
    }
  }
  const shown = hits.slice(0, 8);

  function pick(h: Hit) { h.go(); setQ(""); setOpen(false); }
  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && shown[0]) { e.preventDefault(); pick(shown[0]); }
    if (e.key === "Escape") { setQ(""); setOpen(false); }
  }

  return (
    <div ref={ref} className="sticky top-[92px] z-30 -mx-1 -mt-6 bg-bg/95 px-1 pb-2 pt-2 backdrop-blur">
      <div className="relative">
        <label className="flex h-9 items-center gap-2 rounded-lg bg-field px-3">
          <MagnifyingGlass size={15} className="text-fg-3" />
          <input className="h-9 flex-1 bg-transparent text-[14px] focus:outline-none" placeholder="搜索设置项，例如 杠杆、免打扰"
            aria-label="搜索设置" value={q} onKeyDown={onKey} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
            onChange={(e) => { setQ(e.target.value); setOpen(true); }} />
        </label>
        {open && nq && (
          <ul role="listbox" aria-label="搜索结果" className="absolute inset-x-0 top-11 overflow-hidden rounded-lg border border-line-strong bg-bg shadow-lg">
            {shown.length ? shown.map((h) => (
              <li key={h.key}>
                <button role="option" aria-selected={false} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(h)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[14px] hover:bg-hover">
                  <span>{h.label}</span>{h.where && <span className="text-[12px] text-fg-3">{h.where}</span>}
                </button>
              </li>
            )) : <li className="px-3 py-2 text-[13px] text-fg-3">没有找到</li>}
          </ul>
        )}
      </div>
      <nav aria-label="分类" className="no-scrollbar mt-2 flex gap-1.5 overflow-x-auto">
        {sections.map((s) => (
          <button key={s.id} onClick={() => flash(s)}
            className="shrink-0 rounded-md border border-line-strong px-2.5 py-1 text-[13px] text-fg-2 transition-colors hover:text-fg">
            {titleOf(s)}
          </button>
        ))}
      </nav>
    </div>
  );
}
