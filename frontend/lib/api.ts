import useSWR from "swr";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8787";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function fetcher(path: string) {
  let res: Response;
  try {
    res = await fetch(API + path);
  } catch {
    // A network-level failure (server down, wrong port, CORS) surfaces in the
    // browser only as "Failed to fetch" with no clue which URL was tried.
    // Name it, so the next person does not have to run lsof.
    throw new ApiError(0,
      `Cannot reach the API at ${API}. Start it with \`uv run decode serve\`, ` +
      `and make sure NEXT_PUBLIC_API_URL in frontend/.env.local matches the ` +
      `--port it prints.`);
  }
  if (!res.ok) {
    // FastAPI puts the useful message in `detail` — surface it instead of
    // "500", so an empty archive says "run: decode collect" in the UI.
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

/** Poll every 60s: the collector runs on a 6h cron, so anything faster is waste. */
export function useApi<T>(path: string | null, refreshMs = 60_000) {
  const { data, error, isLoading, mutate } = useSWR<T>(path, fetcher, {
    refreshInterval: refreshMs,
    revalidateOnFocus: true,
    keepPreviousData: true,
  });
  return { data, error: error as ApiError | undefined, isLoading, mutate };
}

// ---- shared types (mirror the API's JSON shapes) ----------------------------

export type Window_ = "12h" | "24h" | "48h" | "3d" | "1w" | "2w" | "1m" | "3m" | "6m";

export interface WindowInfo {
  window: Window_;
  gated: boolean;
  rows: number;
  in_levels_view: boolean;
}

export interface Level {
  price: number;
  usd: number;
  side: "long" | "short";
  dist_pct: number;
}

export interface Heatmap {
  window: string;
  spot: number;
  fetched_at: number;
  run_id: number;
  skew: { raw: number | null; per_level: number | null; below_usd: number; above_usd: number };
  fuel_within_5pct: number;
  levels: Level[];
}

export interface Zone {
  lo: number; hi: number; usd: number; n: number;
  core_lo: number; core_hi: number; core_usd: number; core_n: number;
  side: "long" | "short" | "straddles";
  dist_pct: number; pct_of_book: number;
  cum_usd?: number; gap_pct?: number | null;
}

export interface Walls {
  window: string; spot: number; fetched_at: number;
  short: { total_usd: number; forces: string; biggest: Zone | null; ladder: Zone[] };
  long: { total_usd: number; forces: string; biggest: Zone | null; ladder: Zone[] };
}

export interface SummaryRow {
  window: Window_;
  spot: number;
  book_usd: number;
  n_levels: number;
  fuel_within_5pct: number;
  skew: { raw: number | null; per_level: number | null; below_usd: number; above_usd: number };
  short_core: { lo: number; hi: number; usd: number; dist_pct: number } | null;
  long_core: { lo: number; hi: number; usd: number; dist_pct: number } | null;
}

export interface GridRow {
  lo: number; hi: number; total: number; dist_pct: number;
  cells: Record<string, number>;
}

export interface Grid {
  spot: number; step: number; windows: string[]; rows: GridRow[];
}

export interface Attribution {
  days: number;
  days_unattributable: number;
  buckets: Record<string, number>;
  counts: Record<string, number>;
  net_futures_led: number;
  net_spot_led: number;
  forced_buy_usd: number;
  forced_sell_usd: number;
  verdict: {
    led_by: "futures" | "spot";
    futures_share_pct: number;
    main_driver: string;
    main_driver_label: string;
    main_driver_move_pct: number;
  };
}

export interface Pressure {
  n: number;
  basis_now: number; basis_mean: number; basis_z: number;
  corr_ret_dbasis: number; corr_next_basis: number;
  quadrants: Record<string, { n: number; cum: number; avg: number }>;
  net_futures_led: number; net_spot_led: number;
  series: { ts: number; spot: number; perp: number; basis_pct: number }[];
  attribution?: Attribution;
}

export interface Status {
  runs: number; processed_rows: number; raw_rows: number; errors: number;
  streams: string[]; first_run: number; last_run: number;
  per_stream: Record<string, { rows: number; latest_fetched_at: number; needs_session: boolean }>;
}

// ---- formatting (mirrors src/decode/fmt.py so CLI and UI agree) -------------

export function money(x: number | null | undefined, dp?: number): string {
  if (x == null) return "—";
  const a = Math.abs(x), sign = x < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(dp ?? 2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(dp ?? 0)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(dp ?? 1)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

export function price(x: number | null | undefined): string {
  return x == null ? "—" : "$" + Math.round(x).toLocaleString("en-US");
}

export function pct(x: number | null | undefined, dp = 1): string {
  return x == null ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(dp)}%`;
}

export function ago(ms: number | null | undefined): string {
  if (!ms) return "—";
  const s = (Date.now() - ms) / 1000;
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
