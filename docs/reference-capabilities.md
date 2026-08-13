# Reference 模块交付定义与最终验收条件

本文定义 Reference 的最终职责、边界和验收条件。数据平面的具体实现以
[`reference-sqlite-read-model-design.md`](./reference-sqlite-read-model-design.md) 为准。
Instrument、Listing、Market、MarketDataAccess、ExecutionAccess 的业务语义和关系以
[`instrument-listing-market-access-design.md`](./instrument-listing-market-access-design.md) 为准。

Reference 的定位是：

> 维护全局金融参考目录及生命周期事实；以单写者 SQLite 保存权威当前状态和历史，发布有序变更；消费者通过 contract 建立自己的有界投影。

## 1. 必须交付的能力

Reference 必须维护并交付：

1. Entity / Exchange / Broker / Provider 身份、角色和生命周期；
2. Asset 身份、分类和状态；
3. Instrument 标准身份和金融属性；
4. Listing 挂牌关系和有效区间；
5. Market 静态定义、trading Exchange 和交易规则；
6. FinancialProduct 目录；
7. MarketDataAccess 行情访问映射；
8. ExecutionAccess direct/smart 可执行路径；
9. generation、event sequence 和生命周期历史；
10. provider health、last-known-good、分页进度和 coverage；
11. read-only SQLite contract、变更流、控制面和诊断能力。

Reference 不拥有实时行情、订单簿、订单、成交、余额、持仓、风险预算或策略 universe。

## 2. 模块边界

- Integration 负责 provider-native participant、连接、鉴权、协议和外部事实标准化。
- Reference 负责 Exchange/Broker/Provider 在内的 canonical identity、目录冲突校验和生命周期。
- Market 负责行情、订阅和自己的 Reference projection。
- Execution 负责订单生命周期，并按需读取 Reference 规则。
- Account 负责账户事实，并按 binding 缓存 provider instrument 到 canonical identity 的映射。
- Strategy/Research 只读取声明 universe 范围内的数据。
- Workspace/System 负责数据库路径、进程生命周期和启动依赖。

其他模块只能依赖 `kairos-reference-contract`，不得导入 Reference `services/`、`domain/`、SQLx store 或 provider 实现。

## 3. 数据与身份验收

所有对外记录必须满足：

- canonical ID 稳定且可验证；
- provider payload 不进入 application/domain contract；
- provider symbol、canonical instrument 和 market 显式映射，不能从字符串 grammar 猜测；
- 生命周期状态使用 Reference-owned 语义；
- effective interval 合法；
- 同 ID 的不可调和经济字段冲突拒绝整次 promotion；
- 来源顺序不能成为隐藏 winner；
- provider provenance 保留在具体 listing、market 或 access facts 上。

Instrument 必须表达决定经济同一性的产品/合约属性；provider symbol 和 listing 有效期留在
Listing/Access。现货 Instrument 从旧的“基础资产身份”迁移为 base/quote 产品身份前必须执行显式
ID/data migration，不能静默重写。衍生品身份必须包含足以区分合约的 canonical 属性。

## 4. Provider 和 coverage 验收

- provider 请求失败时保留 last-known-good；
- 失败请求不等于权威空目录；
- 进程重启后分页 cursor、staging、pause 和 coverage 可恢复；
- 一个 source 可以独立 refresh/pause/resume；
- paused source 不被轮询，但已完成事实继续可用；
- replacement 未完成时继续服务上一份完成状态；
- circuit、backoff、timeout 和恢复探测有自动化测试；
- 大目录逐页 staging，cursor 与 page 同事务提交；
- Massive options 必须使用显式 underlying coverage 和过期清理，不能常驻全市场 universe。

## 5. Actor、SQLite 和事务验收

- `ReferenceActor` 是唯一 mutation owner；
- provider worker、publisher、server 和消费者不能直接修改 canonical tables；
- 只有 Reference 进程运行 migration 或打开 read-write connection；
- 消费者以 read-only/query-only 模式通过 contract 读取；
- SQLite 使用 WAL、busy timeout 和短事务；
- 当前状态使用规范化、可索引的 current tables；
- 不存在 whole-catalog JSON blob；
- Actor 稳态内存不随 catalog 总量维护第二份完整 current state；
- reconcile 内存按 provider page/change batch 有界；
- current rows、lifecycle rows、generation/sequence 和 provider promotion 原子提交；
- commit 失败时不会暴露候选状态或前进水位；
- schema 不兼容或损坏返回明确 Persistence/Contract error。

## 6. Lifecycle 和发布验收

`reference.events` 是完整 Reference change stream。每条 delta 至少包含：

- generation 和 sequence；
- event time；
- record kind 和 record ID；
- `upsert` / `delete` operation；
- contract record 或 tombstone；
- 受影响 scope 信息。

验收要求：

- sequence 重启后单调递增且不复用；
- 生命周期历史按 sequence/time 有界分页；
- current state 和 lifecycle 在同一事务提交；
- publication 只保存一个 durable cursor，不复制完整 payload outbox；
- batch 有界，默认最多 1,024 条；
- 发布失败不回滚 current state，cursor 保持不变；
- 重复通知可由消费者幂等处理；
- 无 subscriber 时不会阻塞 Reference commit。

## 7. Contract 和消费者恢复验收

Contract 必须提供：

- schema-version 和 watermark 校验；
- typed read-only SQLite reader；
- 按 ID、provider symbol、instrument、underlying、exchange、status 的有界查询；
- bounded page 和 lifecycle replay；
- typed event decoder/subscriber；
- Rust/Python 一致的 request/result 语义。

消费者启动和恢复遵循：

1. 在一个短 SQLite transaction 读取 scoped projection 和水位 `W0`；
2. 启动 change subscriber；
3. 读取 `W1` 并 replay `(W0, W1]`；
4. 连续应用 live delta；
5. 定期比较 SQLite 水位，检测末尾通知丢失。

sequence gap、重复、schema mismatch、缓存更新失败、重启和 SQLite 水位变化必须有测试。恢复来源是 SQLite，不存在 mmap、manifest 或 UDS catalog-query fallback。

## 8. 各模块投影验收

### Account

- 按 account binding/provider/product 查询 identity；
- 只缓存实际解析的 provider instrument；
- generation 变化时失效或增量更新；
- missing/ambiguous 显式拒绝，不伪造 market。

### Execution

- 监控 Reference watermark；
- preflight 按 market ID 或唯一 instrument market 查询规则；
- 记录使用的 generation/sequence；
- stale、missing、ambiguous 时拒绝或按显式 backtest policy 降级；
- 不加载完整 Reference universe。

### Market

- projection 由 contract 分页/按 subscription scope 建立；
- change 通知触发成员重算或 recovery；
- gap 时从 SQLite catch-up/rebuild；
- 动态订阅使用本地 projection，不在每个 tick 查询 SQLite；
- projection error/stale 时不猜测 provider route。

### Strategy、Research 和 CLI

- Strategy 只读取 declared universe；
- Research 使用显式数据集范围和分页；
- CLI 默认有界，不允许隐式 dump 百万级目录；
- Python 使用 `mode=ro` SQLite URI 和 `PRAGMA query_only=ON`。

## 9. 控制面和可观察性

UDS 仅提供 health、provider、refresh、publish、coverage、管理命令和 stop。目录 current-state 读取走 SQLite contract。

必须可观察：

- provider latency、freshness、circuit 和 page progress；
- refresh/reconcile/commit duration；
- current row count、database/WAL size；
- generation、event sequence 和 unpublished sequence depth；
- publication latency 和 retry；
- consumer projection cardinality、watermark age、recovery/error count；
- control queue depth。

## 10. 最终退出标准

Reference 只有同时满足以下条件才算交付：

1. canonical current tables 是唯一权威当前状态；
2. whole-catalog blob、Reference mmap、manifest、slot-size 配置和 duplicate outbox 已删除；
3. Account、Execution、Market、Strategy、CLI 均使用新 contract；
4. 生产代码搜索 `ReferenceMmap` 和 `snapshots/reference` 结果为零；
5. 单写者/read-only 权限和跨模块 import 架构检查通过；
6. 一百万记录规模测试不产生完整 catalog clone/snapshot，RSS 满足配置预算；
7. crash-before-commit、crash-after-commit、publication failure、gap、duplicate、restart 和 stale watermark 测试通过；
8. focused Rust/Python 测试、workspace 测试、fmt 和 `git diff --check` 通过。

推荐验收命令：

```text
cargo test -p kairos-reference-contract -p kairos-reference-service --all-targets
cargo test -p kairos-account-service -p kairos-execution-service -p kairos-market-service --all-targets
uv run pytest -q tests/test_reference_contract.py
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
rg -n "ReferenceMmap|snapshots/reference" crates kairospy tests
rg -n "reference_catalog|reference_pending_publication" crates/business/reference
```
