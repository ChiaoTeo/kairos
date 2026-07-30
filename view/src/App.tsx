import { useEffect, useState } from "react";
import { Activity, ChevronLeft, ChevronRight, Database, ListTree, Search, WalletCards } from "lucide-react";
import { loadInstances, loadTimeline } from "./api";
import { MarketChart } from "./MarketChart";
import type { JsonValue, TimelineData, TimelineInstanceIndex, TimelineInstanceRow } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function useTimelineData() {
  const [instanceIndex, setInstanceIndex] = useState<TimelineInstanceIndex | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(() => new URLSearchParams(window.location.search).get("path"));
  const [data, setData] = useState<TimelineData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadInstances()
      .then((index) => {
        setInstanceIndex(index);
        setSelectedPath((current) => current ?? index.defaultPath ?? index.instances[0]?.directory ?? null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (selectedPath === null) {
      if (instanceIndex !== null) setLoading(false);
      return;
    }
    setLoading(true);
    loadTimeline(selectedPath)
      .then((timeline) => {
        setData(timeline);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [instanceIndex, selectedPath]);

  function selectPath(path: string) {
    setSelectedPath(path);
    const nextUrl = `${window.location.pathname}?${new URLSearchParams({ path })}`;
    window.history.replaceState(null, "", nextUrl);
  }

  return { data, error, instanceIndex, loading, selectedPath, selectPath };
}

export function App() {
  const { data, error, instanceIndex, loading, selectedPath, selectPath } = useTimelineData();
  const [index, setIndex] = useState(0);

  if (error) return <main className="min-h-svh p-4 text-destructive">{error}</main>;
  if (loading && !data) return <LoadingState />;
  if (!data || !instanceIndex?.instances.length) return <EmptyState root={instanceIndex?.root} />;

  const selectedIndex = Math.max(0, Math.min(data.timeline.length - 1, index));
  const selectedTime = data.timeline[selectedIndex]?.time ?? null;
  const accountSnapshot = selectedTime ? atOrBefore(data.records.riskSnapshots, selectedTime) : null;
  const equitySnapshot = selectedTime ? atOrBefore(data.records.equity, selectedTime) : null;
  const timeline = data.timeline;

  function selectNearestTime(time: string) {
    const target = Date.parse(time);
    if (!Number.isFinite(target)) return;
    let bestIndex = 0;
    let bestDistance = Number.POSITIVE_INFINITY;
    timeline.forEach((point, pointIndex) => {
      const value = Date.parse(point.time);
      if (!Number.isFinite(value)) return;
      const distance = Math.abs(value - target);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = pointIndex;
      }
    });
    setIndex(bestIndex);
  }

  return (
    <main className="grid min-h-svh grid-rows-[auto_auto_auto_1fr] gap-3 bg-background p-4 max-sm:p-2">
      <header className="flex items-start justify-between gap-4 rounded-lg border bg-card p-4 shadow-sm max-lg:grid">
        <section>
          <h1 className="text-xl font-semibold leading-tight">Timeline</h1>
          <p className="mt-1 break-words text-sm text-muted-foreground">{data.instance.mode} / {data.instance.launchId} / {data.instance.launchInstanceId}</p>
        </section>
        <section className="grid min-w-[420px] grid-cols-4 gap-2 max-lg:min-w-0 max-md:grid-cols-2 max-sm:grid-cols-1">
          <Metric label="Final Equity" value={data.summary.final_equity} />
          <Metric label="Net PnL" value={data.summary.net_profit} />
          <Metric label="Timeline" value={data.instance.counts.timelineRecords} />
          <Metric label="Trace" value={data.instance.counts.decisionTrace} />
        </section>
      </header>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Activity data-icon="inline-start" /> Account and Market Timeline
          </CardTitle>
          <CardDescription>勾选实例里存在的数据；点击图上的时间会更新下方快照。</CardDescription>
        </CardHeader>
        <CardContent>
          <MarketChart data={data} selectedTime={selectedTime} onSelectTime={selectNearestTime} />
        </CardContent>
      </Card>

      <Card size="sm">
        <CardContent className="grid grid-cols-[auto_1fr_auto_minmax(220px,auto)] items-center gap-3 max-lg:grid-cols-[auto_1fr_auto]">
          <Button onClick={() => setIndex(Math.max(0, selectedIndex - 1))} size="sm" type="button" variant="outline">
            <ChevronLeft data-icon="inline-start" /> Prev
          </Button>
          <Slider
            aria-label="Timeline position"
            min={0}
            max={Math.max(0, data.timeline.length - 1)}
            step={1}
            value={[selectedIndex]}
            onValueChange={(value) => setIndex(Number((Array.isArray(value) ? value[0] : value) ?? 0))}
          />
          <Button onClick={() => setIndex(Math.min(data.timeline.length - 1, selectedIndex + 1))} size="sm" type="button" variant="outline">
            Next <ChevronRight data-icon="inline-end" />
          </Button>
          <strong className="break-words text-xs font-medium text-muted-foreground max-lg:col-span-full">{selectedTime ?? "No timestamp"}</strong>
        </CardContent>
      </Card>

      <section className="grid min-h-0 grid-cols-[minmax(270px,340px)_minmax(0,1fr)] gap-3 max-lg:grid-cols-1">
        <section className="flex min-h-[420px] flex-col gap-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Database data-icon="inline-start" /> Launch Instances
              </CardTitle>
              <CardDescription>{instanceIndex.count} instances under {instanceIndex.root}</CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-64 pr-2 max-lg:h-52">
                <div className="flex flex-col gap-2">
                  {instanceIndex.instances.map((instance) => (
                    <InstanceButton
                      instance={instance}
                      key={instance.directory}
                      selected={instance.directory === selectedPath}
                      onSelect={() => {
                        setIndex(0);
                        selectPath(instance.directory);
                      }}
                    />
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>

          <Card className="min-h-[340px]">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <ListTree data-icon="inline-start" /> Timeline
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[42vh] pr-2 max-lg:h-60">
                <div className="flex flex-col gap-2">
                  {data.timeline.map((point, pointIndex) => (
                    <Button
                      className="grid h-auto min-h-14 justify-stretch px-2 py-2 text-left"
                      key={`${point.time}-${pointIndex}`}
                      onClick={() => setIndex(pointIndex)}
                      type="button"
                      variant={pointIndex === selectedIndex ? "default" : "outline"}
                    >
                      <span className="truncate text-xs">{point.time}</span>
                      <span className="mt-1 flex flex-wrap gap-1">
                        {Object.entries(point.counts).map(([key, value]) => (
                          <Badge key={key} variant={pointIndex === selectedIndex ? "secondary" : "outline"}>{key}:{value}</Badge>
                        ))}
                      </span>
                    </Button>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </section>

        <section className="grid min-h-0 grid-rows-[auto_1fr] gap-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <WalletCards data-icon="inline-start" /> Account at Selected Time
              </CardTitle>
              <CardDescription>{selectedTime ?? "No timestamp selected"}</CardDescription>
            </CardHeader>
            <CardContent>
              <AccountSnapshot risk={accountSnapshot} equity={equitySnapshot} />
            </CardContent>
          </Card>

          <Card className="min-h-[420px] overflow-hidden">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Search data-icon="inline-start" /> Market Table at Selected Time
              </CardTitle>
              <CardDescription>Positions, funding rates, fills, trades, and OHLC rows nearest to the selected point.</CardDescription>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[52vh] pr-2">
                <MarketAtTime data={data} time={selectedTime} />
                <Separator className="my-4" />
                <RuntimeViews data={data} time={selectedTime} />
                <Separator className="my-4" />
                <details>
                  <summary className="cursor-pointer text-sm font-medium text-muted-foreground">Raw records at this time</summary>
                  <div className="mt-3">
                    <JsonBlock value={selectedTime ? rawSlice(data, selectedTime) : null} />
                  </div>
                </details>
              </ScrollArea>
            </CardContent>
          </Card>
        </section>
      </section>
    </main>
  );
}

function InstanceButton({ instance, selected, onSelect }: { instance: TimelineInstanceRow; selected: boolean; onSelect: () => void }) {
  return (
    <Button
      className="grid h-auto min-h-20 justify-stretch px-2 py-2 text-left"
      onClick={onSelect}
      type="button"
      variant={selected ? "default" : "outline"}
    >
      <span className="truncate text-xs font-medium">{instance.mode ?? "-"} / {instance.launch_id ?? "-"}</span>
      <span className="mt-1 truncate text-xs text-muted-foreground">{instance.launch_instance_id ?? "-"}</span>
      <span className="mt-2 flex flex-wrap gap-1">
        <Badge variant={selected ? "secondary" : "outline"}>timeline:{instance.timeline_count}</Badge>
        <Badge variant={selected ? "secondary" : "outline"}>trace:{instance.decision_trace_count}</Badge>
        <Badge variant={selected ? "secondary" : "outline"}>risk:{instance.risk_snapshot_count}</Badge>
        <Badge variant={selected ? "secondary" : "outline"}>equity:{instance.equity_count}</Badge>
      </span>
    </Button>
  );
}

function AccountSnapshot({ risk, equity }: { risk: Record<string, JsonValue> | null; equity: Record<string, JsonValue> | null }) {
  const source = risk ?? equity;
  if (!source) return <p className="p-3 text-sm text-muted-foreground">No account snapshot at or before this timestamp.</p>;
  const positions = Array.isArray(risk?.positions) ? risk.positions.filter(isRecord) : [];
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-4 gap-2 max-xl:grid-cols-2 max-sm:grid-cols-1">
        <Metric label="Snapshot Time" value={source.time} />
        <Metric label="Account" value={risk?.account_id} />
        <Metric label="Equity" value={risk?.equity ?? equity?.equity} />
        <Metric label="Cash" value={risk?.cash ?? equity?.cash} />
        <Metric label="Gross Notional" value={risk?.gross_notional} />
        <Metric label="Net Notional" value={risk?.net_notional} />
        <Metric label="Positions" value={positions.length} />
        <Metric label="Funding Rates" value={Array.isArray(risk?.funding_rates) ? risk.funding_rates.length : 0} />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-medium">Positions</h3>
        <Rows rows={positions} columns={["instrument_id", "quantity", "mark_price", "notional"]} />
      </div>
    </div>
  );
}

function MarketAtTime({ data, time }: { data: TimelineData; time: string | null }) {
  if (!time) return <p className="text-sm text-muted-foreground">No timeline records.</p>;
  const rows = marketRowsAtTime(data, time);
  return (
    <Rows
      rows={rows}
      columns={["source", "time", "market_id", "instrument_id", "price", "rate", "quantity", "notional", "side"]}
    />
  );
}

function RuntimeViews({ data, time }: { data: TimelineData; time: string | null }) {
  const views = viewsAtTime(data, time);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const viewKeys = Object.keys(views);
  const activeKey = selectedKey && views[selectedKey] ? selectedKey : viewKeys[0] ?? null;

  useEffect(() => {
    setSelectedKey(null);
  }, [time]);

  if (!time) return <p className="text-sm text-muted-foreground">No selected timestamp.</p>;
  if (!viewKeys.length) return <p className="text-sm text-muted-foreground">No sampled runtime views at this timestamp.</p>;

  return (
    <section className="grid grid-cols-[minmax(220px,300px)_minmax(0,1fr)] gap-3 max-xl:grid-cols-1">
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Runtime Views at Selected Time</h3>
        <div className="flex flex-col gap-1">
          {viewKeys.map((key) => {
            const view = objectValue(views[key]);
            const payload = objectValue(view.payload);
            const payloadCount = payloadSize(payload);
            return (
              <Button
                className="grid h-auto justify-stretch px-2 py-2 text-left"
                key={key}
                onClick={() => setSelectedKey(key)}
                type="button"
                variant={key === activeKey ? "default" : "outline"}
              >
                <span className="truncate text-xs font-medium">{key}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  <Badge variant={key === activeKey ? "secondary" : "outline"}>v{formatCell(view.schema_version)}</Badge>
                  <Badge variant={key === activeKey ? "secondary" : "outline"}>{payloadCount} field{payloadCount === 1 ? "" : "s"}</Badge>
                </span>
              </Button>
            );
          })}
        </div>
      </div>
      <div className="min-w-0">
        <JsonBlock value={activeKey ? views[activeKey] : null} />
      </div>
    </section>
  );
}

function marketRowsAtTime(data: TimelineData, time: string) {
  const risk = atOrBefore(data.records.riskSnapshots, time);
  const positions = Array.isArray(risk?.positions) ? risk.positions.filter(isRecord) : [];
  const rates = Array.isArray(risk?.funding_rates) ? risk.funding_rates.filter(isRecord) : [];
  const fills = atTime(data.records.fills, time);
  const trades = latestRowsByMarket(data.records.trades, time);

  return [
    ...positions.map((row) => ({
      source: "position",
      time: risk?.time,
      market_id: row.market_id,
      instrument_id: row.instrument_id,
      price: row.mark_price,
      rate: null,
      quantity: row.quantity,
      notional: row.notional,
      side: null,
    })),
    ...rates.map((row) => ({
      source: "funding",
      time: row.time ?? risk?.time,
      market_id: row.market_id,
      instrument_id: row.instrument_id,
      price: row.mark_price,
      rate: row.rate,
      quantity: null,
      notional: null,
      side: null,
    })),
    ...fills.map((row) => ({
      source: "fill",
      time: row.occurred_at,
      market_id: row.market_id,
      instrument_id: row.instrument_id,
      price: row.price,
      rate: null,
      quantity: row.quantity,
      notional: row.notional ?? row.cost,
      side: row.side,
    })),
    ...trades.map((row) => ({
      source: row.open && row.high && row.low && row.close ? "ohlcv" : "trade",
      time: row.time ?? row.occurred_at,
      market_id: row.market_id,
      instrument_id: row.instrument_id,
      price: row.close ?? row.price,
      rate: null,
      quantity: row.volume ?? row.size ?? row.amount,
      notional: row.cost,
      side: row.side,
    })),
  ];
}

function Decision({ records }: { records: Record<string, JsonValue>[] }) {
  if (!records.length) return <p className="p-3 text-sm text-muted-foreground">No decision trace at this timestamp.</p>;
  return (
    <div className="flex flex-col gap-4">
      {records.map((record, index) => {
        const payload = objectValue(record.payload);
        const decision = objectValue(payload.decision);
        return (
          <section className="flex flex-col gap-3" key={index}>
            <div className="grid grid-cols-4 gap-2 max-xl:grid-cols-2 max-sm:grid-cols-1">
              <Metric label="Action" value={decision.action} />
              <Metric label="Reason" value={decision.reason} />
              <Metric label="Symbol" value={payload.symbol} />
              <Metric label="Funding Rate" value={payload.funding_rate} />
              <Metric label="Basis" value={payload.basis} />
              <Metric label="Net Funding" value={payload.net_funding_rate} />
              <Metric label="Spot Price" value={payload.spot_price} />
              <Metric label="Swap Price" value={payload.swap_price} />
            </div>
            <JsonBlock value={record} />
          </section>
        );
      })}
    </div>
  );
}

function Risk({ record }: { record: Record<string, JsonValue> | null }) {
  if (!record) return <p className="p-3 text-sm text-muted-foreground">No risk snapshot at or before this timestamp.</p>;
  const positions = Array.isArray(record.positions) ? record.positions.filter(isRecord) : [];
  const rates = Array.isArray(record.funding_rates) ? record.funding_rates.filter(isRecord) : [];
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-4 gap-2 max-xl:grid-cols-2 max-sm:grid-cols-1">
        <Metric label="Snapshot Time" value={record.time} />
        <Metric label="Equity" value={record.equity} />
        <Metric label="Cash" value={record.cash} />
        <Metric label="Gross Notional" value={record.gross_notional} />
        <Metric label="Net Notional" value={record.net_notional} />
        <Metric label="Positions" value={positions.length} />
        <Metric label="Funding Rates" value={rates.length} />
        <Metric label="Account" value={record.account_id} />
      </div>
      <Separator />
      <h3 className="text-sm font-medium">Positions</h3>
      <Rows rows={positions} columns={["instrument_id", "quantity", "mark_price", "notional"]} />
      <h3 className="text-sm font-medium">Funding Rates</h3>
      <Rows rows={rates} columns={["time", "market_id", "instrument_id", "rate", "mark_price"]} />
      <JsonBlock value={record} />
    </div>
  );
}

function Rows({ rows, columns }: { rows: Record<string, JsonValue | number | undefined>[]; columns: string[] }) {
  if (!rows.length) return <p className="p-3 text-sm text-muted-foreground">No rows.</p>;
  return (
    <Table>
      <TableHeader><TableRow>{columns.map((column) => <TableHead key={column}>{column}</TableHead>)}</TableRow></TableHeader>
      <TableBody>
        {rows.map((row, index) => (
          <TableRow key={index}>{columns.map((column) => <TableCell className="max-w-[240px] whitespace-normal break-words" key={column}>{formatCell(row[column])}</TableCell>)}</TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Metric({ label, value }: { label: string; value: JsonValue | number | undefined }) {
  return (
    <Card size="sm" className="shadow-none">
      <CardContent className="px-3">
        <span className="block text-xs text-muted-foreground">{label}</span>
        <strong className="mt-1 block break-words text-sm font-semibold">{formatCell(value)}</strong>
      </CardContent>
    </Card>
  );
}

function JsonBlock({ value }: { value: JsonValue | Record<string, JsonValue> | null }) {
  return <pre className="min-w-0 whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-3 text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>;
}

function LoadingState() {
  return (
    <main className="flex min-h-svh flex-col gap-3 bg-background p-4">
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-80 w-full" />
      <Skeleton className="h-14 w-full" />
      <div className="grid grid-cols-[minmax(270px,360px)_1fr] gap-3 max-lg:grid-cols-1">
        <Skeleton className="h-[420px] w-full" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    </main>
  );
}

function EmptyState({ root }: { root?: string }) {
  return (
    <main className="flex min-h-svh items-center justify-center bg-background p-4">
      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>No timeline instances</CardTitle>
          <CardDescription>{root ? `No timeline-capable launch instances under ${root}.` : "No timeline-capable launch instances were found."}</CardDescription>
        </CardHeader>
      </Card>
    </main>
  );
}

function atTime(rows: Record<string, JsonValue>[], time: string) {
  return rows.filter((row) => recordTime(row) === time);
}

function atOrBefore(rows: Record<string, JsonValue>[], time: string) {
  let found: Record<string, JsonValue> | null = null;
  for (const row of rows) {
    const rowTime = recordTime(row);
    if (rowTime && rowTime <= time) found = row;
    if (rowTime && rowTime > time) break;
  }
  return found;
}

function latestRowsByMarket(rows: Record<string, JsonValue>[], time: string) {
  const byMarket = new Map<string, Record<string, JsonValue>>();
  for (const row of rows) {
    const rowTime = recordTime(row);
    if (!rowTime || rowTime > time) continue;
    const key = formatCell(row.market_id ?? row.instrument_id ?? row.symbol ?? "market");
    const current = byMarket.get(key);
    if (!current || (recordTime(current) ?? "") <= rowTime) byMarket.set(key, row);
  }
  return Array.from(byMarket.values());
}

function recordTime(row: Record<string, JsonValue>) {
  if (typeof row.time === "string") return row.time;
  if (typeof row.occurred_at === "string") return row.occurred_at;
  if (typeof row.updated_at === "string") return row.updated_at;
  const intent = objectValue(row.intent);
  return typeof intent.created_at === "string" ? intent.created_at : null;
}

function compactIntent(row: Record<string, JsonValue>) {
  const intent = objectValue(row.intent);
  return {
    updated_at: row.updated_at,
    status: row.status,
    intent_id: refValue(intent.intent_id),
    instrument_id: refValue(intent.instrument_id),
    target_quantity: intent.target_quantity,
    reason: intent.reason
  };
}

function rawSlice(data: TimelineData, time: string) {
  return {
    time,
    timelineRecords: atTime(data.records.timelineRecords, time),
    decisionTrace: atTime(data.records.decisionTrace, time),
    riskSnapshot: atOrBefore(data.records.riskSnapshots, time),
    equity: atOrBefore(data.records.equity, time),
    fills: atTime(data.records.fills, time),
    intents: atTime(data.records.intents, time),
    trades: atTime(data.records.trades, time)
  };
}

function viewsAtTime(data: TimelineData, time: string | null): Record<string, JsonValue> {
  if (!time) return {};
  const records = atTime(data.records.timelineRecords, time);
  const merged: Record<string, JsonValue> = {};
  for (const record of records) {
    const views = objectValue(record.views);
    for (const [key, value] of Object.entries(views)) {
      merged[key] = value;
    }
  }
  return merged;
}

function payloadSize(payload: Record<string, JsonValue>) {
  return Object.keys(payload).length;
}

function objectValue(value: JsonValue | undefined): Record<string, JsonValue> {
  return isRecord(value) ? value : {};
}

function isRecord(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function refValue(value: JsonValue | undefined) {
  const record = objectValue(value);
  return record.value ?? value ?? null;
}

function formatCell(value: JsonValue | number | undefined) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
