# Kairos Reference

Reference 是 Kairos 对交易对象身份、场所、上市关系、成交市场及其生命周期的
Workspace 级知识权威。它把 Integration 提供的 provider-normalized facts 映射为
canonical `Venue`、`Instrument`、`Listing`、`Market` 和
`ProviderCatalogMembership`，并用 coverage evidence 说明一个查询结果是已知、在完整
范围内不存在、未覆盖、准备中、陈旧或来源不可用。

Reference 不拥有 Provider 网络与鉴权、行情 observation、订单路由、账户权限、
workspace 进程编排或研究数据集。

## Canonical 模型

- `Venue` 是 listing、execution 或 reporting 场所。交易所、ATS、PTS、OTC facility
  和 reporting facility 不再被强行归为同一种 Exchange。
- `Listing` 只表示 Instrument 在 listing venue 的正式挂牌关系。
- `Market` 表示 Instrument 在 execution venue 上可独立寻址、报价、成交或应用独立
  trading rules 的入口。它可以引用 origin listing，但两者的 venue 可以不同。
- `ProviderCatalogMembership` 只证明某个 Provider 产品目录包含 Instrument；它不
  证明正式上市、实时行情可用、账户可见或订单可执行。
- 目标边界中，`Exchange` 是 Venue 的一种类型，旧 `Exchange`、`Listing.exchange_id`
  和 `Market.exchange_id` 只服务 v2 迁移。新业务逻辑使用 `VenueId`、
  `listing_venue_id` 与 `execution_venue_id`。当前内部仍保留旧模型到 Venue 的转换和
  存储写入路径，尚未完成只由 canonical Venue 驱动的收敛；不能将其视为独立可编辑的第二份权威。
- `parent_venue_id` 表达有来源证明的场所/设施关系，不表达运营公司或集团的股权层级；
  Exchange 也不是必须包含多个 Venue 的容器。当前没有独立的 Operator 业务实体。

美国股票、中国股票或日本股票不是 canonical 分类。issuer jurisdiction、listing
venue、execution venue、币种、segment、Provider catalog 和账户准入必须分别表达。
类似地，上证可以指上交所、某个板块或指数，必须在用户确认后保存具体 canonical ID。

## Coverage 与查询

Source definition 表示“系统准备从哪里获取哪些事实”；`ReferenceCoverage` 表示当前
Reference 有资格在哪个 declared scope 对哪些 fact kinds 下结论。只有完整读取、校验
并原子提交的 scope 才能标记为 `complete_for_declared_scope`。记录数量不证明完整，
Provider 产品目录完整也不等于某个国家的全部 listings 完整。

公共 contract 不提供 catalog 或 consumer Snapshot。调用方通过
`ReferenceCatalog::read_session()` 打开短生命周期、只读的 SQLite transaction，并使用
bounded typed queries：

- `resolve_market` 原子返回 Instrument、Market、execution Venue、origin Listing、
  Provider membership、coverage evidence 与同一个 watermark；
- `resolve_participant_symbol` 映射 participant/product/source symbol，歧义或未知时不
  制造 canonical ID；
- `search_instruments`、`search_venue_listings`、`search_venue_markets` 与
  `search_venues` 返回 bounded page、knowledge conclusion 与 evidence。

“最新”是一次 query 开始时最新的 committed generation。transaction 返回前结束；
下一次 planning、admission、subscription resolution 或 account normalization 再查询，
因此能看到届时最新的 generation。消费者只保存决策采用的 canonical IDs、规则、路由
事实和 Reference watermark，不保存全目录副本。

Provider 的 full/paged/scoped snapshot 描述一次来源扫描的完整性范围，不是全目录消费者 DTO。
Reference 将已提交来源事实与未完成扫描的 staging/cursor 分开持久化；完整扫描校验后原子提交，
失败时保留上次成功提交的事实。当前恢复路径不依赖一个全量 Actor checkpoint。

`Instrument.settlement_asset_id` 独立于报价币和账户抵押品：来源没有明确结算证据时保持未知，
旧记录读取为空。该字段贯通 typed SQLite 查询、事件及 Python 接口；不能把 Binance 的
`marginAsset` 在通用适配器中直接当作结算币。
当前 Binance、OKX、Hyperliquid 的衍生品身份仍有按 base/quote 合并的旧路径，跨场所合约隔离及
旧记录迁移尚未完成；不能将同一旧 Instrument ID 当作已证明的合约可互换性。

## Integration、Reference 与 System

配置只有三个明确 owner：

```text
config/integration/provider-connections/<connection-id>.toml
  endpoint(s), environment, credential_id, enabled, products, purposes

Reference persisted source registry
  source_id, connection_id/built-in public connection, scope,
  desired_state, sync_policy

[reference.runtime]
  refresh interval and per-tick/publication budgets
```

`[reference.providers.*]` 已废弃；旧 workspace 启动时只把它作为一次性迁移输入：按明确
product 生成缺失的 Integration connection profile 和 Reference source definition，且不覆盖
任一已存在目标。迁移后该旧字段不再参与运行态决策，也不会被双写。Reference 不再保存
endpoint、credential 或 Provider-wide enabled 开关。Integration connection 只表示如何连接，Reference source
只表示需要知道什么；启用 catalog 不会隐式启用 Market stream、Execution 或 Account。

Binance、OKX 和 Hyperliquid 的 credential-free product sources 是首次启动 seed。每个
product 是独立 source；seed 只在 registry 中没有对应定义时写入，随后 persisted registry
是唯一权威。Massive equity/options 与 Binance equity 等凭据型 source 必须由用户的
coverage goal 显式创建，并绑定允许 `reference-catalog` purpose 和相应 product 的
Integration connection。

一个 workspace 正常只运行一个 Reference process。System 负责启动该进程；Reference
异步激活每个 desired source。某个 source 缺凭据、endpoint 无效或暂时失败时，只把该
coverage 标记为 waiting/stale/unavailable，不阻止其他 source 或进程本身启动。成功同步
执行 fetch → stage → validate → atomic promote；catalog generation、membership 与 coverage
watermark 在同一个 transaction 中提交。

`max_sources_per_tick` 同时限制一轮允许推进的独立 source 数量；默认值为 1，配置大于 1
时，这些 source 的网络抓取并发执行，每个 source 保留独立超时。分页 cursor 仍由该
source 独占，结果校验和 canonical 提交仍由唯一 Reference Actor 串行处理。抓取借用
Conflux 管理的只读连接，不复制连接来绕过 readiness/retirement 检查。待提交 source
不会重复抓取，也不会冻结无关健康 source。

## 存储、事件与兼容

SQLite schema version 10 包含 Venue、Venue Listing、Venue Market、Provider membership、
Coverage、待原子提交的 coverage transition 与 Provider venue-identifier mapping current tables。version 6/7/8 数据通过显式迁移生成保守的 v3 场所目录记录；没有证据的
MIC、listing role 或 jurisdiction 保持未知。

version 10 删除 `reference_provider_pending_promotion`。完成和移除的 source 范围由扫描结果显式传给
Actor，事务仅提交这些范围及其 coverage、membership 和 outbox。普通目录写入不提交 staging。
升级保留已提交记录和分页暂存；旧 pending marker 不会被重放。无 cursor 的未提交扫描会在重新抓取前清理，
有 cursor 的扫描继续分页，完成后由当前工作流显式提交。

v3 FlatBuffers 事件包括 Venue、Venue Listing、Venue Market、Provider catalog membership
的 upsert/update，以及会改变消费结论的 `CoverageStateChanged`。事件携带精确 catalog
revision 与 protocol metadata，publication outbox 保存当时编码的 bytes，不能从未来
current row 重建旧事件。v2 schema 不偷改 `exchange_id` 语义。

## 消费规则

- Market 将 canonical Markets 与已安装的 observation capabilities 求交集；consolidated
  view 属于 Market，不写回 Reference。
- Live Execution 在 admission 时重新解析 Market、venue、rules 和 coverage，默认对
  unknown/unavailable fail closed，并把 watermark 写入自己的 commitment/audit。
- Account 用 participant/product/source symbol 查询；未知或歧义时保留 raw diagnostic，
  不构造临时 InstrumentId。
- Strategy 配置保存 canonical ID 与 resolution generation，运行时重新验证。
- Workbench 普通搜索用“已找到 / 范围内没有 / 尚未获取 / 正在准备 / 数据较旧 / 来源
  不可用”的用户语言；source ID、technical blocker 与 credential details 只在高级详情。

## 代码边界

```text
bin -> composition -> application -> services
                         \-> domain
```

`ReferenceApplication` 是同 package use-case facade，`ReferenceActor` 是可变目录状态的
唯一 owner。跨业务模块只依赖 `kairos-reference-contract`。SQL、表名、Provider payload
和 SDK client 都不越过 contract/application 边界。

## 验证

```text
cargo test -p kairos-primitives -p kairos-reference-contract -p kairos-reference
cargo test -p kairos-market -p kairos-execution -p kairos-account
make python-type-check
make rust-fmt-check
python3 scripts/check/check_documentation.py
make docs-check
```
