from __future__ import annotations

HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Kairos Timeline Viewer</title>
    <link rel="stylesheet" href="/style.css">
  </head>
  <body>
    <main class="app">
      <header class="topbar">
        <div>
          <h1>Kairos Timeline</h1>
          <p id="launch-subtitle">Loading launch data...</p>
        </div>
        <div class="metrics" id="metrics"></div>
      </header>
      <section class="chart-band">
        <div class="chart-head">
          <strong>Equity / Risk</strong>
          <span id="range-label"></span>
        </div>
        <canvas id="chart" height="220"></canvas>
      </section>
      <section class="timeline-band">
        <div class="timeline-row">
          <button id="prev" type="button" title="Previous time">Prev</button>
          <input id="scrubber" type="range" min="0" max="0" value="0">
          <button id="next" type="button" title="Next time">Next</button>
        </div>
        <div class="selected-time" id="selected-time"></div>
      </section>
      <section class="workspace">
        <aside class="rail">
          <h2>Events</h2>
          <div id="event-list" class="event-list"></div>
        </aside>
        <section class="detail">
          <div class="tabs">
            <button class="tab active" data-tab="decision" type="button">Decision</button>
            <button class="tab" data-tab="risk" type="button">Risk</button>
            <button class="tab" data-tab="fills" type="button">Fills</button>
            <button class="tab" data-tab="intents" type="button">Intents</button>
            <button class="tab" data-tab="raw" type="button">Raw</button>
          </div>
          <div id="detail-panel" class="detail-panel"></div>
        </section>
      </section>
    </main>
    <script src="/app.js"></script>
  </body>
</html>
"""

CSS = """
:root {
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --ink: #151923;
  --muted: #687080;
  --line: #dde2ea;
  --accent: #1967d2;
  --accent-2: #00856f;
  --warn: #b35c00;
  --bad: #b3261e;
  --shadow: 0 10px 26px rgba(18, 27, 38, 0.08);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.app {
  min-height: 100vh;
  display: grid;
  grid-template-rows: auto auto auto 1fr;
  gap: 12px;
  padding: 16px;
}

.topbar, .chart-band, .timeline-band, .rail, .detail {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.topbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 16px 18px;
}

h1, h2, p { margin: 0; }
h1 { font-size: 22px; line-height: 1.2; font-weight: 720; letter-spacing: 0; }
h2 { font-size: 14px; line-height: 1.3; margin-bottom: 10px; }
p, .muted { color: var(--muted); font-size: 13px; line-height: 1.45; }

.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(98px, 1fr));
  gap: 8px;
  min-width: 420px;
}
.metric {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
}
.metric span { display: block; color: var(--muted); font-size: 11px; }
.metric strong { display: block; font-size: 14px; margin-top: 2px; overflow-wrap: anywhere; }

.chart-band { padding: 12px 14px 8px; min-height: 280px; }
.chart-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 13px; }
#chart { width: 100%; height: 220px; display: block; }

.timeline-band { padding: 12px 14px; }
.timeline-row { display: grid; grid-template-columns: 72px 1fr 72px; gap: 10px; align-items: center; }
button {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
button:hover { border-color: #aab5c5; }
.tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
input[type="range"] { width: 100%; accent-color: var(--accent); }
.selected-time { margin-top: 8px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }

.workspace {
  display: grid;
  grid-template-columns: minmax(260px, 360px) 1fr;
  gap: 12px;
  min-height: 0;
}
.rail, .detail { min-height: 420px; overflow: hidden; }
.rail { padding: 14px; }
.event-list { display: grid; gap: 8px; max-height: 68vh; overflow: auto; padding-right: 4px; }
.event {
  text-align: left;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px;
  background: #fff;
  cursor: pointer;
}
.event.active { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
.event-time { font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.event-counts { margin-top: 5px; display: flex; flex-wrap: wrap; gap: 5px; }
.chip {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  border-radius: 4px;
  border: 1px solid var(--line);
  padding: 2px 6px;
  font-size: 11px;
  color: var(--muted);
}

.detail { display: grid; grid-template-rows: auto 1fr; }
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--line);
}
.detail-panel { padding: 14px; overflow: auto; max-height: 72vh; }
.grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.kv { border: 1px solid var(--line); border-radius: 6px; padding: 9px; }
.kv span { display: block; color: var(--muted); font-size: 11px; }
.kv strong { display: block; font-size: 13px; margin-top: 4px; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 650; }
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  background: #fbfcfe;
  font-size: 12px;
  line-height: 1.45;
}
.empty { color: var(--muted); font-size: 13px; }

@media (max-width: 900px) {
  .topbar { display: grid; }
  .metrics { min-width: 0; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .workspace { grid-template-columns: 1fr; }
  .rail, .detail { min-height: 0; }
  .event-list { max-height: 240px; }
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 560px) {
  .app { padding: 10px; }
  .timeline-row { grid-template-columns: 58px 1fr 58px; }
  .metrics, .grid { grid-template-columns: 1fr; }
}
"""

JS = """
const state = { data: null, index: 0, tab: "decision" };

const $ = (id) => document.getElementById(id);
const fmt = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
const num = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

async function boot() {
  const response = await fetch("/api/timeline");
  if (!response.ok) throw new Error(await response.text());
  state.data = await response.json();
  bindControls();
  renderAll();
}

function bindControls() {
  $("scrubber").addEventListener("input", (event) => {
    state.index = Number(event.target.value);
    renderSelection();
  });
  $("prev").addEventListener("click", () => {
    state.index = Math.max(0, state.index - 1);
    renderSelection();
  });
  $("next").addEventListener("click", () => {
    state.index = Math.min(state.data.timeline.length - 1, state.index + 1);
    renderSelection();
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.tab = button.dataset.tab;
      document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === button));
      renderDetail();
    });
  });
  window.addEventListener("resize", () => drawChart());
}

function renderAll() {
  const data = state.data;
  $("launch-subtitle").textContent = `${data.instance.mode || "-"} / ${data.instance.launchId || "-"} / ${data.instance.launchInstanceId || "-"}`;
  $("metrics").innerHTML = [
    ["Final Equity", data.summary.final_equity],
    ["Net PnL", data.summary.net_profit],
    ["Events", data.summary.event_count],
    ["Trace", data.instance.counts.decisionTrace],
  ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${fmt(value)}</strong></div>`).join("");
  const range = data.instance.timeRange || {};
  $("range-label").textContent = `${fmt(range.start)} to ${fmt(range.end)}`;
  const scrubber = $("scrubber");
  scrubber.max = Math.max(0, data.timeline.length - 1);
  scrubber.value = state.index;
  renderEvents();
  drawChart();
  renderSelection();
}

function renderEvents() {
  const list = $("event-list");
  list.innerHTML = state.data.timeline.map((item, index) => {
    const counts = Object.entries(item.counts || {}).map(([key, value]) => `<span class="chip">${key}: ${value}</span>`).join("");
    return `<div class="event" data-index="${index}"><div class="event-time">${item.time}</div><div class="event-counts">${counts}</div></div>`;
  }).join("");
  list.querySelectorAll(".event").forEach((node) => {
    node.addEventListener("click", () => {
      state.index = Number(node.dataset.index);
      renderSelection();
    });
  });
}

function renderSelection() {
  const timeline = state.data.timeline;
  if (!timeline.length) {
    $("selected-time").textContent = "No timeline records.";
    $("detail-panel").innerHTML = '<p class="empty">No records found.</p>';
    return;
  }
  state.index = Math.max(0, Math.min(timeline.length - 1, state.index));
  $("scrubber").value = state.index;
  const time = timeline[state.index].time;
  $("selected-time").textContent = time;
  document.querySelectorAll(".event").forEach((node) => node.classList.toggle("active", Number(node.dataset.index) === state.index));
  const active = document.querySelector(`.event[data-index="${state.index}"]`);
  if (active) active.scrollIntoView({ block: "nearest" });
  drawChart();
  renderDetail();
}

function atOrBefore(rows, time) {
  let found = null;
  for (const row of rows) {
    const rowTime = row.time || row.occurred_at || row.updated_at || (row.intent && row.intent.created_at);
    if (rowTime && rowTime <= time) found = row;
    if (rowTime && rowTime > time) break;
  }
  return found;
}

function atTime(rows, time) {
  return rows.filter((row) => {
    const rowTime = row.time || row.occurred_at || row.updated_at || (row.intent && row.intent.created_at);
    return rowTime === time;
  });
}

function renderDetail() {
  const time = state.data.timeline[state.index]?.time;
  const records = state.data.records;
  if (state.tab === "decision") return renderDecision(time, records);
  if (state.tab === "risk") return renderRisk(time, records);
  if (state.tab === "fills") return renderRows("Fills", atTime(records.fills, time), ["occurred_at", "instrument_id", "side", "quantity", "price", "fee", "intent_id"]);
  if (state.tab === "intents") return renderIntents(time, records);
  renderRaw(time, records);
}

function renderDecision(time, records) {
  const traces = atTime(records.decisionTrace, time);
  if (!traces.length) {
    $("detail-panel").innerHTML = '<p class="empty">No strategy decision trace at this timestamp.</p>';
    return;
  }
  $("detail-panel").innerHTML = traces.map((trace) => {
    const payload = trace.payload || {};
    const decision = payload.decision || {};
    return `
      <div class="grid">
        <div class="kv"><span>Action</span><strong>${fmt(decision.action)}</strong></div>
        <div class="kv"><span>Reason</span><strong>${fmt(decision.reason)}</strong></div>
        <div class="kv"><span>Symbol</span><strong>${fmt(payload.symbol)}</strong></div>
        <div class="kv"><span>Funding Rate</span><strong>${fmt(payload.funding_rate)}</strong></div>
        <div class="kv"><span>Basis</span><strong>${fmt(payload.basis)}</strong></div>
        <div class="kv"><span>Net Funding</span><strong>${fmt(payload.net_funding_rate)}</strong></div>
        <div class="kv"><span>Spot Price</span><strong>${fmt(payload.spot_price)}</strong></div>
        <div class="kv"><span>Swap Price</span><strong>${fmt(payload.swap_price)}</strong></div>
      </div>
      <pre>${escapeHtml(JSON.stringify(trace, null, 2))}</pre>
    `;
  }).join("");
}

function renderRisk(time, records) {
  const risk = atOrBefore(records.riskSnapshots, time);
  if (!risk) {
    $("detail-panel").innerHTML = '<p class="empty">No risk snapshot at or before this timestamp.</p>';
    return;
  }
  const positions = Array.isArray(risk.positions) ? risk.positions : [];
  const rates = Array.isArray(risk.funding_rates) ? risk.funding_rates : [];
  $("detail-panel").innerHTML = `
    <div class="grid">
      <div class="kv"><span>Snapshot Time</span><strong>${fmt(risk.time)}</strong></div>
      <div class="kv"><span>Equity</span><strong>${fmt(risk.equity)}</strong></div>
      <div class="kv"><span>Cash</span><strong>${fmt(risk.cash)}</strong></div>
      <div class="kv"><span>Gross Notional</span><strong>${fmt(risk.gross_notional)}</strong></div>
      <div class="kv"><span>Net Notional</span><strong>${fmt(risk.net_notional)}</strong></div>
      <div class="kv"><span>Positions</span><strong>${positions.length}</strong></div>
      <div class="kv"><span>Funding Rates</span><strong>${rates.length}</strong></div>
      <div class="kv"><span>Account</span><strong>${fmt(risk.account_id)}</strong></div>
    </div>
    ${table("Positions", positions, ["instrument_id", "quantity", "mark_price", "notional"])}
    ${table("Funding Rates", rates, ["time", "market_id", "instrument_id", "rate", "mark_price"])}
    <pre>${escapeHtml(JSON.stringify(risk, null, 2))}</pre>
  `;
}

function renderIntents(time, records) {
  const rows = atTime(records.intents, time);
  if (!rows.length) {
    $("detail-panel").innerHTML = '<p class="empty">No intent state updates at this timestamp.</p>';
    return;
  }
  $("detail-panel").innerHTML = rows.map((row) => {
    const intent = row.intent || {};
    const compact = {
      updated_at: row.updated_at,
      status: row.status,
      order_ids: row.order_ids,
      intent_id: intent.intent_id?.value || intent.intent_id,
      strategy_id: intent.strategy_id?.value || intent.strategy_id,
      market_id: intent.market_id?.value || intent.market_id,
      instrument_id: intent.instrument_id?.value || intent.instrument_id,
      target_quantity: intent.target_quantity,
      reason: intent.reason,
    };
    return `<pre>${escapeHtml(JSON.stringify(compact, null, 2))}</pre>`;
  }).join("");
}

function renderRaw(time, records) {
  const payload = {
    time,
    decisionTrace: atTime(records.decisionTrace, time),
    riskSnapshot: atOrBefore(records.riskSnapshots, time),
    equity: atOrBefore(records.equity, time),
    fills: atTime(records.fills, time),
    intents: atTime(records.intents, time),
    trades: atTime(records.trades, time),
  };
  $("detail-panel").innerHTML = `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
}

function renderRows(title, rows, columns) {
  $("detail-panel").innerHTML = rows.length ? table(title, rows, columns) : `<p class="empty">No ${title.toLowerCase()} at this timestamp.</p>`;
}

function table(title, rows, columns) {
  if (!rows.length) return `<h2>${title}</h2><p class="empty">None</p>`;
  return `
    <h2>${title}</h2>
    <table>
      <thead><tr>${columns.map((col) => `<th>${col}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((row) => `<tr>${columns.map((col) => `<td>${escapeHtml(fmt(row[col]))}</td>`).join("")}</tr>`).join("")}</tbody>
    </table>
  `;
}

function drawChart() {
  const canvas = $("chart");
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  const pad = { left: 54, right: 18, top: 16, bottom: 30 };
  const series = state.data?.series?.equity || [];
  const points = series.map((row, i) => ({ i, equity: num(row.equity), time: row.time })).filter((p) => p.equity !== null);
  if (points.length < 2) {
    ctx.fillStyle = "#687080";
    ctx.font = "13px system-ui";
    ctx.fillText("No equity series available", pad.left, pad.top + 20);
    return;
  }
  const minY = Math.min(...points.map((p) => p.equity));
  const maxY = Math.max(...points.map((p) => p.equity));
  const ySpan = maxY - minY || Math.max(1, Math.abs(maxY) * 0.01);
  const x = (i) => pad.left + (i / Math.max(1, points.length - 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - ((v - minY) / ySpan)) * (height - pad.top - pad.bottom);
  ctx.strokeStyle = "#dde2ea";
  ctx.lineWidth = 1;
  for (let j = 0; j <= 4; j++) {
    const yy = pad.top + j * (height - pad.top - pad.bottom) / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
  }
  ctx.fillStyle = "#687080";
  ctx.font = "11px system-ui";
  ctx.fillText(maxY.toFixed(2), 6, pad.top + 4);
  ctx.fillText(minY.toFixed(2), 6, height - pad.bottom + 4);
  ctx.strokeStyle = "#1967d2";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, idx) => {
    if (idx === 0) ctx.moveTo(x(idx), y(p.equity));
    else ctx.lineTo(x(idx), y(p.equity));
  });
  ctx.stroke();
  const selectedTime = state.data.timeline[state.index]?.time;
  const nearest = Math.max(0, points.findIndex((p) => p.time >= selectedTime));
  const sx = x(nearest < 0 ? 0 : nearest);
  ctx.strokeStyle = "#b3261e";
  ctx.beginPath(); ctx.moveTo(sx, pad.top); ctx.lineTo(sx, height - pad.bottom); ctx.stroke();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

boot().catch((error) => {
  document.body.innerHTML = `<pre style="padding:16px;color:#b3261e">${escapeHtml(error.stack || error.message || error)}</pre>`;
});
"""
