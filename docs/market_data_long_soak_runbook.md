# 实时行情 24–72 小时长稳验收 Runbook

本文用于验收公共实时行情链路，不涉及账户凭据和下单权限。执行链路为：

```text
Binance WebSocket
  -> Raw Journal
  -> Canonical Event
  -> Bounded Channel
  -> Rotating Canonical Capture
  -> Segment Manifest
  -> Soak/Campaign Artifact
  -> Deterministic Replay
```

## 标准命令

24 小时主动重启验收：

```bash
./pyenv/bin/python -m kairospy \
  --lake-root data/market-data-soak \
  data soak-binance \
  --symbol BTCUSDT \
  --channel bookTicker \
  --duration-seconds 86400 \
  --restart-interval-seconds 21600 \
  --minimum-events 100000 \
  --maximum-silence-seconds 5 \
  --maximum-channel-utilization 0.9 \
  --capture-segment-events 100000 \
  --capture-segment-bytes 268435456 \
  --capture-total-bytes 21474836480
```

72 小时验收将 `--duration-seconds` 改为 `259200`。重启间隔 `21600` 表示每 6 小时主动关闭并重新建立一个完整 Stream Session；每个 Session 使用独立 Raw Journal、Canonical Segment Manifest 和 Leg Artifact。

## 绑定 Live View Freshness

如果本次 soak 是为了批准某个 Strategy paper/live 使用的 Live View，先用 `kairospy data write --live --connector ...`
生成 Live View manifest，再把 manifest 路径传给 soak 命令：

```bash
./pyenv/bin/python -m kairospy \
  --lake-root data/market-data-soak \
  data soak-binance \
  --symbol BTCUSDT \
  --channel bookTicker \
  --duration-seconds 86400 \
  --minimum-events 100000 \
  --maximum-channel-utilization 0.9 \
  --live-view-manifest data/market-data-soak/live-views/<dataset>/<live-view-id>/manifest.json
```

命令会把审计后的 `freshness_status`、`channel_diagnostics` 和 `freshness_evidence` 写回该 manifest。若
channel diagnostics 中出现 drop、overflow 或 sequence gap，manifest 会被标记为 `unhealthy`。后续
`run start --mode paper|live` 会要求同一 DataSet contract 下存在 healthy Live View，并且这些 channel
failure 均为 0。

## 资源边界

- Soak 统计保持 O(1) 内存，只保存上一条事件和聚合计数，不在内存中保留完整事件列表。
- Canonical Capture 同时受单段事件数、单段字节数和总字节预算约束。
- 超过总磁盘预算产生 `CaptureResourceExceeded`，Producer 失败并使 Artifact 不通过。
- Bounded Channel 记录 peak depth；发生 drop 或峰值利用率超过门限时验收失败。
- 每个 Segment 都有 event count、时间范围和内容 SHA-256；Rotation Manifest 对 Segment 列表再次计算哈希。

## 单段通过条件

- 实际持续时间达到目标；
- 事件数达到最低门限；
- Raw 数量等于 Canonical 数量；
- ignored message 为 0；
- source sequence 无回退；
- maximum interarrival 和 tail silence 不超过门限；
- Channel drop 为 0；
- peak channel utilization 不超过门限；
- Producer 没有异常；
- Capture 未超过磁盘预算。

## Restart Campaign 通过条件

- 至少完成两个独立 Stream Session；
- 每个 Leg Artifact 单独通过；
- restart count 与计划一致；
- 相邻 Session 的 source sequence 不回退；
- 总事件数达到 Campaign 门限；
- Campaign Artifact 包含全部 Leg Artifact 路径和 audit hash；
- `restart_drill_passed` 和 `passed` 都为 true。

## Replay 核验

每个 `*.rotation.manifest.json` 使用 `RotatingCapturedCanonicalEventSource` 重放。重放器会再次检查：

- Rotation Manifest hash；
- 每段 Canonical Manifest 和文件 hash；
- 跨段全局事件顺序；
- 跨段重复 message ID；
- Replay event count 与 Manifest 声明一致。

## 当前短时外部证据（2026-07-17）

```text
Rotation smoke:
  duration: 10.133146 seconds
  events: 1315
  segments: 27 (forced at 50 events)
  channel peak: 1 / 4096
  dropped: 0
  replayed: 1315 / 1315
  audit: 91e91f5d43ce9b4d5cc43a4f70270f983e49be634892fac638a0bb9d2598c4e4

Restart campaign smoke:
  duration: 12.651622 seconds
  sessions: 3
  active restarts: 2
  events: 918
  boundary sequence regressions: 0
  restart_drill_passed: true
  audit: c3dbea1883494a4f3953357c7248bcf990235caf68b7fe1aaf1c3eb300e1a4f9
```

上述结果证明 rotation、资源门禁和 restart 机制可工作，不替代 24–72 小时持续运行的最终时间门槛。
