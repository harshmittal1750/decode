"use client";

import { money, pct, price, useApi, Walls as W, Window_ } from "@/lib/api";
import { Async, Card, Note, Pill, Stat, Table } from "./ui";

function Side({ side, d }: { side: "short" | "long"; d: W }) {
  const s = d[side];
  const up = side === "short";
  const big = s.biggest;
  return (
    <div>
      <div className="mb-2 flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-neutral-200">
          {up ? "Price rises into SHORTS" : "Price falls into LONGS"}
        </h3>
        <Pill tone={side}>forced {s.forces}</Pill>
      </div>
      <div className="mb-3 grid grid-cols-2 gap-3">
        <Stat label={`Total ${side} liquidity`} value={money(s.total_usd)}
              tone={up ? "up" : "down"} />
        <Stat label="Where most of it sits"
              value={big ? `${price(big.core_lo)}–${price(big.core_hi)}` : "—"}
              hint={big ? `${money(big.core_usd)} · ${pct(big.dist_pct)} away` : undefined} />
      </div>
      <Table
        head={[{ label: "Reach", align: "r" }, "Core (half the mass)",
               { label: "In core", align: "r" }, { label: "In zone", align: "r" },
               { label: "Cumulative", align: "r" }]}
        rows={s.ladder.slice(0, 7).map((z) => [
          <span key="d" className={up ? "text-emerald-400" : "text-rose-400"}>
            {pct(z.dist_pct)}
          </span>,
          z.core_n === 1 ? price(z.core_lo) : `${price(z.core_lo)}–${price(z.core_hi)}`,
          money(z.core_usd), money(z.usd), money(z.cum_usd),
        ])}
        dense
      />
    </div>
  );
}

export function WallsPanel({ window_ }: { window_: Window_ }) {
  const { data, error, isLoading } = useApi<W>(`/walls/${window_}`);
  return (
    <Card title="What price runs into"
          subtitle="Zones ordered by how soon price reaches them, with running totals">
      <Async data={data} error={error} isLoading={isLoading}>
        {(d) => (
          <>
            <div className="grid gap-6 lg:grid-cols-2">
              <Side side="short" d={d} />
              <Side side="long" d={d} />
            </div>
            <Note>
              &quot;Core&quot; is the narrowest band holding half a zone&apos;s mass. A densely
              populated book cannot be split on gaps — one peak with a long tail merges into a
              single very wide zone — so the core is what to read, and the zone total is the
              full support around it. Cumulative is everything crossed to get there.
              This marks where a move would <em>accelerate</em>; it does not predict one.
            </Note>
          </>
        )}
      </Async>
    </Card>
  );
}
