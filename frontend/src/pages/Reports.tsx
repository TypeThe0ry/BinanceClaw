import { useState } from "react";
import { post, type Report } from "../api";
import { Md } from "../components/Md";
import { DocLink, Empty, Page } from "../components/ui";
import { useApi, useToast } from "../lib";

export default function Reports() {
  const { data } = useApi<Report[]>("/reports", { kinds: ["report"] });
  const [open, setOpen] = useState<string | null>(null);
  const toast = useToast();
  const sel = data?.find((r) => r.day === (open ?? data?.[0]?.day));
  return (
    <Page>
      <div className="flex items-center justify-between border-b border-line pb-3">
        <h1 className="flex items-center gap-1.5 text-[20px] font-semibold">日报<DocLink to="telegram" /></h1>
        <button className="btn-secondary btn-sm" onClick={async () => { await post("/reports/run"); toast("生成中"); }}>立即生成</button>
      </div>
      {data?.length ? (
        <div className="grid gap-8 pt-6 md:grid-cols-[160px_1fr]">
          <div className="no-scrollbar flex gap-1 overflow-x-auto md:flex-col">
            {data.map((r) => (
              <button key={r.day} onClick={() => setOpen(r.day)}
                className={`num shrink-0 rounded-md px-3 py-1.5 text-left text-[14px] ${sel?.day === r.day ? "bg-hover text-fg" : "text-fg-2 hover:text-fg"}`}>{r.day}</button>
            ))}
          </div>
          <article className="max-w-[720px]">{sel && <Md text={sel.text} />}</article>
        </div>
      ) : <Empty text="暂无日报" />}
    </Page>
  );
}
