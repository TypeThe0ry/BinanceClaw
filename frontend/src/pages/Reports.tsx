import { useState } from "react";
import { post, type Report } from "../api";
import { Md } from "../components/Md";
import { Card, Empty, PageTitle } from "../components/ui";
import { useApi, useToast } from "../lib";

export default function Reports() {
  const { data } = useApi<Report[]>("/reports", { kinds: ["report"] });
  const [open, setOpen] = useState<string | null>(null);
  const toast = useToast();
  const sel = data?.find((r) => r.day === (open ?? data?.[0]?.day));
  return (
    <div>
      <PageTitle title="日报" sub="每天定时生成，同时推送到 Telegram。"
        right={<button className="btn-ghost" onClick={async () => { await post("/reports/run"); toast("正在生成，完成后会出现在这里"); }}>立即生成</button>} />
      {data?.length ? (
        <div className="grid gap-5 md:grid-cols-[200px_1fr]">
          <Card className="flex gap-1 overflow-x-auto p-2 md:flex-col">
            {data.map((r) => (
              <button key={r.day} onClick={() => setOpen(r.day)}
                className={`num shrink-0 rounded-lg px-3 py-2 text-left text-[14px] ${sel?.day === r.day ? "bg-white/[0.08] text-ink" : "text-ink-soft hover:text-ink"}`}>
                {r.day}
              </button>
            ))}
          </Card>
          <Card className="p-6">
            <div className="text-[14.5px] leading-[1.75] text-ink">{sel && <Md text={sel.text} />}</div>
          </Card>
        </div>
      ) : <Card><Empty title="还没有日报" hint="每天在设置的时间自动生成，也可以点「立即生成」。" /></Card>}
    </div>
  );
}
