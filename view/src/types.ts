export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface TimelinePoint {
  time: string;
  counts: Record<string, number>;
}

export interface TimelineInstance {
  path: string;
  launchId: string | null;
  mode: string | null;
  launchInstanceId: string | null;
  strategyId: string | null;
  timeRange: { start: string | null; end: string | null };
  counts: Record<string, number>;
}

export interface TimelineInstanceRow {
  mode: string | null;
  launch_id: string | null;
  launch_instance_id: string | null;
  strategy_id: string | null;
  updated_at: number;
  directory: string;
  timeline_count: number;
  decision_trace_count: number;
  risk_snapshot_count: number;
  equity_count: number;
}

export interface TimelineInstanceIndex {
  root: string;
  defaultPath: string | null;
  instances: TimelineInstanceRow[];
  count: number;
}

export interface EquityPoint {
  time: string;
  equity?: string | null;
  cash?: string | null;
}

export interface RiskPoint {
  time: string;
  equity?: string | null;
  cash?: string | null;
  grossNotional?: string | null;
  netNotional?: string | null;
  positionCount: number;
}

export interface MarketSeriesPoint {
  time: string;
  key: string;
  label: string;
  kind: "ohlcv" | "price" | "rate" | "trade";
  marketId?: string | null;
  instrumentId?: string | null;
  venue?: string | null;
  market?: string | null;
  open?: string | null;
  high?: string | null;
  low?: string | null;
  close?: string | null;
  value?: string | null;
  volume?: string | null;
  rate?: string | null;
}

export interface TimelineData {
  instance: TimelineInstance;
  summary: Record<string, JsonValue>;
  metrics: Record<string, JsonValue>;
  config: Record<string, JsonValue>;
  state: Record<string, JsonValue>;
  series: {
    equity: EquityPoint[];
    risk: RiskPoint[];
    fundingRates: Record<string, JsonValue>[];
    markets?: MarketSeriesPoint[];
  };
  records: {
    timelineRecords: Record<string, JsonValue>[];
    decisionTrace: Record<string, JsonValue>[];
    riskSnapshots: Record<string, JsonValue>[];
    equity: Record<string, JsonValue>[];
    fills: Record<string, JsonValue>[];
    intents: Record<string, JsonValue>[];
    trades: Record<string, JsonValue>[];
  };
  timeline: TimelinePoint[];
}
