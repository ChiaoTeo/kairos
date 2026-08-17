# Account 多 Segment 同步、恢复与视图收敛提案

## 1. 文档状态

- 状态：提案，待实施
- 范围：`crates/modules/account` 主 crate、`crates/modules/account/contract`、Account 使用的 Integration account capabilities，以及 Python/CLI 的 Account current-view 读取
- 首个迁移切片：Binance Spot、USD-M Futures、Funding
- 不包含：Execution 订单生命周期重写、Risk 预算模型重写、Reference catalog 重写、通用 provider adapter、跨 provider 的统一交易接口
- 主要目标：让一个外部账户由一个 Account Actor 持有，并在同一 current view 中可靠投影多个 segment；支持“初始快照 + 增量事件 + gap/resync + 定期对账”，同时保留 provider 没有增量能力时的显式 snapshot-only segment

### 1.1 当前实施基线（2026-08-17）

当前 Account 已具备以下基础：

- `AccountActor` 是 Account 可变业务状态的唯一所有者，并以 `SegmentKey` 管理同一 `account_id` 的多个 segment；
- Integration 已提供 Binance Spot、USD-M Futures、Coin-M Futures、Margin、Options、OKX 和 IBKR 的部分 account snapshot/event capability；
- Account Process 已具备初始刷新、私有流消费、provider event ID 去重、channel epoch/sequence gap 检测、有界 recovery buffer、overflow 后触发 resync、重连后等待快照屏障以及 mmap/event publication；
- `AccountCurrentView` 和 `ObservedOrdersCurrentView` 使用 contract-owned FlatBuffers 编码，并通过 replacement mmap 发布；
- live Account facts 只从 Account 自己组合的 Integration capability 进入；Execution 不向 live Account 重复写入 order/fill；
- paper/simulated settlement 使用显式、模式受限、幂等的 simulation command；
- Account binding 已能表达一个逻辑账户下的多个 segment，Binance composition 已能为不同 endpoint family 构造多个 provider/principal context，并汇入同一个 Account Actor；
- current-view publication 已能在同一 mmap payload 中编码同一账户的全部 segment。

当前仍存在以下未收敛问题：

- `initial_refresh_complete`、`refresh_pending`、recovery buffer、resync completion 和部分错误状态仍主要是 Account Process 全局状态，不能准确表达一个 segment 失败而其他 segment 正常；
- 一次全账户 refresh 成功会清理全局 external-event watermarks，并尝试完成全部 stream resync，缺少 segment-scoped barrier；
- Account 级 `observation_mode = "snapshot"` 会关闭全部私有流，是诊断降级手段，不是多 segment 生产同步模型；
- Spot 私有 WebSocket 出现 `Policy: disconnected` 时，健康的 USD-M Futures stream 也会因 Account 级降级路径而被关闭；
- current view 对外只暴露有限的 segment freshness，尚未完整表达 sync mode、completeness、provider watermark、resync reason 和最后成功时间；
- `AccountProjection` 实际表示一个 segment，而 `AccountsSnapshot.accounts` 实际包含同一逻辑账户的多个 segment，命名容易把 segment projection 误解成多个业务账户；
- CLI 文档、命令参数和多账户实例 mmap resource 解析存在漂移，例如读取实例内 Account mmap 时需要显式 `socket-name`；
- provider-specific normalizer、断线、乱序、gap、部分刷新失败和多 segment 恢复测试仍不完整。

## 2. 决策摘要

本提案作出以下目标决策：

1. 一个 broker 上的一个外部账户在 Kairos 中对应一个 `AccountId` 和一个 Account Actor；Spot、Funding、USD-M Futures 等是该账户的 segment，不是多个业务账户。
2. Account Actor 是余额、仓位、权益、freshness、account-side order/fill observations 和 account model 的唯一可变状态所有者。
3. 每个 segment 独立拥有同步模式、生命周期、snapshot watermark、stream epoch/sequence、recovery buffer、freshness 和错误状态。
4. 支持私有流的 segment 使用“流先连接并缓冲 -> 获取权威快照 -> 建立 barrier -> 顺序重放增量 -> Live”的启动路径。
5. provider 没有增量能力的 segment 使用显式 `SnapshotOnly`，不能因为它没有 stream 就伪造一个通用 lifecycle 或空事件流。
6. 周期快照用于初始 bootstrap、对账、gap/overflow 恢复和 freshness 维持；正常低延迟更新来自增量事件。
7. 一个 segment 失败不能停止其他 segment 的 snapshot/event ingestion，也不能删除最后一个已知 current state；失败 segment 必须明确变为 stale/resyncing/unavailable。
8. Account Process health、Account 聚合 readiness 和 segment freshness 是不同语义，不再由一个全局布尔值代替。
9. mmap current view 是 Account Actor 的原子可读投影，不是事实来源、恢复 journal 或 provider payload 缓存。
10. 跨进程 current view 和 business event 必须直接从 application/domain 类型映射到 contract 类型，不经过 JSON model round trip。
11. 不新增 Account-owned `port`、`gateway`、`capability` trait。Account composition 直接使用 Integration 已拥有的稳定 async snapshot/event capability。
12. 按 provider/product/capability 切片迁移；首个完成切片是 Binance Spot + USD-M Futures + Funding，完成后删除 Account 级 snapshot-only 临时路径。

目标信息流如下：

```text
Integration provider-native account capabilities
    ├── Spot snapshot + private events
    ├── USD-M Futures snapshot + private events
    └── Funding snapshot
                 |
                 v
Account segment sync services
    bootstrap / barrier / ordering / resync / freshness
                 |
                 v
Account Actor（唯一状态所有者）
    balances / collateral / positions / equity / observed orders and fills
                 |
          +------+------+
          |             |
          v             v
typed business events   typed mmap current views
          |             |
      Strategy/Risk   CLI/Strategy/Operations
```

## 3. Account 的业务所有权

### 3.1 Account 拥有

- 外部账户身份及其 broker/environment；
- segment 集合及每个 segment 的 account model、margin mode、position mode；
- balance、collateral、position、equity、freshness；
- Account 从自己的 Integration capability 观察到的 order/fill facts；
- provider fact 的去重、顺序、epoch、snapshot barrier 和 resync 结果；
- Account current view 和 Account business event；
- paper/simulated 模式下明确命名的 settlement mutation。

### 3.2 Account 不拥有

- Execution 的 intent、execution plan、exchange-facing order lifecycle、route selection 和 submission audit；
- Risk 的预算、reservation 和准入决策；
- Market 的 quote、trade、order book 和 market freshness；
- Reference 的 canonical Instrument/Market catalog；
- Integration 的认证、签名、HTTP/WebSocket 协议、raw payload、provider session 和技术 quota；
- Workspace/System 的实例路径、全局进程监督和 launch 资源分配。

### 3.3 Live 与 Simulation 的唯一事实入口

Live Account snapshot/order/fill 只能从 Account-owned Integration capability 进入。Execution 即使已经知道一次 order acknowledgement 或 fill，也不能再把同一事实写回 Account。

Simulation 是唯一例外：模拟执行没有外部 Account stream，因此 Execution 可以提交明确的 `SimulatedSettlement` command。该 command 必须：

- 只在 paper/simulated composition 中启用；
- 在 live process 中拒绝；
- 具有稳定 settlement/fill identity；
- 重复提交不重复修改余额和仓位；
- 不伪装成 live provider event。

## 4. 核心领域模型

### 4.1 一个 Account，多 Segment

目标聚合关系为：

```text
AccountId
  broker
  environment
  segments: Map<SegmentKey, AccountSegmentState>
```

一个 Actor 不得持有多个 `AccountId`。同一 `AccountId` 下可以有多个 segment：

```text
spot
funding
cross_margin
isolated_margin:<provider-owned identity>
usd_m_futures
coin_m_futures
options
```

`SegmentKey` 是 Account-owned 业务身份，`provider_product` 是 composition 选择 Integration capability 时使用的外部产品身份。两者不得隐式互相推断。

### 4.2 Segment 同步状态

建议在 Account private services/process 内建立 segment-scoped 状态；它不是新的业务 Actor，也不对外成为 application facade：

```text
SegmentSyncState
  segment_key
  mode
  lifecycle
  snapshot_watermark
  channel_epoch
  provider_sequence
  last_snapshot_at
  last_event_at
  last_success_at
  recovery_buffer
  reconnect_attempt
  last_error
```

同步模式最少包含：

- `SnapshotThenStream`：权威快照 bootstrap，之后消费有序增量；
- `SnapshotOnly`：provider 当前没有对应增量 capability，按 cadence 刷新权威快照。

不引入一个可以容纳任意 provider 行为的通用 strategy/adapter trait。模式来自 composition 已选择的真实 Integration capability 组合。

### 4.3 Segment 生命周期

最小生命周期为：

```text
Configured
  -> Bootstrapping
  -> Live | SnapshotCurrent
  -> Degraded
  -> Resyncing
  -> Live | SnapshotCurrent
  -> Unavailable
  -> Stopped
```

语义如下：

- `Configured`：binding 有效，但尚未开始 provider I/O；
- `Bootstrapping`：stream 可先连接并缓冲，但权威快照 barrier 尚未建立；
- `Live`：增量流已认证、连续，且已经通过快照 barrier；
- `SnapshotCurrent`：snapshot-only segment 最近一次权威快照成功且未过 freshness deadline；
- `Degraded`：仍有可读的最后已知状态，但出现可恢复错误；
- `Resyncing`：发生 gap、overflow、epoch change 或 reconnect，正在重新获取 snapshot 并重放事件；
- `Unavailable`：没有可接受的 bootstrap，或失败已超过 freshness/重试政策；
- `Stopped`：进程停止，不继续 I/O。

### 4.4 Freshness 与 Completeness

Freshness 与生命周期分离：

- `Fresh`：满足该 segment sync mode 的时间和 continuity 条件；
- `Stale`：保留最后已知状态，但超过 freshness deadline；
- `Resyncing`：状态正在恢复，不能用于要求连续事实的决策；
- `Unavailable`：没有可解释的 current state；
- `Unknown`：尚未完成 bootstrap。

Completeness 最少区分：

- `Complete`：权威全量 snapshot 或已经通过 barrier 的 current projection；
- `Partial`：provider 明确返回 delta/部分字段；
- `Unknown`：不能证明是否完整。

Partial snapshot 只能按该类型定义的 merge 规则应用，不能把未出现的余额或仓位自动解释为删除。

## 5. 快照与增量同步算法

### 5.1 启动 Bootstrap

`SnapshotThenStream` segment 的启动顺序：

1. composition 创建 provider-native snapshot handle 与 event stream；
2. Process 连接 event stream，记录新的 channel epoch，并将事件写入该 segment 的有界 recovery buffer；
3. Process 获取该 segment 的权威 full snapshot；
4. snapshot 通过 Integration normalized fact 映射为 Account-owned snapshot；
5. Account Actor 原子应用 snapshot；
6. 根据 provider 能力确定 snapshot barrier，丢弃 barrier 之前的重复事件；
7. 按 provider sequence 顺序重放 barrier 之后的缓冲事件；
8. buffer 无 gap、无 overflow 且 stream healthy 时，将 segment 标为 `Live/Fresh`；
9. 发布新的 business events 和 replacement mmap current view。

如果 provider snapshot 与 stream 没有可比较的 sequence，Integration 必须定义该 provider 的安全握手顺序，例如在 snapshot 后建立新 stream epoch。Account 不自行猜测 vendor 协议。

### 5.2 稳态增量

稳态事件必须携带或可推导：

- binding/source identity；
- segment identity；
- channel ID 与 channel epoch；
- provider event ID；
- provider sequence（provider 提供时）；
- provider observed time 与 received time；
- normalized snapshot/order/fill/change payload。

Account Process 先完成去重和 continuity 检查，再映射为 Account domain event。Actor transition 成功后：

1. 持久化需要 durable 的业务事实；
2. 更新 Actor generation/event sequence；
3. 发布 typed business event；
4. 原子替换 current-view mmap。

事件 publication 失败不能回滚已提交 Actor 状态，也不能阻止 current view publication；应通过 bounded retry/outbox 语义补发。

### 5.3 Gap、Overflow 与 Reconnect

以下情况使对应 segment 进入 `Resyncing`：

- provider sequence gap；
- channel epoch 非预期变化；
- consumer queue overflow；
- recovery buffer overflow；
- normalizer 无法映射关键 identity；
- stream disconnect 后重新认证；
- provider 明确要求重新获取 snapshot。

恢复只影响该 segment：

1. 保留最后已知 Account state，但标为 resyncing/stale；
2. 停止直接应用该 segment 新事件，改为有界缓冲；
3. 获取新的权威 snapshot；
4. 建立新的 barrier；
5. 重放可证明位于 barrier 之后的事件；
6. 成功后恢复 `Live/Fresh`；
7. 失败则使用指数退避和 jitter 重试，并在超过政策后标为 unavailable。

不得在一个 segment resync 完成后清空其他 segment 的 watermark、buffer 或错误状态。

### 5.4 周期快照与对账

对 `SnapshotThenStream` segment，周期快照不是正常增量路径的替代品，而用于：

- provider stream 无法覆盖的字段；
- 状态漂移检测；
- freshness 证据；
- gap/overflow 恢复；
- 运维显式 reconcile。

快照与 current state 有差异时，应产生可观测 reconciliation result。是否发布 balance/position change event 取决于差异是否代表新的 Account 业务事实，不能通过序列化前后 JSON 做 diff。

### 5.5 Snapshot-Only Segment

Funding 等没有真实增量 capability 的 segment：

- 使用独立 cadence；
- 一个慢请求不能阻塞 Spot/Futures；
- 连续失败达到阈值后打开该 segment circuit breaker；
- 旧状态在 freshness deadline 前仍可读；
- 超时后标为 stale/unavailable，但不删除旧状态；
- snapshot 成功后原子应用并恢复 `SnapshotCurrent/Fresh`。

## 6. Readiness、部分故障与 Launch 依赖

必须区分三种状态：

1. Process liveness：Account process 是否存活、control/mmap resource 是否存在；
2. Account aggregate readiness：该账户是否至少完成合法 bootstrap，是否存在不可接受的必需 segment 故障；
3. Segment readiness/freshness：单个 segment 是否能被当前业务决策使用。

一个 segment 失败时：

- 其他 segment 继续接收 snapshot/event；
- current view 继续发布全部 segment；
- 失败 segment 保留最后已知 state 并携带 freshness/error；
- Account aggregate 可以是 `degraded`，而不是把所有 segment 伪装成 unavailable；
- Launch 是否可启动由它声明的 required segments 决定，而不是由 Account 猜测策略需求。

建议的 launch 输入是：

```toml
[accounts.primary]
ref = "main-binance"
enabled = true
required_segments = ["usd_m_futures"]
```

`required_segments` 属于 launch/application composition 的依赖声明；它不会创建第二份 Account 状态，也不会改变 Account binding 的 segment 集合。

## 7. Application 与 Contract 边界

### 7.1 Application API

Account application 是唯一公开业务 facade，应提供业务请求/结果：

- 读取一个逻辑账户的完整 current view；
- 按 segment 读取 balance/position/current quality；
- 显式触发 refresh/reconcile；
- paper/simulated settlement；
- process facade 的 lifecycle/health/event draining。

Application 不暴露：

- SDK client；
- raw Binance payload；
- persistence record；
- concrete stream/snapshot service；
- composition connection；
- application-owned capability trait。

跨模块、CLI 和 Strategy 通过 Account application API 或 `kairos-account-contract` 读取，不导入 `services/`。

### 7.2 Current View

一个 `AccountCurrentView` 对应一个 `AccountId`，包含多个 segment：

```text
AccountCurrentView
  metadata
    generation
    applied_event_sequence
    published_at
    completeness
  account_id
  segments[]
    segment_key
    account_model
    sync_mode
    lifecycle
    freshness
    state_generation
    snapshot_watermark
    event_watermark
    observed_at
    last_success_at
    last_error
    balances[]
    collateral[]
    positions[]
    equity
```

必须保持：

- payload 中所有 segment 属于同一 `account_id`；
- mmap envelope generation 与 FlatBuffers metadata generation 一致；
- applied revision 等于 Account Actor 已提交 event sequence；
- replacement publication 原子完成，reader 不会看到半个 generation；
- current view 与 observed-orders view 使用不同 resource key；
- JSON 只用于显式 control/config/diagnostic 边界。

`AccountProjection` 应逐步收敛为语义明确的 `AccountSegmentView`；`AccountsSnapshot.accounts` 应收敛为一个 aggregate view 中的 `segments`，避免把 segment 集合误解为多个账户。

### 7.3 Business Event

Account business event 表达 Actor 已接受的业务事实变化，至少包含：

- Account Actor event sequence；
- account/segment identity；
- change kind 与 typed payload；
- provider provenance；
- provider observed/received time；
- source binding、event ID、sequence/epoch（存在时）。

Snapshot publication 不反向合成 business events。Event 必须来自 Actor transition；重复 snapshot publication 不产生重复业务事件。

## 8. Persistence 与恢复

Account persistence 需要明确区分：

- current state checkpoint：用于加速进程重启；
- durable business facts：simulation settlement、observed fill 和必须审计的 account-side order/fill change；
- replaceable provider projection：可以在重启后通过权威 snapshot 重建；
- publication outbox：确保已提交业务事件能够补发。

重启流程：

1. 恢复本地 checkpoint/journal，发布状态只能是 restoring/stale；
2. 为每个 segment 重新建立 provider capability；
3. 完成该 segment bootstrap/resync；
4. 只有 provider 证据满足条件后才恢复 fresh；
5. 分配新的 mmap producer incarnation，不能沿用旧 writer lease；
6. generation/event sequence 保持单调并可解释。

不要求把每个高频 provider snapshot 完整写入永久 journal，但必须在设计与测试中明确哪些事实可重建、哪些事实必须 durable。不能仅因当前测试方便而让 live fill/order audit 依赖下一次 REST snapshot。

## 9. Integration 边界与 Binance 首个切片

### 9.1 Integration 提供

- provider-native authenticated connection/principal context；
- async snapshot handle；
- async private event stream；
- provider payload normalization；
- provider channel health、epoch、sequence 和 delivery facts；
- 安全的 reconnect/resubscribe 机制；
- provider-specific snapshot/stream barrier 语义。

### 9.2 Account 提供

- segment 业务身份；
- capability 的业务组合；
- bootstrap/resync/freshness；
- Account domain mapping 与 Actor transition；
- Account current view、business event 和 health；
- launch 所需账户资源的运行时实例。

### 9.3 Binance 切片

第一切片固定为：

| Segment | Snapshot | Incremental stream | 目标模式 |
|---|---|---|---|
| Spot | Binance Spot account snapshot | Spot private account events | SnapshotThenStream |
| USD-M Futures | Futures account snapshot | Futures private account events | SnapshotThenStream |
| Funding | Funding wallet snapshot | 当前无真实 capability | SnapshotOnly |

该切片完成前必须：

- 修复并记录 Spot WebSocket `Policy: disconnected` 的根因；
- 证明 Spot 和 Futures 可在同一 Account Actor 下独立 bootstrap、独立断线、独立恢复；
- Funding 刷新失败不影响 Spot/Futures stream；
- 更新 `docs/integration-adapter-references/binance-spot.md` 和 `binance-usdm-futures.md`，记录协议、上游参考、恢复行为、测试和 license；
- 删除 Account 级 `observation_mode = "snapshot"` 临时路径。

后续按 Coin-M、Margin、Options、OKX、IBKR 分切片迁移，不先创建 universal provider/session registry。

## 10. 配置与运维界面

Account binding 只描述账户和 segment，不描述策略需求：

```toml
[account]
id = "main-binance"
broker = "binance"
integration_provider = "binance"
environment = "live"

[segments.spot]
product_family = "spot"

[segments.usd_m_futures]
product_family = "usd_m_futures"

[segments.funding]
product_family = "funding"

[credentials.default]
ref = "binance-readonly"
role = "readonly"
```

同步模式默认从真实 capability 推导；只有 provider 同时存在两种生产语义并需要用户选择时，才允许显式配置。不能用 Account 全局 `snapshot_only` 掩盖一个 stream adapter 故障。

CLI 最少应稳定支持：

```text
account show <account>
account doctor <account>
account current <account>
account balances <account> --segment ... --include-zero
account positions <account> --segment ...
account observed-orders <account> --segment ...
account refresh <account> --segment ...
account reconcile <account> --segment ...
```

CLI 对运行时 mmap 的定位应通过 launch instance manifest 和 account identity 解析，不要求用户理解内部 `socket-name`。直接 provider query 与运行中 Actor current-view query 必须使用不同、明确的命令命名。

Health/diagnostic 输出至少包含：

- account/process identity；
- 每个 segment 的 mode/lifecycle/freshness；
- snapshot age、event age、provider sequence、channel epoch；
- recovery buffer depth、consumer queue depth；
- reconnect attempts、last error；
- last successful refresh duration；
- mmap generation/event sequence/producer incarnation；
- credential role 和权限摘要，但绝不输出 secret。

## 11. 安全要求

- credential secret 不进入 CLI 输出、日志、Debug、health、state、mmap 或 crash report；
- read-only credential 永远不能启用 trade access，即使 provider 返回 trade permission；
- writable credential 仍需显式 role、provider permission 和 trade lease 同时满足；
- Account binding 不保存 raw API key；只保存 credential reference；
- diagnostic 工具默认只显示 credential ID/role，不读取或打印 secret；
- tests 使用 fake/ephemeral credential，不把真实密钥写入 fixture 或快照。

## 12. 目标代码结构

目录调整只在真实职责迁移时创建，不预先生成空模块：

```text
crates/modules/account/
  contract/
    src/
      view/
      event/
      control/
      encode/

  src/
    bin/
      kairos-account-server.rs
      kairos-account-cli.rs

    composition/
      mod.rs
      bindings/
      providers/
        binance.rs
        okx.rs
        ibkr.rs
      publication/
        current_view.rs
        observed_orders.rs
        events.rs

    application/
      mod.rs
      model/
        command.rs
        query.rs
        result.rs
        event.rs
        error.rs
      process/
        mod.rs
        lifecycle.rs
        ingress.rs
        refresh.rs
        recovery.rs
        readiness.rs
        publication.rs

    services/
      actor/
        mod.rs
        balances.rs
        positions.rs
        observations.rs
      synchronization/
        mod.rs
        segment.rs
        bootstrap.rs
        continuity.rs
        recovery.rs
        freshness.rs
      persistence/
      settlement/

    domain/
      mod.rs
      account/
      segment/
      balance/
      position/
      observation/
```

该结构不是文件创建清单。迁移一个职责时必须删除对应旧实现；不得长期保留 `application/process.rs` 与 `application/process/` 两套路径，也不得创建空 re-export 目录。

## 13. 分阶段迁移计划

### Phase 0：固定基线与设计

1. 记录当前 Account file tree、trait、public export、provider slice 和测试基线；
2. 分类每个 snapshot/event publisher、JSON boundary 和 cross-module caller；
3. 更新 Binance adapter reference notes；
4. 固定本文为 Account 同步与 current-view 语义的架构权威。

退出条件：不存在未分类的 live fact ingress、publisher 或 Account-owned mutable state。

### Phase 1：命名与 Aggregate View

1. 将 per-segment `AccountProjection` 收敛为 `AccountSegmentView`；
2. 将 current aggregate 明确为一个 Account + 多 segments；
3. current/observed-orders mmap 编码全部 segment；
4. 增加相同 account ID、generation、applied revision 和 completeness 校验；
5. 修复 CLI instance/account mmap resource 自动解析。

退出条件：所有 reader 都能从一个 Account current view 读取全部配置 segment，不依赖内部状态文件。

### Phase 2：Segment-Scoped Sync State

1. 引入 private `SegmentSyncState`；
2. 将 refresh pending、initial bootstrap、recovery buffer、watermark 和 error 按 segment 管理；
3. 将全局 refresh completion 改为逐 segment completion；
4. 每个 segment 独立 freshness/circuit/retry；
5. aggregate health 从 segment states 派生。

退出条件：一个 segment 的失败、刷新或 resync 不清除或阻塞其他 segment 的同步状态。

### Phase 3：Binance Spot + USD-M Barrier

1. 明确两个 provider 的 snapshot/stream handshake；
2. stream 先连接并有界缓冲；
3. snapshot 建立 barrier 后顺序重放；
4. gap、epoch change、overflow 和 reconnect 触发 segment resync；
5. 修复 Spot `Policy: disconnected`；
6. Funding 保持独立 SnapshotOnly。

退出条件：三个 segment 在同一 Actor 中正常工作，Spot 或 Futures 单独断线不会停止另一条流或 Funding 刷新。

### Phase 4：Persistence、Event 与 Publication

1. 分类 durable business fact 与 replaceable provider projection；
2. 固定 outbox/retry 行为；
3. business event 只从 Actor transition 产生；
4. current view、observed orders 和 event publication 使用 typed direct mapping；
5. 重启时恢复 generation/event sequence，并通过新 provider bootstrap 恢复 freshness。

退出条件：crash/restart、publisher failure 和 duplicate provider fact 不造成重复状态修改或不可解释的事件丢失。

### Phase 5：Readiness 与运维

1. current view 增加 segment sync metadata；
2. launch 支持 `required_segments`；
3. health/doctor 显示 segment lifecycle、watermark、lag 和 error；
4. CLI 统一 direct-provider query 与 runtime-view query；
5. 删除 Account 级 snapshot-only workaround。

退出条件：用户可以解释“数据来自哪里、是否完整、最后更新何时、是否正在 resync、为什么 launch 可用或不可用”。

### Phase 6：后续 Provider Slices 与清理

依次迁移 Coin-M、Margin、Options、OKX 和 IBKR。每个切片完成时：

- 新路径通过 focused tests；
- 旧 registry/compatibility/sync path 删除；
- adapter reference note 更新；
- 不保留没有当前调用者的抽象。

## 14. 测试与验证矩阵

### 14.1 Domain/Actor

- full snapshot 替换与 partial snapshot merge；
- zero balance/zero position 的删除语义；
- duplicate/stale snapshot；
- account model/margin mode/position mode transition；
- Hedge Mode 下同 instrument long/short identity 不冲突；
- duplicate fill/settlement 幂等；
- live observed fill 不执行 simulation settlement。

### 14.2 Synchronization

- stream event 在 snapshot 前、期间、之后到达；
- snapshot barrier 前事件丢弃，之后事件顺序重放；
- duplicate event ID；
- provider sequence 重复、乱序、gap；
- channel epoch rollover；
- consumer queue/recovery buffer overflow；
- reconnect 成功但 snapshot resync 未完成时保持 degraded；
- 一个 segment resync，其他 segment 继续更新；
- SnapshotOnly timeout/circuit/freshness recovery。

### 14.3 Contract/Publication

- multi-segment current-view FlatBuffers round-trip；
- envelope/view generation 与 applied revision 一致；
- atomic replacement、writer fencing、producer incarnation；
- observed-orders 独立 resource；
- event publication failure 不阻塞 snapshot publication；
- JSON adapter 不进入 business event/current view 路径。

### 14.4 Process/Integration

- Binance Spot、USD-M、Funding focused normalizer；
- credential role/permission/trade lease；
- process restart 与 provider rebootstrap；
- partial provider outage；
- bounded retry/backoff；
- slow segment 不阻塞其他 segment；
- 真实 provider sandbox/smoke test 只在显式环境下运行，不进入默认单元测试。

### 14.5 Handoff Checks

每个阶段至少运行：

```bash
cargo test -p kairos-account
cargo test -p kairos-account-contract
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

完成切片前还需运行：

```bash
cargo test --workspace
uv run pytest -q
```

静态审计必须覆盖：

```bash
rg "services::|/services/" crates/modules --glob '*.rs'
rg "serde_json::(to_value|from_value)" crates/modules/account
rg "trait .*Port|trait .*Gateway|trait .*Capability" crates/modules/account/src/application
rg "snapshot_only|observation_mode" crates/modules/account
rg "ExecutionAccountFacts|order-event|simulated-fill" crates/modules/account crates/modules/execution
```

## 15. 非平凡抽象检查

本文提出的 `SegmentSyncState` 不是 public capability、port、manager 或第二个 Actor。它只承载当前 Process 已经存在、但错误地聚合在全局的 segment 同步状态。

在引入该内部模型前，问题回答如下：

1. **现在解决什么具体问题？** 多 segment 的 bootstrap、gap、resync、freshness 和错误互相污染，一个 segment 失败会导致整个 Account 降级或关闭健康流。
2. **当前调用者是谁？** `AccountProcess` 的 refresh、stream ingress、health、recovery 和 publication 路径。
3. **现有边界为什么不足？** `AccountActor` 只应拥有业务状态，不能承载网络连接和 recovery buffer；现有 Process 全局字段无法表示 segment 独立状态。
4. **保持边界的最简单实现是什么？** 在 private services/process 中把现有字段按 `SegmentKey` 分组，不新增 application trait 或新的状态 owner。
5. **迁移后删除什么？** 全局 `initial_refresh_complete`、全局 recovery buffer、全局 resync completion、Account 级 snapshot-only workaround 以及依赖它们的兼容判断。
6. **如何证明有用？** Spot 断线而 USD-M/Funding 保持 fresh 的测试；单 segment gap/resync 测试；三个 segment current view 与 live smoke test。

## 16. 非目标与反过度设计规则

本提案不允许：

- `AccountManager`、`SegmentCoordinator`、通用 session registry；
- Account application 自己定义 Integration capability mirror trait；
- 为每个 segment 创建 Actor；
- 将 provider raw payload 或 SDK client 暴露到 application；
- 把 Execution order lifecycle 搬入 Account；
- 让 Funding 伪装成有增量事件的 stream；
- 为测试 fake 保留没有生产多实现的 public trait；
- 创建 universal `execute(operation, payload)` provider facade；
- 通过 JSON serialize/deserialize round trip 映射业务 snapshot/event；
- 在新路径通过后长期保留旧 compatibility path。

## 17. 完成定义

Account 模块完善至少满足：

1. 一个外部账户由一个 Account Actor 持有全部配置 segment；
2. Spot 与 USD-M 使用 snapshot + incremental + resync，Funding 使用明确 SnapshotOnly；
3. 每个 segment 独立 bootstrap、freshness、health、buffer、watermark 和恢复；
4. segment 部分故障不停止其他 segment，也不丢弃最后已知 current state；
5. current mmap 原子包含完整多 segment view 和可解释的质量元数据；
6. business events 来自 Actor transition，具有 Account event sequence 和 provider provenance；
7. restart、gap、overflow、duplicate、publisher failure 和 provider outage 均有测试；
8. read-only credential 不能获得交易能力；
9. live Account facts 只有 Account-owned Integration ingress；
10. 临时 Account 级 snapshot-only 路径及旧兼容概念已删除；
11. Binance 首个切片通过 focused、contract、workspace 和 Python checks；
12. 用户可以通过 CLI/Strategy 明确看到每个 segment 的余额、仓位、freshness、watermark 和错误原因。
