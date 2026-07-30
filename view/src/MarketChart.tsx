import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  createChart,
  type CandlestickData,
  type IChartApi,
  type LineData,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { JsonValue, MarketSeriesPoint, TimelineData } from "./types";
import { Badge } from "@/components/ui/badge";

type SourceKind = "account" | "market";

interface ChartSource {
  key: string;
  label: string;
  kind: SourceKind;
  chartKind: "line" | "candles";
  data: LineData<Time>[] | CandlestickData<Time>[];
  color: string;
  count: number;
}

const MARKET_COLORS = ["#d95d4f", "#8a5cf6", "#c78200", "#2f7d32", "#cc3d7a", "#607d8b", "#7b5e2a", "#5d6fd3"];

export function MarketChart({
  data,
  selectedTime,
  onSelectTime,
}: {
  data: TimelineData;
  selectedTime: string | null;
  onSelectTime: (time: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const sources = useMemo(() => chartSources(data), [data]);
  const [visibleKeys, setVisibleKeys] = useState<string[]>(() => defaultVisibleKeys(sources));

  useEffect(() => {
    setVisibleKeys(defaultVisibleKeys(sources));
  }, [sources]);

  const visibleSources = useMemo(
    () => sources.filter((source) => visibleKeys.includes(source.key)),
    [sources, visibleKeys],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      autoSize: true,
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#596273",
      },
      grid: {
        vertLines: { color: "#e7ebf0" },
        horzLines: { color: "#e7ebf0" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      leftPriceScale: { visible: true, borderColor: "#d9dee7" },
      rightPriceScale: { visible: true, borderColor: "#d9dee7" },
      timeScale: { borderColor: "#d9dee7", timeVisible: true, secondsVisible: false },
    });

    for (const source of visibleSources) {
      if (source.chartKind === "candles") {
        const series = chart.addSeries(CandlestickSeries, {
          priceScaleId: "right",
          title: source.label,
          upColor: "#1f9d76",
          downColor: "#d95d4f",
          borderUpColor: "#1f9d76",
          borderDownColor: "#d95d4f",
          wickUpColor: "#1f9d76",
          wickDownColor: "#d95d4f",
        });
        series.setData(source.data as CandlestickData<Time>[]);
      } else {
        const series = chart.addSeries(LineSeries, {
          color: source.color,
          lineWidth: source.kind === "account" ? 2 : 1,
          priceLineVisible: false,
          priceScaleId: source.kind === "account" ? "left" : "right",
          title: source.label,
        });
        series.setData(source.data as LineData<Time>[]);
      }
    }

    chart.subscribeClick((event: MouseEventParams<Time>) => {
      const clickedTime = chartTimeToIso(event.time);
      if (clickedTime) onSelectTime(clickedTime);
    });

    chart.timeScale().fitContent();
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [onSelectTime, visibleSources]);

  useEffect(() => {
    if (!selectedTime || !chartRef.current) return;
    const selected = isoToTime(selectedTime);
    if (selected === null) return;
    const minTime = minSeriesTime(visibleSources);
    if (minTime === null) return;
    const visible = chartRef.current.timeScale().getVisibleRange();
    if (!visible || selected < Number(visible.from) || selected > Number(visible.to)) {
      chartRef.current.timeScale().setVisibleRange({
        from: Math.max(minTime, selected - 24 * 60 * 60) as UTCTimestamp,
        to: (selected + 24 * 60 * 60) as UTCTimestamp,
      });
    }
  }, [selectedTime, visibleSources]);

  function toggle(key: string) {
    setVisibleKeys((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key]);
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {sources.map((source) => (
          <label
            className="inline-flex h-7 items-center gap-2 rounded-md border bg-background px-2 text-xs font-medium text-foreground"
            key={source.key}
          >
            <input
              checked={visibleKeys.includes(source.key)}
              className="size-3.5 accent-primary"
              onChange={() => toggle(source.key)}
              type="checkbox"
            />
            <span className="size-2 rounded-full" style={{ backgroundColor: source.color }} />
            <span>{source.label}</span>
            <Badge variant="outline">{source.count}</Badge>
          </label>
        ))}
      </div>
      <div className="h-[420px] min-w-0" ref={containerRef} />
      {!sources.length ? <p className="text-sm text-muted-foreground">This instance has no plottable account or market data.</p> : null}
    </div>
  );
}

function chartSources(data: TimelineData): ChartSource[] {
  const account = [
    lineSource("account:equity", "Account equity", "account", "#1d5fd0", data.series.equity, "equity"),
    lineSource("account:cash", "Cash", "account", "#00856f", data.series.equity, "cash"),
    lineSource("account:gross", "Gross notional", "account", "#7a5cff", data.series.risk, "grossNotional"),
    lineSource("account:net", "Net notional", "account", "#b46b00", data.series.risk, "netNotional"),
  ].filter((source): source is ChartSource => source !== null);
  return [...account, ...marketSources(data)];
}

function lineSource(
  key: string,
  label: string,
  kind: SourceKind,
  color: string,
  rows: Array<{ time?: string | null }>,
  field: string,
): ChartSource | null {
  const points = rows
    .map((row) => linePoint(String(row.time ?? ""), (row as Record<string, JsonValue | number | undefined>)[field]))
    .filter((point): point is LineData<Time> => point !== null);
  const data = dedupeByTime(points);
  if (!data.length) return null;
  return { key, label, kind, chartKind: "line", data, color, count: data.length };
}

function marketSources(data: TimelineData): ChartSource[] {
  const byKey = new Map<string, MarketSeriesPoint[]>();
  for (const row of marketRows(data)) {
    const bucket = byKey.get(row.key) ?? [];
    bucket.push(row);
    byKey.set(row.key, bucket);
  }
  return Array.from(byKey.entries()).map((entry, index): ChartSource => {
    const [key, bucket] = entry;
    const candleData = dedupeByTime(bucket.map(candlePoint).filter((point): point is CandlestickData<Time> => point !== null));
    const color = MARKET_COLORS[index % MARKET_COLORS.length];
    if (candleData.length) {
      return { key, label: bucket[0]?.label ?? key, kind: "market", chartKind: "candles", data: candleData, color, count: candleData.length };
    }
    const lineData = dedupeByTime(bucket
      .map((row) => linePoint(row.time, row.value ?? row.close ?? row.rate ?? null))
      .filter((point): point is LineData<Time> => point !== null));
    return { key, label: bucket[0]?.label ?? key, kind: "market", chartKind: "line", data: lineData, color, count: lineData.length };
  }).filter((source) => source.count > 0);
}

function marketRows(data: TimelineData): MarketSeriesPoint[] {
  const explicit = data.series.markets ?? [];
  const fromFunding = data.series.fundingRates.flatMap((row): MarketSeriesPoint[] => {
    const time = text(row.snapshotTime) ?? text(row.time);
    const marketId = text(row.market_id);
    if (!time || !marketId) return [];
    return [{ time, key: `funding:${marketId}`, label: `${shortMarket(marketId)} funding`, kind: "rate", marketId, instrumentId: text(row.instrument_id), value: text(row.rate), rate: text(row.rate) }];
  });
  const fromPositions = data.records.riskSnapshots.flatMap((snapshot): MarketSeriesPoint[] => {
    const time = text(snapshot.time);
    const positions = Array.isArray(snapshot.positions) ? snapshot.positions.filter(isRecord) : [];
    if (!time) return [];
    return positions.flatMap((position): MarketSeriesPoint[] => {
      const instrumentId = text(position.instrument_id);
      const value = text(position.mark_price);
      if (!instrumentId || !value) return [];
      return [{ time, key: `position:${instrumentId}`, label: `${shortMarket(instrumentId)} mark`, kind: "price", instrumentId, value }];
    });
  });
  const fromTrades = data.records.trades.flatMap((row): MarketSeriesPoint[] => {
    const time = text(row.time) ?? text(row.occurred_at);
    const marketId = text(row.market_id) ?? text(row.instrument_id);
    if (!time || !marketId) return [];
    return [{
      time,
      key: `trade:${marketId}`,
      label: `${shortMarket(marketId)} trades`,
      kind: hasOhlc(row) ? "ohlcv" : "trade",
      marketId,
      instrumentId: text(row.instrument_id),
      open: text(row.open),
      high: text(row.high),
      low: text(row.low),
      close: text(row.close),
      value: text(row.price) ?? text(row.close),
      volume: text(row.volume) ?? text(row.size) ?? text(row.amount),
    }];
  });
  return [...explicit, ...fromFunding, ...fromPositions, ...fromTrades];
}

function defaultVisibleKeys(sources: ChartSource[]) {
  const account = sources.filter((source) => source.kind === "account").slice(0, 2).map((source) => source.key);
  const market = sources.filter((source) => source.kind === "market").slice(0, 4).map((source) => source.key);
  return [...account, ...market];
}

function dedupeByTime<T extends { time: Time }>(points: T[]): T[] {
  const byTime = new Map<string, T>();
  for (const point of points) byTime.set(String(point.time), point);
  return Array.from(byTime.values()).sort((left, right) => Number(left.time) - Number(right.time));
}

function linePoint(time: string | undefined, rawValue: JsonValue | number | undefined): LineData<Time> | null {
  const chartTime = isoToTime(time);
  const value = numeric(rawValue);
  if (chartTime === null || value === null) return null;
  return { time: chartTime as Time, value };
}

function candlePoint(row: MarketSeriesPoint): CandlestickData<Time> | null {
  const time = isoToTime(row.time);
  const open = numeric(row.open);
  const high = numeric(row.high);
  const low = numeric(row.low);
  const close = numeric(row.close);
  if (time === null || open === null || high === null || low === null || close === null) return null;
  return { time: time as Time, open, high, low, close };
}

function isoToTime(value: string | null | undefined): UTCTimestamp | null {
  if (!value) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return Math.floor(timestamp / 1000) as UTCTimestamp;
}

function chartTimeToIso(value: Time | undefined): string | null {
  if (typeof value === "number") return new Date(value * 1000).toISOString();
  if (typeof value === "string") return value;
  if (value && typeof value === "object") return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}T00:00:00.000Z`;
  return null;
}

function minSeriesTime(sources: ChartSource[]) {
  const times = sources.flatMap((source) => source.data.map((point) => Number(point.time)).filter(Number.isFinite));
  return times.length ? Math.min(...times) : null;
}

function numeric(value: JsonValue | number | undefined) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function text(value: JsonValue | undefined): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function hasOhlc(row: Record<string, JsonValue>) {
  return ["open", "high", "low", "close"].every((key) => numeric(row[key]) !== null);
}

function isRecord(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function shortMarket(value: string) {
  return value.replace(/^market:/, "").replace(/^instrument:/, "").replaceAll(":", " ");
}
