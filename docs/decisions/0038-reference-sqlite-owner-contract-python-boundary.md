# Decision 0038：Reference SQLite Owner Contract Python 边界

- Status: Accepted
- Date: 2026-08-27
- Complements: [Decision 0037](0037-complete-owner-contract-python-convergence.md)
- Scope: Reference catalog Python reads

## Context

Reference 与其他 current-view owner 不同：它的权威读模型是 SQLite，而不是 LMDB 与
FlatBuffers。Rust `kairos-reference-contract` 已拥有只读 catalog adapter、schema version、typed
record 和 bounded query，但 Python `ReferenceClient` 仍直接打开 SQLite，持有 transaction，
拼接 SQL、命名表和索引列，并解码 JSON payload。这形成了第二套生产 contract，并让 Python
调用方能够绕过 owner 的 schema、error 和 typed query 语义。

Reference 查询还需要一个重要保证：一组策略读取必须固定在同一个已提交 generation。迁移不能把
每个 query 改成独立连接，也不能把长生命周期的 SQLite connection 或 transaction 暴露给业务层。

## Decision

Reference 提供独立 companion package：

```text
crates/modules/reference/contract/py
```

它生成私有 ABI3 extension `kairospy._native_reference_contract`，并与其他 owner extension 一起进入
唯一的 `kairospy` wheel。Python 不保留 fallback 或 direct-SQL 路径。

`kairos-reference-contract` 是 SQLite catalog 的唯一实现者。它拥有：

- existing-file open、immediate `query_only` write prohibition、busy timeout、schema version validation；
- generation、event sequence 与 commit watermark；
- exchange、asset、instrument、listing、market 的固定 typed query；
- batch ID、escaped search、status、active、expiry、option、asset-code 与 pagination 语义；
- query 中的 market、instrument、listing、exchange、asset identity 及 closed enum validation；
- lifecycle event、option coverage、integrity 与 publication outbox 查询；
- JSON persistence row 到 contract-owned typed value 的唯一 decode。

Rust `ReferenceReadSession` 拥有 connection，并在 `BEGIN DEFERRED` 后立即读取 metadata，建立固定 WAL
read session。它的所有查询返回 owned typed records；connection、transaction、SQL 和 JSON payload 都不
跨 Pyo3。关闭或析构 session 会结束 transaction。

连接不带 CREATE flag，并在任何 schema/data query 之前设置 `query_only`。这是为了允许部分 SQLite
版本为已关闭的 WAL catalog 建立只读所需的 `-shm` sidecar；该连接从未获得 contract 写入能力。

Python `ReferenceReadSession` 只是 native session 的 context-manager facade。它可以把 immutable native
record 适配成现有 Application 消费的 mapping，但不验证 persistence row、不解释 schema，也不持有
SQLite object。`ReferenceApplication` 与策略继续只使用现有业务 API，不感知 SQLite 或 Pyo3。

Reference control RPC 仍是独立 capability；本 Decision 不把 catalog read transaction 与 control
socket 合并，也不改变 Reference 作为唯一 writer 和 catalog mutable-state owner 的定位。

## Consequences

- 六个业务 owner 都在 `contract/py` 提供 Python boundary，Reference 不再是例外。
- 同一 snapshot 中的多次读取不会混合 catalog generation。
- Python production code 不再导入 `sqlite3`、包含 Reference 表名、执行 SQL 或解码 catalog JSON。
- SQLite 的复制边界是 typed owned record；LMDB/FlatBuffers 的 borrowed-field 性能结论不被错误套用到
  跨 Python 生命周期的 SQLite row。
- Wheel、架构检查、native smoke、Rust contract test 和 Python behavior test 同时约束此 hard cut。
