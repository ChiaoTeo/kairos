# Reference Canonical Identity 与业务 Route 分离设计

## 1. 文档状态

- 状态：已实施；Slice 1 至 Slice 5 已完成，仓库级验证见本文末实施记录。
- 日期：2026-08-17。
- 范围：Reference canonical `Instrument`、`Listing`、`Market`，Market 行情来源与
  observation scope，Execution broker route，以及 Massive 和 Binance Equity 的映射。
- 架构权威：本文细化并修正
  [`access-capability-and-route-refactoring.md`](./access-capability-and-route-refactoring.md)
  中“删除 Reference Access 后如何承载 provider address”的部分。
- 不改变的既有决策：Reference 不恢复 `MarketDataAccess` 或 `ExecutionAccess`；Market
  拥有行情来源与订阅路径，Execution 拥有候选、选中和实际执行路线。

本文替代以下实现或设计假设：

- provider provenance、provider product 或 provider symbol 可以作为 canonical `Market`
  的字段或 identity 输入；
- primary listing exchange 可以代表该证券的唯一交易 Market；
- 每个 market observation 都必须归属于一个具体 `market_id`；
- broker/data provider 可以因为提供股票访问而被投影成股票交易所；
- 删除 Reference Access 后，provider address 必须寄生在 Reference `Market` 上。

## 2. 问题与实证

### 2.1 当前模型混合了四种身份

当前实现把以下不同事实压进了 `Market`：

1. Instrument：证券或经济合约本身；
2. Listing：Instrument 在 primary listing exchange 上的上市关系；
3. Market：Instrument 在某个真实交易 venue/product surface 上的可交易关系；
4. Provider route：数据商或券商如何寻址、订阅或执行该 Instrument/Market。

这会产生不存在的 `exchange:binance`、`listing:binance:equity:*`、
`market:binance:equity:*`、`listing:massive:options:*` 和
`market:massive:options:*`。

### 2.2 Massive live payload 证据

2026-08-17 使用 Workspace 当前 `massive-readonly` 凭据进行了只读抽样；凭据未进入
日志、fixture 或本文。

AAPL ticker reference 返回：

```text
ticker = AAPL
market = stocks
primary_exchange = XNAS
```

这只能证明 AAPL 的 primary Listing 位于 Nasdaq，不能证明所有 AAPL 成交或报价属于
Nasdaq。同期 AAPL trades 抽样同时出现：

```text
exchange = 19  -> Cboe BZX / BATS
exchange = 4   -> FINRA Alternative Display Facility / XADF
tape = 3
trf_id = 201 / 202
```

因此同一 Instrument 同时存在 primary listing exchange、多个实际成交 venue、TRF/off-
exchange reporting facility 和 Massive 数据来源。

SPY option contract reference 返回：

```text
ticker = O:SPY260817C00500000
primary_exchange = BATO
```

`BATO` 是 Cboe BZX Options Exchange。当前 Massive mapping 已经读取该字段，却对 options
强制生成 `listing:massive:options:*` 和 `market:massive:options:*`，属于 canonical mapping
错误。

### 2.3 Binance Equity 证据

Binance Equity catalog 的 participant kind 已是 `Broker`。已验证 endpoint 提供 broker
symbol、tradability 和数量限制，但不提供 primary listing venue、quote currency、
settlement asset 或 price tick。Reference 当前仍创建：

```text
exchange:binance
listing:binance:equity:AAPL
market:binance:equity:AAPL
```

这些记录把 broker entrance 误写成交易所、Listing 和 Market。Binance 的 crypto exchange
产品仍可合法映射为 `exchange:binance`；本文只否定 Binance Equity broker surface 自动
成为股票交易所的行为。Participant 的具体角色和 provider product 必须参与映射判断，
不能仅按 provider 名称判断。

### 2.4 当前持久化影响范围

抽样时 `.kairos/reference/reference.sqlite` 中存在：

```text
market:binance:equity:*  7,924 rows
market:massive:*         14,546 rows
```

旧数据不能通过字符串 rename 修复：一个 primary Listing 可能对应多个 Markets，而一个
consolidated provider observation 也可能不对应任何单一 Market。

## 3. 决策摘要

本文采用以下目标决策：

1. Reference 只拥有 canonical Asset、Instrument、Exchange/Venue、Listing、Market 及其
   lifecycle，不拥有当前行情可用性或 execution access。
2. 不恢复 `MarketDataAccess`、`ExecutionAccess`，也不新增统一 `Access`、registry、manager、
   gateway 或 application-owned provider trait。
3. `Listing.exchange_id` 表示 listing venue；`Market.exchange_id` 表示真实交易 venue。二者
   可能相同，但绝不是同一事实。
4. DataProvider/Broker provenance 不进入 canonical identity equality，也不作为 canonical
   `Market` 的单一 owner/source。
5. Market composition 拥有 provider-native market-data address、source binding、capability
   与运行 route；Execution composition/application 拥有 broker-native address 和 route。
6. Reference provider ingestion 可以保存内部 staging provenance、cursor、coverage 和
   diagnostics，但这些事实不进入公开 canonical entity。
7. Observation 同时支持 venue-specific 和 consolidated/instrument-scoped identity；不为
   满足必填 `market_id` 而制造 `market:massive:*`、`market:opra:*` 或类似伪 Market。
8. Provider 未给出的 venue、Listing、currency 或 tick rule 保持 unknown；禁止用 provider、
   primary Listing 或默认交易所补猜。

目标信息流：

```text
Integration provider facts
          |
          +--> Reference composition normalization
          |       -> canonical Instrument / Listing / Market
          |       -> provider provenance only in staging/diagnostics
          |
          +--> Market composition
          |       -> MarketDataRoute + provider-native subscription address
          |       -> venue/consolidated ObservationScope
          |
          +--> Execution composition
                  -> ExecutionRouteCandidate + broker-native order address
                  -> selected/submitted/reported execution facts
```

## 4. Canonical 模型

### 4.1 Instrument

`Instrument` 表示与 provider 和 venue 无关的证券或经济合约：

```text
instrument:equity:US:AAPL:common
instrument:option:SPY:20260817:500:C
```

Massive ticker、Binance broker symbol 或其他 provider code 不是 Instrument identity。

### 4.2 Listing

`Listing` 表示 Instrument 在 primary listing exchange 上的有效关系：

```rust
struct Listing {
    listing_id: ListingId,
    instrument_id: InstrumentId,
    exchange_id: Exchange,
    exchange_symbol: Symbol,
    status: ReferenceStatus,
    effective_from_unix_nanos: UnixNanos,
    effective_to_unix_nanos: Option<UnixNanos>,
}
```

规则：

- `primary_exchange=XNAS` 可以创建 Nasdaq Listing；
- `primary_exchange=BATO` 可以创建 Cboe BZX Options Listing；
- 没有 primary venue 的 Binance Equity catalog 不创建 Listing；
- Listing 不包含 `source_id`；source provenance 保留在 Reference ingestion internals；
- Listing exchange 不自动成为所有 quote、trade 或 bar 的 Market exchange。

### 4.3 Market

`Market` 表示 Instrument 在具体交易 venue/product surface 上的 canonical 可交易关系：

```rust
struct Market {
    market_id: MarketId,
    instrument_id: InstrumentId,
    listing_id: Option<ListingId>,
    exchange_id: Exchange,
    instrument_kind: InstrumentKind,
    venue_symbol: Option<Symbol>,
    status: ReferenceStatus,
    // venue-owned trading rules when authoritative
}
```

目标 invariant：

- `exchange_id` 必须是实际 venue，而不是 DataProvider/Broker；
- `listing_id` 是可选关系；Market 不通过 Listing 推导唯一性；
- 同一上市股票可以同时具有 Nasdaq、Cboe BZX、NYSE Arca 等 Markets；
- venue 未知时不创建 `exchange:unknown` Market 作为生产 canonical identity；
- provider 未证明某个 venue-instrument relationship 时，不因 provider 能返回行情就创建
  Market；
- Market 不保存单一 provider `source_id`；
- provider symbol 不保存为 `source_symbol`；若 venue 的原生 symbol 确有业务意义，使用语义
  明确的 `venue_symbol`；
- provider product code 不继续借用 `market_type`。Canonical kind 使用
  `InstrumentKind`，provider product 留在对应业务 route；
- provider-prefixed `market_key` 删除或收敛为纯 canonical internal key，不能参与公开身份。

### 4.4 Exchange、Venue 与 reporting facility

Reference 维护稳定的 exchange/venue identity 和 provider code 到 canonical venue 的静态或
reference mapping，例如：

```text
XNAS -> exchange:nasdaq
BATS -> exchange:cboe-bzx
BATO -> exchange:cboe-bzx-options
XADF -> facility:finra-adf
```

TRF、ADF 和 off-exchange reporting facility 不应伪装成 primary Listing exchange。是否将其
建模为 `Exchange` 的一个 closed kind，或独立 `VenueKind`，在实现 slice 开始前由 Reference
domain 明确；在此之前必须保留 provider reporting code，不得有损映射为普通 exchange。

## 5. Provider 地址不进入 Reference

### 5.1 Market-owned route

Market composition 直接使用 Integration owner-defined capability 构造当前 source route：

```rust
struct MarketDataRoute {
    source_id: SourceId,
    provider_id: ProviderId,
    provider_product: ProviderProductCode,
    provider_symbol: ProviderSymbol,
    instrument_id: InstrumentId,
    scope: ObservationScope,
}
```

该类型是 Market-owned current route，不是 Reference entity，不进入 Reference DB、contract
或 lifecycle。其 caller 是 Market subscription、availability query 和 source activation。

Market composition 可以通过以下当前事实完成 provider mapping：

- Workspace source binding；
- Integration provider-native catalog/capability；
- Reference canonical Instrument/Listing/Market projection；
- provider-specific composition normalizer。

禁止从 canonical `market_id` 字符串解析 provider product/symbol。若映射需要显式配置，配置
必须同时携带 canonical target 和 provider-native address，并在 composition 启动时验证。

### 5.2 Execution-owned route

Execution 使用已有 `ExecutionRouteCandidate`、`SelectedExecutionRoute` 和
`ExecutionAttempt`，provider-native order address 属于 route snapshot：

```rust
struct ExecutionRouteCandidate {
    route_id: ExecutionRouteId,
    broker_id: BrokerId,
    account_id: AccountId,
    provider_product: ProviderProductCode,
    provider_symbol: ProviderSymbol,
    instrument_id: InstrumentId,
    destination_market_id: Option<MarketId>,
    selection_kind: RouteSelectionKind,
}
```

规则：

- Binance Equity broker catalog 可以生成 Execution route coverage，但不能生成 Binance 股票
  Exchange、Listing 或 Market；
- smart-routed broker order 的 destination 可以在提交时 unknown；
- selected destination、submitted provider 和 reported execution venue 是三个独立事实；
- provider 没有报告 execution venue 时保持 `None`，不回填 primary Listing exchange。

### 5.3 是否需要 external identifier mapping

本次不增加 Reference 公共 external identifier catalog。Provider mapping 优先留在 Market 或
Execution composition，因为它们的 product、coverage 和调用语义不同。

只有在至少两个业务模块确实需要同一稳定外部标识映射，且各自维护已产生冲突证据时，才
单独评估最小的 identity mapping：

```text
(namespace, external_value) -> canonical Instrument/Exchange
```

该映射也只能表达 identity resolution，不能表达 supported、configured、ready、可行情或
可执行，不能演化成另一个 Access catalog。

## 6. Observation identity

### 6.1 Scope

当前“所有 observation 必须有一个 canonical `market_id`”的 invariant 对 consolidated
行情不成立。目标模型采用 typed scope：

```rust
enum ObservationScope {
    Market(MarketId),
    Consolidated {
        instrument_id: InstrumentId,
        network_id: Option<String>,
    },
}
```

具体命名可在 Market slice 中按现有 domain vocabulary 调整，但必须保留两种互斥语义，
不能用空字符串、`market:unknown` 或 provider market 代替。

### 6.2 Trade

Trade 的 venue 映射规则：

- provider exchange code 能解析为已知 canonical venue/Market 时使用
  `ObservationScope::Market`；
- TRF/off-exchange trade 保留 reporting facility、tape 和 `trf_id`；
- venue code 未知时保留原始证据并进入 degraded/quarantine 路径，不能回退到 primary
  Listing 或 provider；
- `source_id=massive-equity` 仍是 observation provenance，不是 Market identity。

### 6.3 Quote

NBBO/consolidated Quote 的 bid 与 ask 可能来自不同 venue。Quote 至少需要：

```text
scope = Consolidated(instrument_id, network_id?)
bid_market_id?
ask_market_id?
source_id
```

单 venue quote 可以使用 `Market` scope。禁止用 primary Listing exchange 同时填充 bid 和
ask market。

### 6.4 Bar 与其他 aggregate

跨 venue bar、reference price、index 或 provider aggregate 通常使用
`Consolidated/Instrument` scope。只有 provider contract 明确说明按单 venue 聚合时，才使用
Market scope。

Market view key、freshness key、mmap key 和 publication contract 必须将 scope 纳入 typed
identity，不能继续假设 `(source_id, market_id)` 覆盖所有 observation。

## 7. Provider-specific 修正

### 7.1 Massive Equity

Reference mapping：

- ticker facts 创建或丰富 canonical equity Instrument；
- `primary_exchange` 只用于 Listing；
- exchange directory 用于解析 MIC、participant code、TRF/facility；
- ticker reference 不枚举该股票所有实际 Market；
- 不生成 `market:massive:equity:*`；
- Massive ingestion provenance 仅保留在 provider staging、health 和 diagnostics。

Market mapping：

- Massive ticker 是 `MarketDataRoute.provider_symbol`；
- trades 保留 `exchange`、`tape`、`trf_id` 和 participant/SIP timestamps；
- quotes 保留 bid/ask venue；
- consolidated aggregates 使用 consolidated scope。

### 7.2 Massive Options

Reference mapping：

- OCC-style contract facts 创建 canonical option Instrument；
- `primary_exchange=BATO` 创建 Cboe BZX Options Listing；
- 不生成 `listing:massive:options:*` 或 `market:massive:options:*`；
- multiply-listed venue relationships 只能由相应权威 facts 补充，不能从 provider name 推断。

Market mapping：

- `O:...` ticker 留在 Massive options route；
- consolidated options quote/trade 按 payload 中实际 venue 或 consolidated scope 归属；
- OPRA/network identity 不伪装为 execution venue。

### 7.3 Binance Equity

Reference mapping：

- Binance Equity participant 保持 Broker；
- provider catalog 可作为低权威 Instrument candidate，但不能创建 Binance Exchange、Listing
  或 Market；
- 缺失 primary listing venue 时保持 Listing absent，等待 Massive 或其他权威 Reference
  source enrichment；
- 不为满足 catalog validation 制造 `exchange:unknown` 生产记录。

Execution/Market mapping：

- tradability、quantity limits 和 broker symbol 进入 Execution route coverage；
- Binance quote capability 若配置，则由 Market route 独立表达；
- 同一 Binance symbol 在行情与执行上的 capability 不因字符串相同而合并成 Reference
  Access。

### 7.4 Binance Crypto

Binance Spot、Futures、Options 等 exchange products 不受 Binance Equity 修正规则误伤。
Integration participant 在这些产品上确实代表交易 venue 时，可以生成 canonical Binance
Markets。映射必须依据 participant role 和 product contract，而不是 provider 名称白名单。

## 8. 生命周期与持久化

Reference provider staging 可以保存：

- `source_id`；
- provider cursor、coverage 和 last-known-good；
- raw/normalized provider symbol 和 venue code；
- provider health 与 reconciliation diagnostics。

Canonical current tables、events 和 consumer projection 不应把单一 provider 写成
Instrument/Listing/Market owner。多个 provider 观察到同一 canonical fact 时，domain
reconciliation 合并业务事实；provider availability 和 runtime readiness 不推进 canonical
catalog revision。

Provider source 删除或暂停不能自动 delist canonical Instrument/Listing/Market。只有被认定为
相应 canonical fact authority 的完整 lifecycle observation 才能改变其状态。

## 9. 迁移计划

### Slice 1：冻结错误 identity 生成

- 停止 Massive options 生成 provider-prefixed Listing/Market；
- 停止 Binance Equity 生成 Binance Exchange/Listing/Market；
- 为上述映射增加 focused regression tests；
- 在新 schema 可消费前不进行长期双写。

退出条件：新 refresh 不再产生新的 provider-as-venue canonical rows。

实施记录（2026-08-17）：

- Binance Equity Broker catalog 只生成 canonical Instrument/Asset candidate，不再生成
  Binance Exchange、Listing 或 Market；
- Massive ticker/option reference 只生成 Instrument 与有权威 primary venue 时的 Listing，
  不再生成 Massive Market；
- `BATO` 映射为 Cboe BZX Options Listing venue，`OPRA` 被识别为 consolidated network，
  不再创建 `exchange:opra` Listing/Market；
- provider focused tests 和 Reference architecture guard 已覆盖上述边界。

### Slice 2：收敛 Reference canonical schema

- 从 canonical Listing/Market 移除单一 `source_id`；
- 将 provider `source_symbol` 移到业务 route；需要 venue symbol 时使用明确字段；
- 用 canonical `InstrumentKind` 替代 provider `market_type` 语义；
- 清理 provider-prefixed `market_key`；
- 更新 Reference contract、SQLite projection、events、Python model 和 CLI。

退出条件：Reference consumer projection 不需要 provider address 即可解释 canonical identity。

实施记录（2026-08-17）：

- Listing 删除 canonical `source_id`；Market 删除 `source_id`、`market_key`、provider
  `market_type` 和 `source_symbol`，改为 canonical `instrument_kind` 与 `venue_symbol`；
- Reference FlatBuffers、SQLite v4 projection、Rust/Python contract、query 和 CLI 已同步；
- 旧 projection schema 不做字符串 rename，而是拒绝并从 provider source 重建。

### Slice 3：Market route 与 observation scope

- Market composition 从 source binding、Integration capability 和 canonical projection 构造
  route；
- 保留 Massive quote/trade venue、tape、TRF 和 timestamps；
- 引入 typed Market/Consolidated scope；
- 更新 subscription validation、view keys、freshness、publication、mmap、replay 和 Python
  strategy contract；
- 删除从 `market_id` 解析 provider 的路径。

退出条件：venue-specific 和 consolidated observations 都不需要伪 Market ID。

实施记录（2026-08-17）：

- Market observation、freshness、view/mmap key、event wire、history manifest 和 Python
  strategy model 已采用 typed `ObservationScope`；OrderBook 保持 canonical Market scope；
- Massive NBBO/aggregate 使用 consolidated scope，并保留 bid/ask venue、tape；trade 保留
  venue/TRF/timestamp evidence，无法完成 canonical venue join 时进入 quarantine；
- Market collection 与 strategy command 可直接建立 instrument-scoped route；Massive source
  binding 不再要求或发布虚构 exchange；
- 历史下载对 Massive aggregate 不再要求 `market_id`，Massive trade 未配置 venue join 时明确
  拒绝而不是回退到 Listing/provider。

### Slice 4：Execution broker route

- Binance Equity provider address 只存在于 Execution candidate/selected/attempt facts；
- destination Market 与 reported execution venue 保持可选且互不替代；
- 删除任何从 Binance Equity canonical Market 反推 broker route 的路径；
- 验证 Account observation 不借用 Market source 作为通用 provider identity。

退出条件：Execution 可以在没有 `market:binance:equity:*` 的情况下列出、选择和审计
Binance broker route。

实施记录（2026-08-17）：

- Execution normalized config 增加 instrument address 与可选 `destination_market_id`；
- 显式 Binance Equity route 在无 canonical Market 时仍可生成 candidate，participant kind 为
  Broker；只有真实 venue 与 participant 匹配的 Reference Market 才可生成 exchange route；
- Execution quote dependency 明确只消费 market-scoped current view；consolidated 回测输入保留
  typed scope，fill 的 execution market 仍来自选中 order route，而非行情来源。

### Slice 5：数据重建与依赖迁移

- schema version 明确拒绝旧 provider-as-market canonical shape；
- 从 provider staging/reference sources 重建 Reference catalog，不做字符串原地 rename；
- 对可确定的新 identity 生成一次性 migration report；
- `market:binance:equity:*` 没有真实 venue 时只迁移到 Instrument，不能制造 replacement
  Market；
- 历史 Execution audit 保留当时 submitted provider address，并通过 correction/migration
  metadata 标记错误 canonical destination，不伪改已发生事实；
- replay/dataset 中的旧伪 Market 必须显式迁移到真实 Market 或 consolidated scope。

退出条件：active Reference catalog 不含 provider-as-venue rows，历史数据没有静默重解释。

实施记录（2026-08-17）：

- Reference projection schema version 变化会废弃并重建旧 canonical projection，不对旧 ID
  原地改名；provider staging 保留 provenance；
- 仓库内 Massive replay fixture、option acquisition target、analytics 和示例 strategy 已迁移到
  consolidated scope；不存在可被静默继续消费的 `market:massive:*`/`market:opra` fixture；
- `scripts/check/reference_route_separation_report.py` 可只读扫描 SQLite/JSONL，逐条区分
  consolidated migration、删除伪 Market 后保留 Execution route，以及需要人工 venue join 的
  记录；工具不做危险的字符串 rename；
- legacy provider-shaped rows仅能作为显式 migration/architecture test data 出现，active
  provider mapping 有静态 guard 防止重新生成。

## 10. 测试与架构检查

### 10.1 必要行为测试

至少覆盖：

1. AAPL primary Listing 为 XNAS，同时 trade venue 为 BATS；二者保持不同；
2. AAPL TRF trade 保留 XADF/TRF evidence，不映射为 Nasdaq；
3. SPY option `primary_exchange=BATO` 不生成 Massive Listing/Market；
4. Binance Equity broker catalog 不生成 Binance Exchange/Listing/Market；
5. Binance crypto exchange market 仍能正常生成；
6. consolidated bar 没有伪 `market_id`；
7. NBBO quote 可以包含不同 bid/ask Markets；
8. 同一 canonical Instrument 可以同时被 Massive Market route 和 Binance Execution route
   寻址，而 Reference 不保存两个 Access；
9. provider pause/unavailable 不改变 canonical lifecycle；
10. refresh/rebuild 幂等且不会重新产生旧 ID。

### 10.2 静态搜索

迁移期间持续执行：

```text
rg -n "market:massive|listing:massive" crates kairospy schemas tests examples strategies
rg -n "market:binance:equity|listing:binance:equity|exchange:binance" crates kairospy schemas tests
rg -n "MarketDataAccess|ExecutionAccess" crates kairospy schemas tests
rg -n "source_id|source_symbol|market_type|market_key" crates/modules/reference
rg -n "split.*market_id|trim_start_matches.*market|parse.*market_id" crates/modules/market crates/modules/execution
rg -n "exchange|tape|trf_id|bid_exchange|ask_exchange" crates/platform/integration/src/services/participants/massive
```

`exchange:binance` 的残留必须逐项确认属于真实 Binance exchange product，而不是 Equity
broker surface。`source_id` 等字段的残留必须属于 provider staging/health，而非 canonical
entity。

### 10.3 仓库验证

每个 slice 运行 focused tests，完成前运行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

如全仓检查被无关既有失败阻塞，记录精确失败并运行受影响模块的最窄完整检查。

## 11. 非目标与禁止项

本文不做以下事情：

- 不恢复 Reference `MarketDataAccess` 或 `ExecutionAccess`；
- 不新增统一 ProviderAccess、RouteManager、CapabilityRegistry、port 或 gateway；
- 不让 Reference 持久化 runtime supported/configured/ready/fresh；
- 不让 Market 或 Execution 成为 canonical Instrument/Listing/Market 的第二 owner；
- 不从 provider name、symbol 格式或 canonical ID 字符串猜测能力；
- 不把 primary Listing exchange 当作默认 execution venue；
- 不把 OPRA、SIP、Massive 或 broker smart route 伪装成交易所；
- 不透明修改 immutable execution audit；
- 不为未知事实填充 `unknown` canonical Market 后继续正常交易或发布；
- 不同时保留旧伪 Market 与新 scope 的长期兼容双路径。

## 12. 完成定义

本设计只有同时满足以下条件才算实施完成：

- Reference 只发布可独立解释的 canonical Instrument、Listing、Market 和 venue facts；
- Listing exchange 与实际 Market/execution venue 在模型、contract 和测试中明确分离；
- Massive 与 Binance provider/broker identity 不再进入股票/期权 canonical Market ID；
- Reference 不恢复两个 Access，也不以新名字复制它们；
- Market 可以独立组合并解释 provider-native 行情 route；
- Execution 可以独立组合并审计 broker-native execution route；
- venue-specific、TRF 和 consolidated observations 都保留其真实 identity semantics；
- active catalog 和新生成数据不含 provider-as-venue identity；
- 旧 catalog、snapshot、dataset 和历史事实完成显式迁移或 correction 标记；
- focused behavior、architecture、contract 和 repository checks 通过，或精确记录无关失败。

## 13. 最终验证记录（2026-08-17）

- `cargo test -p kairos-market -p kairos-execution`：通过（Market 56 unit + 41
  application/architecture/behavior；Execution 108 unit + CLI/server/21 architecture）；
- `cargo test -p kairos-reference -p kairos-reference-contract -p kairos-integration`：通过
  （仅显式 live/scale tests ignored）；
- `uv run pytest -q`：本设计相关测试全部通过；全套 384 passed、8 skipped，剩余一个无关
  失败是 Account CLI 帮助测试仍期待已被并行重构删除的 `snapshot` command；
- `cargo test --workspace`：被无关的 Conflux 并行重构阻塞，`context.rs`/`process.rs` 仍引用已
  删除的 `ConfluxSystem`；
- `cargo fmt --all -- --check`、`git diff --check`、crate layout check、相关 Python Ruff 与
  compileall：通过；
- provider-as-venue、Access 恢复、canonical ID 解析路径和业务 publisher JSON adapter 静态
  搜索已审计；命中 JSON 的位置均为显式 control/CLI boundary。
