"use client";

import clsx from "clsx";
import { useState } from "react";
import { LiqProfile, TopLevels } from "./components/LiqMap";
import { PriceLadder, SummaryByTimeframe } from "./components/Levels";
import { WallsPanel } from "./components/Walls";
import { PressurePanel } from "./components/Pressure";
import { FundingPanel, PositioningPanel, RealisedPanel } from "./components/Market";
import { ErrorsPanel, RunsPanel, StatusPanel } from "./components/Diagnostics";
import { ago, Status, useApi, WindowInfo, Window_ } from "@/lib/api";

const TABS = ["Overview", "Liquidation map", "Price ladder", "Spot vs futures",
              "Market", "Diagnostics"] as const;
type Tab = typeof TABS[number];

export default function Page() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [window_, setWindow] = useState<Window_>("6m");
  const { data: windows } = useApi<WindowInfo[]>("/windows", 300_000);
  const { data: status } = useApi<Status>("/status");

  const needsWindow = tab === "Liquidation map";

  return (
    <main className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-neutral-100">decode</h1>
          <p className="mt-1 text-sm text-neutral-500">
            BTC derivatives archive · liquidation map, positioning, and spot-vs-futures
          </p>
        </div>
        <div className="text-right text-xs text-neutral-500">
          {status && <>
            <div>{status.runs} runs · {status.processed_rows.toLocaleString()} rows</div>
            <div>last collected {ago(status.last_run)}</div>
          </>}
        </div>
      </header>

      <nav className="mb-5 flex flex-wrap items-center gap-2 border-b border-neutral-800 pb-3">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx("rounded-lg px-3 py-1.5 text-sm transition",
              tab === t ? "bg-neutral-100 font-medium text-neutral-900"
                        : "text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200")}>
            {t}
          </button>
        ))}
        {needsWindow && windows && (
          <div className="ml-auto flex flex-wrap items-center gap-1">
            <span className="mr-1 text-xs text-neutral-500">window</span>
            {windows.map((w) => (
              <button key={w.window} onClick={() => setWindow(w.window)}
                title={w.gated ? "needs the login session" : "open, no session needed"}
                className={clsx("rounded px-2 py-1 text-xs",
                  window_ === w.window ? "bg-neutral-200 text-neutral-900"
                    : w.rows === 0 ? "bg-neutral-900 text-neutral-700"
                    : "bg-neutral-800 text-neutral-400 hover:bg-neutral-700")}>
                {w.window.toUpperCase()}{w.gated && <span className="ml-0.5 opacity-60">•</span>}
              </button>
            ))}
          </div>
        )}
      </nav>

      {tab === "Overview" && (
        <div className="space-y-4">
          <SummaryByTimeframe />
          <div className="grid gap-4 xl:grid-cols-2">
            <WallsPanel window_="6m" />
            <div className="space-y-4">
              <RealisedPanel />
              <PositioningPanel />
            </div>
          </div>
        </div>
      )}

      {tab === "Liquidation map" && (
        <div className="space-y-4">
          <LiqProfile window_={window_} />
          <div className="grid gap-4 xl:grid-cols-2">
            <WallsPanel window_={window_} />
            <TopLevels window_={window_} />
          </div>
        </div>
      )}

      {tab === "Price ladder" && (
        <div className="space-y-4">
          <PriceLadder />
          <SummaryByTimeframe />
        </div>
      )}

      {tab === "Spot vs futures" && (
        <div className="space-y-4">
          <PressurePanel />
          <div className="grid gap-4 xl:grid-cols-2">
            <FundingPanel />
            <PositioningPanel />
          </div>
        </div>
      )}

      {tab === "Market" && (
        <div className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-2">
            <FundingPanel />
            <PositioningPanel />
          </div>
          <RealisedPanel />
        </div>
      )}

      {tab === "Diagnostics" && (
        <div className="space-y-4">
          <StatusPanel />
          <div className="grid gap-4 xl:grid-cols-2">
            <RunsPanel />
            <ErrorsPanel />
          </div>
        </div>
      )}

      <footer className="mt-8 border-t border-neutral-800 pt-4 text-xs text-neutral-600">
        Liquidation maps show where a move would <em>accelerate</em>, not whether it happens.
        Measured on this archive, basis has no forecasting power for the next bar.
        Nothing here is trading advice.
      </footer>
    </main>
  );
}
