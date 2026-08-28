# Decision 0039：Workspace 与 Instance 事件传输路由

- Status: Accepted; section 6 superseded by [Decision 0041](0041-authoritative-current-views-and-best-effort-notifications.md)
- Date: 2026-08-28
- Scope: Aeron business-event publication, Run Instance isolation, shared-service fan-out
- Extends: [Decision 0001](0001-workspace-resource-layout.md),
  [Decision 0017](0017-run-plans-instances-and-operations-center.md)

## Context

`schemas/transport.toml` 为 Reference、Market、Account、Execution、Risk 和 Capital 分配固定的
Aeron stream ID，生成的 Rust/Python transport spec 同时提供一个固定默认 channel。固定 stream ID
能够区分 owner contract 的消息类别，但不能区分同时运行的 Workspace 或 Run Instance。

当前 instance-owned publisher 和 Python subscriber 共用同一个 Workspace Media Driver，并在没有显式
配置时回退到同一个 `DEFAULT_CHANNEL`。这会让一个订阅收到其他 Instance 的 publication：

- instance Market 在校验订阅需求前就会拒绝其他 `launch_id / instance_id` 的事件；
- 忽略外部事件也不能修复顺序，因为不同 Actor 的独立 sequence 已经在同一订阅中交错；
- Account 虽按 `account_id` 过滤并按账户维护 cursor，但相同账户身份或错误接线仍会混入其他
  Instance 的 producer；
- Execution、Risk 和 Capital 具有相同的路由风险。

Reference 的生命周期不同。一个 Workspace 只有一个权威 Reference Actor、catalog、current view 和
publisher；Run Instance 只连接它，不拥有或复制 Reference。Market 可以是 Workspace 共享进程，也可以
按运行方案选择 instance scope。Account、Risk、Execution、Capital 和 instance Market 由 Run Instance
拥有。

因此，payload identity 校验不能承担传输隔离职责，动态创建 `MARKET_EVENTS_1`、
`MARKET_EVENTS_2` 也会把稳定 contract 类别与运行资源分配错误地耦合。

## Decision

### Conflux 的边界

Conflux 继续作为 owner contract 的事件数据面：它在调用方提供的 `aeron_dir`、`channel` 和固定 stream ID
上建立 publisher/subscriber，完成 frame 传输、解码和分发。这个机制足以承载本 Decision 所需的数据流，
不需要让 Conflux 感知 Workspace、Run Instance、租约或 manifest。

Conflux 本身不是系统拓扑控制面，也不负责决定哪些进程应共享 endpoint。仅依赖 Conflux 或
`generated_spec.py` 的默认 channel，无法保证多 Workspace / 多 Instance 隔离。完整保证来自 System
分配 typed route、在进程启动和 manifest 中注入同一 route fact，并由 composition fail closed 地校验；
Conflux 只消费已经解析和验证过的 route。

### 1. 固定 stream ID 只表达 owner contract 类别

`schemas/transport.toml` 继续是固定 transport spec 的唯一来源：

| Owner | Aeron stream ID |
| --- | ---: |
| Reference | 1201 |
| Market | 1301 |
| Account | 1401 |
| Execution | 1501 |
| Risk | 1601 |
| Capital | 1701 |

这些 ID 不随 Workspace、Run Instance、`market_id` 或 `account_id` 动态生成。一个 route 内的多个
Account publisher 共享 Account stream；事件中的 `account_id`、producer identity 和 incarnation
定义各自的业务事实与顺序边界。

### 2. System 拥有两级命名事件路由

System 是 runtime path、process lifecycle、instance resource 和 launch coordination 的 owner，因此
也拥有事件路由的分配、租约、持久化、注入和释放。业务 Application、Domain 和 Actor 不构造或推导
Aeron endpoint。

每个 Workspace 至少有一个 `WorkspaceEventRoute`：

- Reference 唯一发布到该 route；
- shared Market 发布到该 route；
- 多个 Run Instance 可以订阅该 route；
- route 只在当前 Workspace 内共享，不是整台机器或多个 Workspace 的全局总线。

每个 live/paper Run Instance 有一个稳定的 `InstanceEventRoute`：

- Account、Risk、Execution 和 Capital 发布到该 route；
- instance Market 发布到该 route；
- 该 Instance 的 Strategy 和其他获准 consumer 订阅该 route；
- route 在 Instance 完整生命周期内不变，组件重启不得重新分配。

Replay 若完全使用本地确定性事件源且不发布 live Aeron 事件，可以不分配 Instance route。

标准拓扑为：

```text
WorkspaceEventRoute
  1201 Reference (exactly one publisher owner)
  1301 shared Market (zero or one publisher owner)
      -> Instance A consumers
      -> Instance B consumers

InstanceEventRoute A
  1301 instance Market (when selected)
  1401 Account(s)
  1501 Execution
  1601 Risk
  1701 Capital
      -> Instance A consumers only

InstanceEventRoute B
  1301 instance Market (when selected)
  1401 Account(s)
  1501 Execution
  1601 Risk
  1701 Capital
      -> Instance B consumers only
```

### 3. Route 是 typed System resource

System 定义 typed route value，至少包含：

```text
route_id
scope: workspace | instance
aeron_dir
channel
transport_spec_version
transport_fingerprint
workspace_id
launch_id / instance_id (instance scope only)
```

System route allocator 必须在 machine-local 范围协调 UDP endpoint，避免同时运行的 Workspace 互相
占用端口。分配使用互斥租约、端口可用性检查和原子持久化；不能用 `hash(instance_id)` 猜测端口。
Workspace 和 Instance 记录所获租约，stale recovery 只有在确认 owner 已停止后才能回收。

`DEFAULT_CHANNEL` 只允许用于 contract fixture、单进程开发或显式选择的兼容模式。instance-scoped
生产进程缺少显式 route 时必须启动失败，不能静默回退。

### 4. Instance manifest 声明 route，并由组件引用

Instance manifest 升级 schema，分别声明可见的 Workspace route 和本 Instance route。每个组件连接
显式引用其 event route，不能假设全部组件使用同一 channel：

```json
{
  "schema_version": 2,
  "event_routes": {
    "workspace_shared": {
      "scope": "workspace",
      "aeron_dir": "/workspace/run/aeron/media",
      "channel": "aeron:udp?endpoint=127.0.0.1:40123",
      "transport_spec_version": 1
    },
    "instance": {
      "scope": "instance",
      "aeron_dir": "/workspace/run/aeron/media",
      "channel": "aeron:udp?endpoint=127.0.0.1:41001",
      "transport_spec_version": 1
    }
  },
  "components": {
    "reference": {"event_route": "workspace_shared"},
    "market": {"event_route": "workspace_shared"},
    "risk": {"event_route": "instance"},
    "execution": {"event_route": "instance"},
    "capital": {"event_route": "instance"}
  },
  "accounts": {
    "main": {"event_route": "instance"}
  }
}
```

当 Market scope 为 `instance` 时，Market 的引用改为 `instance`。Reference 永远引用
`workspace_shared`；manifest 中的连接事实不转移 Reference 的生命周期或 mutable-state ownership。

Manifest reader 一次完成 schema、identity、route reference、scope 和 transport fingerprint 校验，向
composition 返回 typed connection facts。业务调用方不得读取任意 JSON 并自行拼 channel。

### 5. Publisher 和 subscriber 使用同一份 route fact

System 启动组件时，将 route 的 `aeron_dir` 和 `channel` 显式传给 binary。Binary 只做参数适配，
composition 用它构造 `AeronEndpoint`，contract publisher 继续验证固定的模块 stream ID。

Market 必须补齐 `--aeron-channel` 和 `MarketHostRequest.aeron_channel`，移除 instance composition 中对
`DEFAULT_AERON_CHANNEL` 的硬编码。Account、Execution、Risk 和 Capital 虽已有部分 channel 参数，
System 启动路径仍必须统一显式注入，不能依赖各自默认值。

Strategy composition 从 manifest 的 component route reference 构造 owner contract client。Market、
Account、Execution、Risk 和 Capital Python client 已有的 `channel` 参数必须收到 manifest 中的值。
一个 Strategy 可以同时订阅 Workspace route 上的 shared Market 和 Instance route 上的其他 owner。

System 在启动 publisher 时持久化包含进程 PID 和完整 typed route 的 component declaration。复用已 ready
进程前先验证 declaration 的 PID 仍存活，且其 scope、identity、channel、spec version 和 fingerprint 与
当前 System route 一致；缺失 declaration 的旧进程也 fail closed。Instance manifest 引用同一个 route
fact，consumer composition 再验证 manifest route。任一端不一致时，Instance 不得进入 ready。

### 6. 顺序连续性按 producer incarnation 分区

本节关于 route 隔离和 continuity key 的结论保留；snapshot/resync 与 gap fail-closed 语义由
[Decision 0041](0041-authoritative-current-views-and-best-effort-notifications.md) 替代。

Aeron route 负责 delivery isolation；contract metadata 负责 event-log identity。连续性键至少包含：

```text
logical_stream_id + producer_id + producer_incarnation
```

Account 还按 `account_id` 分区。Market、Execution、Risk 和 Capital 不能把其他 producer 的 sequence
纳入同一个 cursor。Actor 重启产生新的 incarnation；consumer 必须从 owner current view 做 snapshot /
resync 后建立新 cursor，不能把重置后的 sequence 当成旧 stream 的重复或 gap。

`workspace_id / launch_id / instance_id` payload 校验继续保留为纵深防御。完成物理隔离后收到外部
Instance 事件表示 route 或 manifest 错接，应 fail fast；不能简单 `continue` 后隐藏拓扑错误。

### 7. 不建立重复的共享事件总线

System 不把 Instance event 复制到 Workspace route，也不增加 event manager、registry facade 或跨 owner
JSON envelope。共享 route 只承载本来就由 Workspace-scoped owner 管理的事实。需要其他业务事实的
模块继续依赖 owner contract，而不是绕过 contract 监听一个无类型的系统总线。

## Technical migration

迁移按以下可独立验证的顺序完成：

1. 在 System application resource boundary 增加 typed route、allocator、lease 和 manifest v2；
   manifest v1 可以被兼容读取，但事件 consumer 缺少 typed route 时一律 fail closed。
2. 为 Market server/request/composition 补齐 channel 注入；统一 Account、Execution、Risk、Capital 的
   System launch 参数，Reference 和 shared Market 使用 Workspace route。
3. 扩展 `ComponentConnection` 和 `resolve_instance_connections` 返回 typed route reference；所有 Python
   owner client 显式使用 manifest channel。
4. 将 consumer cursor 改为 producer/incarnation scoped，并定义 restart 后 snapshot/resync 行为。
5. instance 模式禁止默认 channel，删除完成迁移后的隐式 fallback 和 manifest v1 生产路径。

当前实现的主要改造锚点包括：

- `schemas/transport.toml` 与生成脚本：保留固定 stream ID，收窄 default channel 的使用语义；
- `kairospy/system/apps/launch/application/runtime.py`：分配 route、写 manifest、向组件注入；
- `kairospy/system/apps/launch/application/connections.py`：typed manifest route 解析；
- `kairospy/system/apps/components/application/__init__.py`：构造显式 component process command；
- `crates/modules/market/src/bin/kairos-market-server.rs` 与 Market composition：移除硬编码 channel；
- `kairospy/investment/apps/*/composition/`：从 component route 构造 native event source；
- 各 owner Application event loop：按 producer incarnation 验证 cursor。

## Verification

完成改造至少需要以下证据：

- 同一 Workspace 同时运行两个 Instance，二者订阅相同 `market_id`，Instance Market 事件互不可见；
- shared Market 的同一事件可被两个 Instance 正常消费；
- 同一 Instance 内多个 Account publisher 可由一个 Account subscription 聚合，cursor 按账户和
  producer incarnation 独立；
- Instance A 的 Account、Risk、Execution 和 Capital 事件不会到达 Instance B；
- 两个 Workspace 同时运行时 route 不发生端口冲突或交叉消费；
- Actor 重启产生新 incarnation，并要求 snapshot/resync，不产生虚假的 duplicate 或 gap；
- manifest 缺 route、route reference 越界、scope 错误、publisher/subscriber channel 不一致或
  fingerprint 不一致时 readiness 失败；
- architecture check 阻止 instance process 直接使用 `DEFAULT_CHANNEL`，并阻止 Reference 使用
  Instance route；
- transport、owner contract、双 Instance process integration 和 Python Strategy ingress 测试全部通过。

## Consequences

- Reference 在 Workspace 内继续保持唯一 Actor、catalog 和 publisher，Run Instance 不复制 Reference。
- shared Market 与 instance Market 可以同时使用同一个稳定 Market contract stream ID，而不会互相混流。
- 多 Account 仍能在一个 Instance route 内高效聚合，不需要为每个账户发明 stream ID。
- System 增加 route allocation、lease、manifest 和 readiness 责任，但业务模块不获得新的基础设施状态。
- Instance 启动不再依赖全局默认 endpoint，多个 Instance 和多个 Workspace 可以并发运行。
- payload identity 与 transport route 各司其职；sequence gap 只在一个 producer incarnation 内有意义。
- route/schema 迁移需要同步更新 Rust process composition、Python client composition、manifest reader 和
  集成测试，不能只修改生成的 `generated_spec.py`。
