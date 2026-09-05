# 0046 — Reference venue、coverage 与 current query 边界

- Status: Accepted
- Date: 2026-09-03

## Context

旧 Reference 模型把 `Exchange` 同时用于正式上市地、实际成交设施和 Provider 地址，无法
准确表达美国 NMS 多场所成交、日本 PTS、交易所内独立设施、OTC reporting，以及 listing
与 execution venue 不同的情况。消费者还在进程启动时复制完整 Reference snapshot；后续
目录提交不会更新该副本。空查询结果也无法区分不存在、未下载、同步中、陈旧或失败。

Reference 与 Integration 曾同时配置 Provider endpoint、credential 和 enabled 状态，形成
双重权威；一个 Provider-wide 开关还会隐式启动多个语义不同的产品目录。

## Decision

1. Reference v3 使用 `Venue`、`Instrument`、`Listing`、`Market` 与
   `ProviderCatalogMembership`。Listing 引用 listing venue；Market 引用 execution venue，
   可选引用同 Instrument 的 origin listing。`Exchange` 只保留为 v2 兼容投影。
2. Coverage 是 Reference-owned 一等事实。只有 declared scope 的完整候选经过校验和原子
   提交后，空结果才能成为 `NotFoundInCoveredScope`；否则返回 unknown、preparing、
   stale 或 unavailable。
3. 删除公共 Reference/consumer catalog Snapshot。消费者通过 Reference contract 的 bounded
   typed SQLite query，在一个短 read transaction 中取得关联事实、coverage evidence 和同一
   watermark；每次新业务决策重新查询 current tables。
4. Integration profile 独占 endpoint、environment、credential reference、连接 enabled、
   products 与 purposes。Reference persisted source registry 独占 source scope、desired state
   与 sync policy。System 只拥有 Reference process lifecycle。
5. 内置 public product sources 只在 registry 缺失时 seed 一次。之后 registry 是唯一权威；
   单个 source 的连接或同步失败不得阻止其他 source 或 Reference process 启动。
6. v3 使用显式 SQLite schema/version 与 FlatBuffers event schema。v2 字段语义不被偷改，
   无法无损表达的场所不伪装成 Exchange。
7. Canonical 事实冲突按 source scan 隔离：同轮冲突涉及的候选来源一并拒绝，保留 staging
   与 last-known-good；无冲突候选通过完整校验后可提交。不以来源完成顺序或记录遍历顺序
   选择赢家。提交确认只清除该事务包含的 scan，不清空其他来源的待提交状态。

## Consequences

- Market、Execution、Account 与 Strategy 不再维护第二份 canonical catalog；事件只用于唤醒
  重评估和审计，不替代 current query。
- Provider catalog membership 不再被误当成 listing、可订阅行情或可执行权限。
- Live Execution 可以基于 coverage/freshness policy fail closed；交互浏览可以展示
  last-known-good 并明确警告。
- Source setup 需要分别选择 Provider product、coverage scope 和 Integration connection；
  启用一种业务能力不自动启用同 Provider 的其他能力。
- v2 compatibility 是有期限的迁移机制。主要消费者迁移到 Venue 后应删除该兼容转换，
  不把双写固定为永久 facade。
