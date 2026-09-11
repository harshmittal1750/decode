"use client";

import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { money, pct, Pressure as P, useApi } from "@/lib/api";
import { Async, Card, Note, Table } from "./ui";

const DRIVERS: Record<string, { label: string; plain: string; tone: string }> = {
  squeeze: {
    label: "Short squeeze",
    plain: "Shorts were forced to buy back. Liquidations did the buying, not new money.",
    tone: "text-emerald-400",
  },
  fresh_leverage: {
    label: "New leveraged buying",
    plain: "Traders opened new longs on futures. Real demand, but borrowed.",
    tone: "text-emerald-400",
  },
  long_cascade: {
    label: "Long liquidation cascade",
    plain: "Longs were forced to sell. Liquidations did the selling.",
    tone: "text-rose-400",
  },
  fresh_shorting: {
    label: "New leveraged selling",
    plain: "Traders opened new shorts on futures.",
    tone: "text-rose-400",
  },
  spot_led: {
    label: "Spot buying / selling",
    plain: "Cash market moved price. Futures followed rather than led.",
    tone: "text-neutral-300",
  },
};

const ROW_ORDER = ["squeeze", "fresh_leverage", "long_cascade", "fresh_shorting", "spot_led"];

export function PressurePanel() {
  const { data, error, isLoading } = useApi<P>("/pressure");
  return (
    <Card title="What is moving the price?"
          subtitle="Leverage or cash — and if leverage, forced or voluntary">
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => {
          const a = d.attribution;
          if (!a) return <p className="text-sm text-neutral-500">No attribution available.</p>;
          const v = a.verdict;
          const drv = DRIVERS[v.main_driver] ?? DRIVERS.spot_led;
          const forcedUp = a.buckets.squeeze;
          const freshUp = a.buckets.fresh_leverage;
          const total = Object.values(a.buckets).reduce((s, x) => s + Math.abs(x), 0) || 1;

          return (
            <>
              {/* ---- the one-line answer ---- */}
              <div className="mb-5 rounded-lg border border-neutral-800 bg-neutral-900/50 p-4">
                <div className="text-[11px] uppercase tracking-wider text-neutral-500">
                  Over the last {a.days} days
                </div>
                <div className="mt-1.5 text-lg leading-snug text-neutral-100">
                  Price is <strong className={v.led_by === "futures"
                    ? "text-amber-300" : "text-sky-300"}>
                    {v.led_by === "futures" ? "futures-led" : "spot-led"}</strong>
                  {" "}({v.futures_share_pct.toFixed(0)}% of the movement), and the biggest
                  single driver is <strong className={drv.tone}>{drv.label.toLowerCase()}</strong>
                  {" "}at <strong className={drv.tone}>{pct(v.main_driver_move_pct)}</strong>.
                </div>
                <div className="mt-2 text-sm text-neutral-400">{drv.plain}</div>
              </div>

              {/* ---- forced vs voluntary, the actual question ---- */}
              <div className="mb-5 grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-neutral-800 p-3">
                  <div className="text-[11px] uppercase tracking-wider text-neutral-500">
                    Upside came from
                  </div>
                  <div className="mt-2 space-y-1.5 text-sm">
                    <Bar label="Forced buying (squeeze)" value={forcedUp}
                         total={total} tone="#10b981" />
                    <Bar label="Voluntary leveraged buying" value={freshUp}
                         total={total} tone="#0ea5e9" />
                  </div>
                  <div className="mt-2 text-xs text-neutral-500">
                    {money(a.forced_buy_usd)} of shorts actually liquidated
                  </div>
                </div>
                <div className="rounded-lg border border-neutral-800 p-3">
                  <div className="text-[11px] uppercase tracking-wider text-neutral-500">
                    Downside came from
                  </div>
                  <div className="mt-2 space-y-1.5 text-sm">
                    <Bar label="Forced selling (cascade)" value={a.buckets.long_cascade}
                         total={total} tone="#f43f5e" />
                    <Bar label="Voluntary leveraged selling" value={a.buckets.fresh_shorting}
                         total={total} tone="#fb923c" />
                  </div>
                  <div className="mt-2 text-xs text-neutral-500">
                    {money(a.forced_sell_usd)} of longs actually liquidated
                  </div>
                </div>
              </div>

              <Table
                head={["What happened", { label: "Days", align: "r" },
                       { label: "Price move", align: "r" }, "Meaning"]}
                rows={ROW_ORDER.filter(k => a.counts[k] > 0).map((k) => {
                  const info = DRIVERS[k];
                  return [
                    <span key="l" className={info.tone}>{info.label}</span>,
                    a.counts[k],
                    <span key="v" className={a.buckets[k] >= 0
                      ? "text-emerald-400" : "text-rose-400"}>{pct(a.buckets[k])}</span>,
                    <span key="p" className="text-neutral-500">{info.plain}</span>,
                  ];
                })}
                dense
              />

              <details className="mt-5">
                <summary className="cursor-pointer text-xs uppercase tracking-wider
                                    text-neutral-500 hover:text-neutral-300">
                  Show the underlying basis measurement
                </summary>
                <div className="mt-3">
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={d.series.map(s => ({ ...s,
                      t: new Date(s.ts).toLocaleDateString(undefined,
                                  { month: "short", day: "numeric" }) }))}
                      margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                      <CartesianGrid stroke="#1f1f1f" vertical={false} />
                      <XAxis dataKey="t" tick={{ fontSize: 11, fill: "#737373" }}
                             minTickGap={40} />
                      <YAxis tick={{ fontSize: 11, fill: "#737373" }} width={58}
                             tickFormatter={(x) => `${x.toFixed(2)}%`} />
                      <Tooltip contentStyle={{ background: "#0a0a0a",
                        border: "1px solid #262626", borderRadius: 8, fontSize: 12 }}
                        formatter={(x: number) => [`${x.toFixed(4)}%`, "Basis"]} />
                      <ReferenceLine y={d.basis_mean} stroke="#525252" strokeDasharray="3 3" />
                      <Line dataKey="basis_pct" stroke="#38bdf8" dot={false}
                            strokeWidth={1.5} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                  <Note>
                    Basis is the perp price minus spot. It rising while price rises means
                    futures are pulling the market; falling means spot is. Current{" "}
                    {d.basis_now.toFixed(4)}% vs a 180-day mean of {d.basis_mean.toFixed(4)}%
                    (z = {d.basis_z.toFixed(2)}). Correlation of next return against basis is{" "}
                    {d.corr_next_basis.toFixed(3)} — near zero, so none of this forecasts.
                  </Note>
                </div>
              </details>

              <Note>
                Attribution is daily because realised liquidations are only published daily.
                A day counts as forced when over half its liquidation volume ran in the same
                direction as the price move.
                {a.days_unattributable > 0 &&
                  ` ${a.days_unattributable} day(s) had no liquidation data and were left out
                    rather than guessed.`}
              </Note>
            </>
          );
        }}
      </Async>
    </Card>
  );
}

function Bar({ label, value, total, tone }: {
  label: string; value: number; total: number; tone: string;
}) {
  const w = Math.min(100, (Math.abs(value) / total) * 100);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-neutral-300">{label}</span>
        <span className="tabular-nums font-medium" style={{ color: tone }}>{pct(value)}</span>
      </div>
      <div className="mt-1 h-1.5 rounded-sm bg-neutral-800">
        <div className="h-full rounded-sm" style={{ width: `${w}%`, background: tone }} />
      </div>
    </div>
  );
}
