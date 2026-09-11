"use client";

import { ago, Status, useApi } from "@/lib/api";
import { Async, Card, Note, Pill, Stat, Table } from "./ui";

interface Run {
  run_id: number; started_at: number; finished_at: number | null;
  ok_count: number; err_count: number; note: string | null;
}
interface Err {
  id: number; run_id: number; stream: string; occurred_at: number;
  kind: string; message: string;
}

export function StatusPanel() {
  const { data, error, isLoading } = useApi<Status>("/status");
  return (
    <Card title="Archive" subtitle="What has been collected, and what needs the session">
      <Async data={data} error={error} isLoading={isLoading}>
        {(s) => {
          const gated = Object.entries(s.per_stream).filter(([, v]) => v.needs_session);
          const stale = Object.entries(s.per_stream)
            .filter(([, v]) => v.latest_fetched_at &&
                               Date.now() - v.latest_fetched_at > 12 * 3600_000);
          return (
            <>
              <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Runs" value={s.runs} hint={ago(s.last_run)} />
                <Stat label="Processed rows" value={s.processed_rows.toLocaleString()} />
                <Stat label="Raw blobs" value={s.raw_rows.toLocaleString()}
                      hint="expire after 14 days" />
                <Stat label="Errors" value={s.errors}
                      tone={s.errors > 0 ? "down" : undefined} />
              </div>
              {stale.length > 0 && (
                <div className="mb-3 rounded-lg border border-amber-900/60 bg-amber-950/30
                                p-2 text-xs text-amber-200">
                  {stale.length} stream(s) not updated in over 12h — is the cron running?
                </div>
              )}
              <Table
                head={["Stream", { label: "Rows", align: "r" }, "Latest", "Needs"]}
                rows={Object.entries(s.per_stream)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([name, v]) => [
                    name, v.rows.toLocaleString(), ago(v.latest_fetched_at),
                    v.needs_session ? <Pill key="g" tone="warn">session</Pill> : "",
                  ])}
                dense
              />
              {gated.length > 0 && (
                <Note>
                  {gated.length} streams need the login session. When it expires they all fail
                  together — <code>decode collect</code> exits non-zero and tells you to run
                  <code className="mx-1">decode login</code>. A single stream failing on its own
                  is transient and is not alerted, because it retries on the next run.
                </Note>
              )}
            </>
          );
        }}
      </Async>
    </Card>
  );
}

export function RunsPanel() {
  const { data, error, isLoading } = useApi<Run[]>("/runs?limit=15");
  return (
    <Card title="Recent runs">
      <Async data={data} error={error} isLoading={isLoading}>
        {(rows) => (
          <Table
            head={[{ label: "Run", align: "r" }, "Started",
                   { label: "OK", align: "r" }, { label: "Failed", align: "r" },
                   { label: "Took", align: "r" }]}
            rows={rows.map((r) => [
              r.run_id, ago(r.started_at),
              <span key="o" className="text-emerald-400">{r.ok_count}</span>,
              r.err_count > 0
                ? <span key="e" className="text-rose-400">{r.err_count}</span>
                : <span key="e" className="text-neutral-600">0</span>,
              r.finished_at ? `${((r.finished_at - r.started_at) / 1000).toFixed(1)}s` : "—",
            ])}
            dense
          />
        )}
      </Async>
    </Card>
  );
}

export function ErrorsPanel() {
  const { data, error, isLoading } = useApi<Err[]>("/errors/recent?limit=15");
  return (
    <Card title="Recent failures" subtitle="Every failure is recorded with its stage">
      <Async data={data} error={error} isLoading={isLoading}>
        {(rows) => rows.length === 0
          ? <p className="text-sm text-emerald-400">No errors recorded.</p>
          : <Table
              head={["When", "Stream", "Kind", "Message"]}
              rows={rows.map((e) => [
                ago(e.occurred_at), e.stream, e.kind,
                <span key="m" className="text-neutral-400">{e.message.slice(0, 90)}</span>,
              ])}
              dense
            />}
      </Async>
    </Card>
  );
}
