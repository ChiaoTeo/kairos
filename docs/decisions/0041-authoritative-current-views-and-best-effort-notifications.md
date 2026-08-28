# Decision 0041：权威 Current View 与 Best-effort Notification

- Status: Accepted
- Date: 2026-08-28
- Refines: [Decision 0034](0034-unified-current-view-storage.md)
- Supersedes in part: [Decision 0039 section 6](0039-workspace-and-instance-event-transport-routes.md#6-顺序连续性按-producer-incarnation-分区)
- Scope: Market、Account、Execution、Risk、Capital 的 LMDB current view、Aeron publication 与 Python consumer

## Context

运行模块同时通过 LMDB 和 Aeron 对外提供数据。如果 consumer 把 LMDB metadata 中的
`applied_event_sequence` 当成 Aeron replay cursor，或在 sequence gap / producer restart 后把 current
view 伪装成事件 backlog，会产生一套实际上无法兑现的恢复协议：

- Aeron publication 没有持久 retention，subscriber 未连接、队列溢出或进程暂停时可以错过消息；
- LMDB 只保存 keyed current state，不保存被覆盖的中间变化、event-only fact 或完整顺序；
- 一个 current-view transaction 可以合并多个 owner transition，同一 sequence 也可能没有 view mutation；
- producer restart 改变 incarnation，但读取 current view 不能重建旧 incarnation 中缺失的通知；
- Python 将 notification payload 转成 owned object 是合理的异步生命周期边界，但再维护
  notification-derived “latest” cache 会建立第二份、可能缺失的当前状态。

Market 的高频路径还存在额外放大：一次 Actor drain 中的每个变化分别编码完整 current value、开启并提交
一个 LMDB write transaction；order-book delta 每次都会把完整盘口 clone 后再次完整编码。这抵消了 mmap
current view 的主要收益。

## Decision

### 1. LMDB owner current view 是唯一权威当前状态

Market、Account、Execution、Risk 和 Capital 的跨进程“现在是什么”只由 owner-scoped LMDB current
view 回答。Actor/持久 journal 仍是 owner 内部恢复源；LMDB 是可重建的权威读取面，不反向恢复 Actor。

`applied_event_sequence` 的含义收窄为“本次提交反映的最高 owner state sequence”。它用于新鲜度、诊断、
bounded wait 和比较两次 current read，不是 Aeron cursor、replay token 或 snapshot-to-stream join point。
Value 自带的 source event identity 也只是证据，不承诺所有中间通知可读。

需要历史、audit 或逐事件重放的调用方必须使用 owner 明确定义的 durable query/journal/dataset contract；不得
从 current view 猜测历史。

### 2. Aeron 只承载有界、不可重放的 best-effort notification

运行时 Aeron business publication 是低延迟变化通知。它可以携带完整 typed event payload，让对单条实时
事实感兴趣的 consumer 直接处理；但 payload 不拥有当前状态，也不升级为 durable delivery。

统一规则如下：

- subscriber 可以从任意收到的 sequence 建立观察位置；首次消息不要求从 1 开始；
- 同一 `(logical_stream_id, producer, producer_incarnation)` 内的 sequence 用于去重和检测漏通知；
- gap 增加独立健康计数并继续接收后续通知，不触发 snapshot-as-replay，也不宣称已恢复缺失事件；
- incarnation 改变会重置该 notification 观察位置并增加独立计数，不要求 current view 生成新 cursor；
- route、workspace、launch、instance 和业务 scope 错误仍 fail closed，因为它们表示拓扑错接而非普通丢通知；
- queue overflow 关闭并重建 subscription，同时报告 notification loss；是否显式重读所需 current key 由
  consumer use case 决定；
- deterministic replay、durable audit 等其他数据源可以声明严格连续性，但不能复用 live Aeron 的语义名称。

Consumer 若收到 notification 后需要当前状态，显式按 owner key 读取 current view。它可以基于
`applied_event_sequence` 或 value evidence 做有界重试；超时报告 view stale，不回退到 notification cache。

### 3. Current view 先提交，notification 后发布

同一 publication flush 的顺序统一为：

```text
Actor transition / durable owner state
  -> encode and atomically commit affected LMDB current values
  -> attempt Aeron notification publication
  -> record publication/drop/queue health
```

LMDB commit 失败按 owner freshness policy fail closed。Aeron 没有 subscriber、暂时不可用或队列丢失不会
撤销已经成立的业务状态，也不会让 command 伪装成业务失败；notification 失败必须可观测并被有界丢弃，
不能无限堵塞 owner outbox。对于没有 current value 的瞬时 Market fact，只发布 notification；调用方明确
接受它可能完全错过。

这不是 LMDB 与 Aeron 的原子双写协议。进程可能在 commit 后、publish 前退出；结果是 current view 正确而
notification 缺失，符合本 Decision。

### 4. Market current-view flush 使用 latest-wins batch

Market 每次 publication drain 最多处理有界数量的 `MarketChange`。在 FlatBuffers 编码前，按 contract-owned
canonical `MarketViewKey` 做 latest-wins 合并：

- 每个 key 只编码 drain 中最后一个 current value；
- 所有保留的 put/delete 和 metadata 在一个 LMDB write transaction 中提交；
- transaction watermark 是保留变化的最高 owner state sequence；
- event-only change 不创建空 current transaction；
- 指标分别记录 input updates、encoded updates、coalesced updates、mutation count、commit latency 和错误。

Order-book actor state继续增量应用 delta。Publication 不为同一 drain 中已被后续 delta 覆盖的中间盘口 clone
或编码完整 levels；只为最终保留的 book key 物化一次 bounded current value。盘口同步、provider sequence、
checksum 和 resync-required 是 Market domain 健康，不与 Aeron notification gap 混为一个指标。

### 5. Python 高频边界批量化，但返回值仍为稳定 owned object

Python 不持有 LMDB transaction、mmap pointer、Aeron fragment pointer 或 generated FlatBuffers lifetime。
Current-view keyed read 在短 LMDB transaction 内直接借用 value bytes 完成校验和投影，随后返回稳定 owned
Python object；不得先复制到中间 `Vec<u8>` 再解码。

Live Market notification 的推荐接口一次 native poll 解码一批 typed owner objects，再以一个 Python list / tuple
跨越 GIL 边界。单条 async iterator 保留为便利封装，但内部消费 batch，不为每条 payload重复
`to_thread`、Rust `Vec -> PyBytes -> Rust decode -> PyObject` 往返。性能认证必须同时报告吞吐、p50/p99、batch
size、queue overflow、Rust/Python allocation 和 CPU；只比较单次函数耗时不构成认证。

### 6. 不建立第二套状态或通用恢复抽象

- 删除 notification-derived latest trade/quote/order/risk cache；没有 current-view contract 的值就没有
  `latest_*` 权威 API。
- 不增加通用 event manager、recovery coordinator、owner-independent cursor protocol 或 snapshot adapter。
- 各 owner 保留自己的 key、view freshness 和 business reconciliation；共享 platform 只负责 LMDB transaction、
  Aeron frame 与 transport health。

## Consequences

- Consumer 不再因为普通 live gap 停止交易循环，但可以通过独立 loss/incarnation 指标实施告警或降级。
- Current view 与 notification 不会再被误解成一套可原子 join、可恢复的 event log。
- 需要每个 transition 的 Execution/Account/Risk/Capital 调用方必须依赖 durable owner capability，而不是提高
  Aeron queue 后继续假设 exactly-once。
- Market 热路径以一次 drain、一次按 key 合并编码、一次 LMDB transaction 为成本边界；高频 Python 用户走
  native batch，便利 iterator 只承担包装成本。
- Python 最终对象仍然 owned。这一份生命周期复制是跨线程、跨 transaction 和跨 GIL 的安全边界；应消除的
  是此前的中间 byte copies、逐条 GIL 往返和重复完整盘口物化。

## Verification

- owner publication tests 证明 view commit 先于 notification attempt，且 notification drop 不回滚状态；
- consumer tests 证明首次任意 sequence、gap、duplicate 和 incarnation change 的统一行为；
- architecture tests 禁止 current-view-as-recovery API 和 notification-derived authoritative cache；
- Market tests证明 same-key latest-wins、跨 key 单 transaction、event-only no-op 和最终 order-book 编码；
- Rust/Python contract tests证明短 transaction 后 owned object 仍稳定；
- criterion/pytest benchmark 固定 baseline、输入分布、batch size 和回归阈值，并分别报告 view 与 notification
  健康指标。
