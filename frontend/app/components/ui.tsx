"use client";

import clsx from "clsx";
import { ReactNode } from "react";
import { ApiError } from "@/lib/api";

export function Card({ title, subtitle, right, children, className }: {
  title?: string; subtitle?: string; right?: ReactNode;
  children: ReactNode; className?: string;
}) {
  return (
    <section className={clsx(
      "rounded-xl border border-neutral-800 bg-neutral-950/60 p-4", className)}>
      {(title || right) && (
        <header className="mb-3 flex items-start justify-between gap-4">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-neutral-200">
              {title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-neutral-500">{subtitle}</p>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }: {
  label: string; value: ReactNode; hint?: string;
  tone?: "up" | "down" | "neutral";
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-neutral-500">{label}</div>
      <div className={clsx("mt-1 text-xl font-semibold tabular-nums",
        tone === "up" && "text-emerald-400",
        tone === "down" && "text-rose-400",
        !tone && "text-neutral-100")}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-neutral-500">{hint}</div>}
    </div>
  );
}

/** Every panel handles loading / error / empty the same way, so a stale archive
 *  explains itself ("run: decode collect") instead of rendering a blank box. */
export function Async<T>({ data, error, isLoading, children, empty }: {
  data: T | undefined; error?: ApiError; isLoading: boolean;
  children: (d: T) => ReactNode; empty?: string;
}) {
  if (error) {
    return (
      <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 p-3 text-sm">
        <div className="font-medium text-amber-300">
          {error.status === 0 ? "API unreachable" :
           error.status === 404 ? "Nothing archived yet" :
           error.status === 409 ? "Not enough data yet" : `Error ${error.status}`}
        </div>
        <div className="mt-1 text-amber-200/70">{error.message}</div>
      </div>
    );
  }
  if (isLoading && !data) {
    return <div className="animate-pulse space-y-2">
      {[0, 1, 2].map(i => <div key={i} className="h-4 rounded bg-neutral-800/70" />)}
    </div>;
  }
  if (!data) return <div className="text-sm text-neutral-500">{empty ?? "No data"}</div>;
  return <>{children(data)}</>;
}

export function Table({ head, rows, dense }: {
  head: (string | { label: string; align?: "l" | "r" })[];
  rows: ReactNode[][]; dense?: boolean;
}) {
  const cols = head.map(h => typeof h === "string" ? { label: h, align: "l" as const } : h);
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-neutral-800">
            {cols.map((c, i) => (
              <th key={i} className={clsx(
                "whitespace-nowrap px-3 pb-2 text-[11px] font-medium uppercase",
                "tracking-wider text-neutral-500 first:pl-0 last:pr-0",
                c.align === "r" ? "text-right" : "text-left")}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-neutral-900/70 last:border-0
                                   hover:bg-neutral-900/40">
              {r.map((cell, j) => (
                <td key={j} className={clsx(
                  "whitespace-nowrap px-3 tabular-nums text-neutral-300",
                  "first:pl-0 last:pr-0",
                  dense ? "py-1" : "py-2",
                  cols[j]?.align === "r" ? "text-right" : "text-left")}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pill({ children, tone = "neutral" }: {
  children: ReactNode; tone?: "long" | "short" | "neutral" | "warn";
}) {
  return (
    <span className={clsx(
      "inline-block rounded px-1.5 py-0.5 text-[11px] font-medium",
      tone === "long" && "bg-rose-950/60 text-rose-300",
      tone === "short" && "bg-emerald-950/60 text-emerald-300",
      tone === "warn" && "bg-amber-950/60 text-amber-300",
      tone === "neutral" && "bg-neutral-800 text-neutral-400")}>{children}</span>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="mt-3 text-xs leading-relaxed text-neutral-500">{children}</p>;
}
