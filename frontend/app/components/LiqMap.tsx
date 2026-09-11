"use client";

import {
  Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Heatmap, Level, money, pct, price, useApi, Window_ } from "@/lib/api";
import { Async, Card, Note, Pill, Stat, Table } from "./ui";

const LONG = "#f43f5e";   // longs liquidate downward -> red
const SHORT = "#10b981";  // shorts liquidate upward  -> green

export function LiqProfile({ window_ }: { window_: Window_ }) {
  const { data, error, isLoading } = useApi<Heatmap>(`/heatmap/${window_}`);
  return (
    <Card title="Liquidation profile"
          subtitle="Standing liquidity at every price level. Bars left of the line are longs."
          right={data && <Pill>{price(data.spot)} spot</Pill>}>
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => {
          const rows = d.levels.map((l) => ({ ...l, usdM: l.usd / 1e6 }));
          return (
            <>
              <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Longs below" value={money(d.skew.below_usd)}
                      hint="forced selling if hit" tone="down" />
                <Stat label="Shorts above" value={money(d.skew.above_usd)}
                      hint="forced buying if hit" tone="up" />
                <Stat label="Skew (per level)"
                      value={d.skew.per_level ? d.skew.per_level.toFixed(2) + "×" : "—"}
                      hint="raw skew minus window geometry" />
                <Stat label="Fuel within ±5%" value={money(d.fuel_within_5pct)}
                      hint="the part that actually churns" />
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
                  <XAxis dataKey="price" tick={{ fontSize: 11, fill: "#737373" }}
                         tickFormatter={(v) => price(v)} minTickGap={40} />
                  <YAxis tick={{ fontSize: 11, fill: "#737373" }}
                         tickFormatter={(v) => `$${v}M`} width={54} />
                  <Tooltip
                    contentStyle={{ background: "#0a0a0a", border: "1px solid #262626",
                                    borderRadius: 8, fontSize: 12 }}
                    labelFormatter={(v) => price(Number(v))}
                    formatter={(v: number, _n, p) => [
                      `${money(v * 1e6)}  (${pct((p.payload as Level).dist_pct)})`,
                      (p.payload as Level).side === "long" ? "Longs" : "Shorts"]} />
                  <ReferenceLine x={d.spot} stroke="#e5e5e5" strokeDasharray="3 3"
                                 label={{ value: "spot", fill: "#e5e5e5", fontSize: 10,
                                          position: "top" }} />
                  <Bar dataKey="usdM" isAnimationActive={false}>
                    {rows.map((r, i) => (
                      <Cell key={i} fill={r.side === "long" ? LONG : SHORT} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <Note>
                Side is inferred, not labelled by the exchange: levels decay once price
                sweeps them, so anything still standing below spot is longs and above is
                shorts. Per-level skew divides out the window being asymmetric around spot —
                raw skew usually overstates the long side.
              </Note>
            </>
          );
        }}
      </Async>
    </Card>
  );
}

export function TopLevels({ window_ }: { window_: Window_ }) {
  const { data, error, isLoading } = useApi<Heatmap>(`/heatmap/${window_}`);
  return (
    <Card title="Largest single levels" subtitle="Individual buckets, biggest first">
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => (
          <Table
            head={["Price", { label: "From spot", align: "r" }, "Side",
                   { label: "Liquidity", align: "r" }]}
            rows={[...d.levels].sort((a, b) => b.usd - a.usd).slice(0, 12).map((l) => [
              price(l.price), pct(l.dist_pct),
              <Pill key="s" tone={l.side}>{l.side}</Pill>, money(l.usd),
            ])}
            dense
          />
        )}
      </Async>
    </Card>
  );
}
