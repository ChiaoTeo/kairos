# ExternalAccount State and Read Contract

账户状态不采用独立的读写真相源。`AccountActor` 是运行时账户状态的唯一入口：REST 快照、private stream、成交事件和模拟账本都通过它更新账户状态；CLI、策略和 Monitor 通过 Actor/Application contract 查询状态。
一个 ExternalAccount 可以拥有多个 AccountSegment；Actor 必须按 segment 隔离余额、仓位、订单和保证金状态，
账号级查询只负责返回聚合元数据和各 segment 的状态摘要。

## State ownership

```text
Broker REST / private stream / execution events
                    ↓
              ExternalAccount Actor
                    ↓
           ExternalAccount Application API
                    ↓
        CLI / strategy / monitor / views
```

`AccountSnapshot` 是某个 AccountSegment 的外部观察事实，`AccountState` 是该 segment 的账户领域状态。
`AccountCurrentView` 和 `AccountPortfolioView` 是不可变展示投影，不拥有状态，也不能反向更新账户。

## Query and refresh

- `AccountQueryRequest(mode=cached)` 只读取当前账户状态，并按 `max_age_seconds` 计算 `age` 和 `stale`。
- `RefreshAccountCommand` 请求 ExternalAccount Actor 读取 broker；刷新成功后先更新账户状态，再发布 `account.snapshot` 事件。
- REST 用于启动快照、定期校准和断线恢复；private stream 用于低延迟增量更新。
- 查询不得返回 vendor raw payload。账户 CLI 输出由 typed `AccountSnapshot` 转换而来。

## Multi-segment aggregation

同一 `ExternalAccount` 的不同 `AccountSegment` 余额和权益只有在估值货币明确且一致时才允许聚合。
跨多个 `ExternalAccount` 的聚合必须交给 Portfolio。否则保留各 segment 明细，并将 portfolio 标记为
`aggregate_complete=false`，不得直接相加不同货币的金额。

账户模式切换期间，查询结果必须携带 `AccountStatus.RECONCILING` 或
`AccountStatus.TYPE_MISMATCH`，交易用例必须拒绝使用尚未重新校准的 segment。

## Freshness

账户展示投影在发布时根据 `as_of` 和最大快照年龄计算 stale。交易前应使用带 freshness policy 的账户查询，并拒绝 stale 或 incomplete 状态。
