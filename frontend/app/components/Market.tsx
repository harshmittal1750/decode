"use client";

import { money, useApi } from "@/lib/api";
import { Async, Card, Note, Stat, Table } from "./ui";

interface Funding {
  ts: number; spot: number;
  venues: { venue: string; rate_pct_8h: number | null; annualised_pct: number | null }[];
}
interface LongShort {
  fetched_at: number;
  venues: { venue: string; long: number | null; short: number | null;
            long_usd: number | null; short_usd: number | null }[];
}
interface LiqToday {
  longLiquidationUsd?: number; shortLiquidationUsd?: number; liquidationUsd?: number;
  liquidationTraders?: number; peakLiquidationHour?: string | number;
}

export function FundingPanel() {
  const { data, error, isLoading } = useApi<Funding>("/funding");
  return (
    <Card title="Funding by venue"
          subtitle="Never averaged — venues use different intervals and caps">
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => (
          <>
            <Table
              head={["Venue", { label: "Per 8h", align: "r" },
                     { label: "Annualised", align: "r" }]}
              rows={d.venues.map((v) => [
                v.venue,
                <span key="r" className={(v.rate_pct_8h ?? 0) >= 0 ? "text-emerald-400"
                                                                   : "text-rose-400"}>
                  {v.rate_pct_8h == null ? "—" : `${v.rate_pct_8h.toFixed(4)}%`}</span>,
                v.annualised_pct == null ? "—" : `${v.annualised_pct.toFixed(1)}%`,
              ])}
              dense
            />
            <Note>
              Positive funding means longs pay shorts — the perp trades above spot. Hyperliquid
              funds hourly and Binance every 8h, so these rows are not directly comparable;
              that is why nothing here is averaged into a single number.
            </Note>
          </>
        )}
      </Async>
    </Card>
  );
}

export function PositioningPanel() {
  const { data, error, isLoading } = useApi<LongShort>("/longshort");
  return (
    <Card title="Measured positioning"
          subtitle="Actual long/short split — not inferred from the heatmap">
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => (
          <>
            <Table
              head={["Venue", { label: "Long", align: "r" }, { label: "Short", align: "r" },
                     { label: "Long $", align: "r" }, { label: "Short $", align: "r" }]}
              rows={d.venues.map((v) => [
                v.venue,
                <span key="l" className="text-rose-400">
                  {v.long == null ? "—" : `${v.long.toFixed(2)}%`}</span>,
                <span key="s" className="text-emerald-400">
                  {v.short == null ? "—" : `${v.short.toFixed(2)}%`}</span>,
                money(v.long_usd), money(v.short_usd),
              ])}
              dense
            />
            <Note>
              This is the direct measurement. The liquidation map&apos;s long/short skew is a
              model of where positions would be liquidated — the two answer different
              questions, and a large map skew often sits alongside near-balanced positioning.
            </Note>
          </>
        )}
      </Async>
    </Card>
  );
}

export function RealisedPanel() {
  const { data, error, isLoading } = useApi<LiqToday>("/liqtoday");
  return (
    <Card title="Realised liquidations (24h)"
          subtitle="What actually got liquidated — use it to score the map">
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Total" value={money(d.liquidationUsd)} />
            <Stat label="Longs" value={money(d.longLiquidationUsd)} tone="down" />
            <Stat label="Shorts" value={money(d.shortLiquidationUsd)} tone="up" />
            <Stat label="Traders"
                  value={d.liquidationTraders?.toLocaleString() ?? "—"} />
          </div>
        )}
      </Async>
    </Card>
  );
}
