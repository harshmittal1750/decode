"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Status = {
  runs: number;
  processed_rows: number;
  raw_rows: number;
  errors: number;
  streams: string[];
  last_run: number;
  per_stream: Record<string, { rows: number; latest_fetched_at: number }>;
};

type Heatmap = {
  spot: number;
  range: [number, number];
  levels: [number, number][];
};

type LiqTarget = {
  target: number;
  side: string;
  cumulative: number;
  in_range: boolean;
  at_level: { price: number; value: number };
  top_clusters: [number, number][];
};

type Liq = {
  spot: number;
  range: [number, number];
  n_levels: number;
  skew: { raw: number | null; per_level: number | null; below_usd: number; above_usd: number };
  fuel_5pct: number;
  targets: LiqTarget[];
};

type Pressure = {
  n: number;
  basis_now: number;
  basis_mean: number;
  basis_z: number;
  corr_ret_dbasis: number;
  corr_next_basis: number;
  quadrants: Record<string, { n: number; cum: number; avg: number }>;
  net_futures_led: number;
  net_spot_led: number;
};

type LongShort = {
  venues: Record<string, { long: number; short: number; long_usd: number; short_usd: number }>;
};

function useApi<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    fetch(`${API}${path}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
        return r.json();
      })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message ?? e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error };
}

const usd = (n: number) => `$${(n / 1e9).toFixed(2)}B`;
const fmtTs = (ms: number) => (ms ? new Date(ms).toLocaleString() : "-");

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-black/10 dark:border-white/15 p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 mb-3">
        {title}
      </h2>
      {children}
    </section>
  );
}

function ErrorNote({ error }: { error: string }) {
  return <p className="text-sm text-amber-600 dark:text-amber-400">{error}</p>;
}

export default function Dashboard() {
  const { data: status, error: statusErr } = useApi<Status>("/status");
  const { data: pressure, error: pressureErr } = useApi<Pressure>("/pressure");
  const { data: longshort, error: longshortErr } = useApi<LongShort>("/longshort");
  const { data: heatmap, error: heatmapErr } = useApi<Heatmap>("/heatmap");

  const [targetLow, setTargetLow] = useState("");
  const [targetHigh, setTargetHigh] = useState("");
  const [targetsQuery, setTargetsQuery] = useState("");

  // seed sane default targets (+/-5%) once we know spot -- one-time init from
  // async data, not a sync-on-every-render loop, hence the rule override
  useEffect(() => {
    if (!heatmap || targetsQuery) return;
    const lo = Math.round(heatmap.spot * 0.95);
    const hi = Math.round(heatmap.spot * 1.05);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTargetLow(String(lo));
    setTargetHigh(String(hi));
    setTargetsQuery(`${lo},${hi}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heatmap]);

  const { data: liq, error: liqErr } = useApi<Liq>(
    targetsQuery ? `/liq?targets=${targetsQuery}` : "",
    [targetsQuery]
  );

  const chartData =
    heatmap?.levels.map(([price, value]) => ({
      price,
      value,
      side: price < heatmap.spot ? "long" : "short",
    })) ?? [];

  return (
    <div className="flex-1 bg-zinc-50 dark:bg-black px-6 py-8">
      <div className="mx-auto max-w-5xl flex flex-col gap-6">
        <h1 className="text-2xl font-semibold">decode</h1>

        <Card title="Archive status">
          {statusErr && <ErrorNote error={statusErr} />}
          {status && (
            <div className="text-sm space-y-1">
              <p>
                {status.runs} runs &middot; {status.processed_rows} processed rows &middot;{" "}
                {status.errors} errors &middot; last run {fmtTs(status.last_run)}
              </p>
              <table className="w-full mt-2 text-left">
                <thead className="text-zinc-500">
                  <tr>
                    <th className="font-normal">stream</th>
                    <th className="font-normal">rows</th>
                    <th className="font-normal">latest</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(status.per_stream).map(([name, s]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{s.rows}</td>
                      <td>{fmtTs(s.latest_fetched_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Liquidation heatmap">
          {heatmapErr && <ErrorNote error={heatmapErr} />}
          {heatmap && (
            <>
              <p className="text-sm mb-3">
                spot ${heatmap.spot.toLocaleString()} &middot; standing levels $
                {(liq?.range ?? heatmap.range)[0].toLocaleString()}-$
                {(liq?.range ?? heatmap.range)[1].toLocaleString()}
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
                  <XAxis dataKey="price" tick={false} />
                  <YAxis tickFormatter={(v) => `$${(v / 1e6).toFixed(0)}M`} width={56} />
                  <Tooltip
                    formatter={(v: number) => [`$${(v / 1e6).toFixed(1)}M`, "standing"]}
                    labelFormatter={(p: number) => `$${p.toLocaleString()}`}
                  />
                  <ReferenceLine x={heatmap.spot} stroke="#888" strokeDasharray="4 4" />
                  <Bar dataKey="value">
                    {chartData.map((d, i) => (
                      <Cell key={i} fill={d.side === "long" ? "#22c55e" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>

              <div className="flex items-end gap-2 mt-4 text-sm">
                <label className="flex flex-col">
                  low target
                  <input
                    className="border rounded px-2 py-1 w-28 dark:bg-zinc-900"
                    value={targetLow}
                    onChange={(e) => setTargetLow(e.target.value)}
                  />
                </label>
                <label className="flex flex-col">
                  high target
                  <input
                    className="border rounded px-2 py-1 w-28 dark:bg-zinc-900"
                    value={targetHigh}
                    onChange={(e) => setTargetHigh(e.target.value)}
                  />
                </label>
                <button
                  className="rounded bg-foreground text-background px-3 py-1"
                  onClick={() => setTargetsQuery(`${targetLow},${targetHigh}`)}
                >
                  update
                </button>
              </div>

              {liqErr && <ErrorNote error={liqErr} />}
              {liq && (
                <div className="mt-3 text-sm space-y-2">
                  <p>
                    longs (below spot) {usd(liq.skew.below_usd)} &middot; shorts (above spot){" "}
                    {usd(liq.skew.above_usd)} &middot; fuel &plusmn;5% {usd(liq.fuel_5pct)}
                  </p>
                  {liq.targets.map((t) => (
                    <p key={t.target}>
                      ${t.target.toLocaleString()} ({t.side}s){!t.in_range && " [outside range]"}
                      : cumulative {usd(t.cumulative)}
                    </p>
                  ))}
                </div>
              )}
            </>
          )}
        </Card>

        <Card title="Spot vs futures pressure">
          {pressureErr && <ErrorNote error={pressureErr} />}
          {pressure && (
            <div className="text-sm space-y-2">
              <p>
                n={pressure.n} bars &middot; basis now {pressure.basis_now.toFixed(4)}% &middot;
                z={pressure.basis_z.toFixed(2)}
              </p>
              <table className="w-full text-left">
                <thead className="text-zinc-500">
                  <tr>
                    <th className="font-normal">quadrant</th>
                    <th className="font-normal">n</th>
                    <th className="font-normal">avg</th>
                    <th className="font-normal">cumulative</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(pressure.quadrants).map(([label, q]) => (
                    <tr key={label}>
                      <td>{label.trim()}</td>
                      <td>{q.n}</td>
                      <td>{q.avg.toFixed(3)}%</td>
                      <td>{q.cum.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>
                NET futures-led {pressure.net_futures_led.toFixed(1)}% &middot; NET spot-led{" "}
                {pressure.net_spot_led.toFixed(1)}%
              </p>
            </div>
          )}
        </Card>

        <Card title="Long / short positioning">
          {longshortErr && <ErrorNote error={longshortErr} />}
          {longshort && (
            <table className="w-full text-left text-sm">
              <thead className="text-zinc-500">
                <tr>
                  <th className="font-normal">venue</th>
                  <th className="font-normal">long %</th>
                  <th className="font-normal">short %</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(longshort.venues).map(([venue, v]) => (
                  <tr key={venue}>
                    <td>{venue}</td>
                    <td>{v.long?.toFixed(2)}</td>
                    <td>{v.short?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
