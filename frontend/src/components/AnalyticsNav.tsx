import { NavLink } from "react-router-dom";

/** Page title that doubles as the switch between 绩效 and 回测. */
export function AnalyticsNav() {
  const cls = ({ isActive }: { isActive: boolean }) => `text-[20px] font-semibold transition-colors ${isActive ? "text-fg" : "text-fg-3 hover:text-fg-2"}`;
  return (
    <nav className="flex items-center gap-5">
      <NavLink to="/analytics" end className={cls}>绩效</NavLink>
      <NavLink to="/analytics/backtest" className={cls}>回测</NavLink>
    </nav>
  );
}
