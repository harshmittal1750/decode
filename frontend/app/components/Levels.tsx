"use client";

import clsx from "clsx";
import { useState } from "react";
import { Grid, money, pct, price, SummaryRow, useApi } from "@/lib/api";
import { Async, Card, Note, Table } from "./ui";

export function SummaryByTimeframe() {
  const { data, error, isLoading } = useApi<SummaryRow[]>("/levels/summary");
  return (
    <Card title="Liquidation map by timeframe"
          subtitle="Where each side's mass sits, per lookback window">
      <Async data={data} error={error} isLoading={isLoading}>
        {(rows) => (
          <>
            <Table
              head={["Window", { label: "Spot", align: "r" }, { label: "Book", align: "r" },
                     { label: "Shorts above", align: "r" }, "Where most shorts sit",
                     { label: "Away", align: "r" },
                     { label: "Longs below", align: "r" }, "Where most longs sit",
                     { label: "Away", align: "r" }]}
              rows={rows.map((r) => [
                <span key="w" className="font-medium text-neutral-200">
                  {r.window.toUpperCase()}</span>,
                price(r.spot), money(r.book_usd),
                <span key="sa" className="text-emerald-400">{money(r.skew.above_usd)}</span>,
                r.short_core ? `${price(r.short_core.lo)}–${price(r.short_core.hi)}` : "—",
                <span key="sd" className="text-emerald-400">
                  {pct(r.short_core?.dist_pct)}</span>,
                <span key="lb" className="text-rose-400">{money(r.skew.below_usd)}</span>,
                r.long_core ? `${price(r.long_core.lo)}–${price(r.long_core.hi)}` : "—",
                <span key="ld" className="text-rose-400">{pct(r.long_core?.dist_pct)}</span>,
              ])}
            />
            <Note>
              Longer windows hold more history, so their books are naturally larger — compare
              <em> across a row</em>, not down a column. When several independent windows agree
              on the same core band, that is the strongest signal here.
            </Note>
          </>
        )}
      </Async>
    </Card>
  );
}

export function PriceLadder() {
  const [span, setSpan] = useState(5);
  const [hideEmpty, setHideEmpty] = useState(false);
  const { data, error, isLoading } = useApi<Grid>(`/levels/grid?span=${span}`);

  return (
    <Card
      title="Liquidity by price level"
      subtitle="One shared ladder across timeframes — empty buckets included"
      right={
        <div className="flex items-center gap-2">
          {[3, 5, 8, 15].map((s) => (
            <button key={s} onClick={() => setSpan(s)}
              className={clsx("rounded px-2 py-1 text-xs",
                span === s ? "bg-neutral-200 text-neutral-900"
                           : "bg-neutral-800 text-neutral-400 hover:bg-neutral-700")}>
              ±{s}%
            </button>
          ))}
          <button onClick={() => setHideEmpty(v => !v)}
            className={clsx("rounded px-2 py-1 text-xs",
              hideEmpty ? "bg-neutral-200 text-neutral-900"
                        : "bg-neutral-800 text-neutral-400 hover:bg-neutral-700")}>
            hide empty
          </button>
        </div>
      }>
      <Async data={data} error={error} isLoading={isLoading}>
        {(g) => {
          const peak = Math.max(...g.rows.map(r => r.total), 1);
          const rows = hideEmpty ? g.rows.filter(r => r.total > 0) : g.rows;
          let spotDrawn = false;
          return (
            <>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b border-neutral-800 text-[11px] uppercase
                                   tracking-wider text-neutral-500">
                      <th className="pb-2 pr-3 text-left">Price bucket</th>
                      <th className="pb-2 pr-3 text-right">From spot</th>
                      {g.windows.map(w => (
                        <th key={w} className="pb-2 pr-3 text-right">{w.toUpperCase()}</th>
                      ))}
                      <th className="pb-2 pr-3 text-right">Total</th>
                      <th className="pb-2 text-left w-32"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const crossesSpot = !spotDrawn && r.hi > g.spot;
                      if (crossesSpot) spotDrawn = true;
                      return (
                        <tr key={i} className={clsx(
                          "border-b border-neutral-900/60 last:border-0 hover:bg-neutral-900/40",
                          crossesSpot && "border-t-2 border-t-neutral-300")}>
                          <td className="py-1 pr-3 tabular-nums text-neutral-400">
                            {price(r.lo)}–{price(r.hi - 1)}
                          </td>
                          <td className={clsx("py-1 pr-3 text-right tabular-nums",
                            r.dist_pct >= 0 ? "text-emerald-500/80" : "text-rose-500/80")}>
                            {pct(r.dist_pct)}
                          </td>
                          {g.windows.map(w => (
                            <td key={w} className="py-1 pr-3 text-right tabular-nums
                                                   text-neutral-300">
                              {r.cells[w] ? (r.cells[w] / 1e6).toFixed(0)
                                          : <span className="text-neutral-600">·</span>}
                            </td>
                          ))}
                          <td className="py-1 pr-3 text-right tabular-nums font-medium
                                         text-neutral-200">
                            {r.total ? (r.total / 1e6).toFixed(0) : "·"}
                          </td>
                          <td className="py-1">
                            <div className="h-2 rounded-sm"
                                 style={{
                                   width: `${(r.total / peak) * 100}%`,
                                   background: r.dist_pct >= 0 ? "#10b981" : "#f43f5e",
                                   opacity: 0.55,
                                 }} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Note>
                Figures are $ millions per bucket ({price(g.step)} wide). A
                <span className="mx-1 text-neutral-600">·</span> means nothing there — price
                crosses that level meeting no forced flow, which a list of only-populated
                levels would hide. The white rule marks spot. Columns overlap in time, so
                read across a row rather than summing.
              </Note>
            </>
          );
        }}
      </Async>
    </Card>
  );
}
