# Reference 模块交付定义与最终验收条件

本文定义 Reference 模块的最终业务职责、对外交付内容、边界和验收条件。

Reference 的最终定位是：

> 维护并发布全局金融参考目录及其生命周期事实，为 Market、Execution、Account 和策略提供统一、可恢复、带版本水位的身份与交易规则；Reference 不负责实时行情、账户状态、风险决策或订单执行。

本文中的“最终验收”指模块达到可以作为其他业务模块稳定依赖的状态，而不是仅仅“代码能够运行”。每一项都必须有自动化测试、契约测试或可重复的运行验证。

## 1. 交付范围总览

Reference 必须交付以下能力：

1. 全局参考目录：Entity、Exchange、Asset、Instrument、Listing、Market。
2. Market 的静态交易规则和有效期。
3. FinancialProduct 目录和 ExecutionAccess 目录。
4. 多 provider 全量同步、标准化、校验、合并和冲突处理。
5. 单写入者目录 reconcile 和持久化恢复。
6. 当前目录 snapshot。
7. 目录变化事件和生命周期历史。
8. generation、event sequence 和 freshness 水位。
9. 目录查询、唯一市场解析和生命周期查询。
10. provider 健康、刷新、发布和 outbox 运营能力。
11. 面向 Market、Execution 等消费者的稳定 contract。

Reference 不交付以下能力：

- Quote、Trade、Bar、Greeks、Rate、OrderBook 等实时行情；
- 交易所订阅和行情 feed 状态；
- 下单、撤单、订单生命周期和成交；
- 账户余额、持仓、资金划转、收益结算；
- 风险评估、预算和 reservation；
- provider SDK、鉴权和具体 HTTP/WebSocket 实现。

## 2. 统一验收原则

所有 Reference 对外数据必须同时满足：

- 有稳定的业务 ID；
- 有明确的拥有者和来源；
- 能从持久化状态恢复；
- 有 generation 或 event sequence 水位；
- 不暴露 provider payload、SQLite record、Actor 或服务实例；
- 快照和事件可以通过水位关联；
- provider 失败不会被误解释为权威空目录；
- 其他模块只能依赖 `kairos-reference-contract`。

## 3. 领域目录交付验收

### 3.1 Entity / Exchange

Reference 负责维护实体和交易场所的标准身份、名称、类型和状态。

最终验收条件：

- 相同业务实体在重复刷新后保持相同 `entity_id` / `exchange_id`；
- Listing 和 Market 引用的 exchange 必须存在，否则 refresh 被拒绝；
- exchange 的状态可以被查询，并进入对应 snapshot；
- exchange 变化可以判断为 unchanged 或 changed，不得产生重复记录；
- 进程重启后 entity/exchange 目录、generation 和状态保持一致；
- 查询接口不暴露 provider 原始字段或 provider response。

### 3.2 Asset

Reference 负责维护资产身份、代码、资产类别和状态。

最终验收条件：

- `asset_id` 和 `code` 非空且在目录内满足唯一性规则；
- Market、FinancialProduct、ExecutionAccess 引用的 Asset 必须存在；
- 同一资产重复同步不会增加 generation；
- Asset 的新增、修改、停用能够被 snapshot 查询；
- 管理员 upsert 如果作为正式能力，必须同时完成 catalog、generation、事件和 outbox 的一致提交；
- 管理员 upsert 失败时，旧目录保持不变；
- 进程重启后管理员变更仍然存在。

### 3.3 Instrument

Reference 负责维护标准 Instrument 身份及其金融产品属性。

最终验收条件：

- Instrument 具有稳定的 `instrument_id`，不能以 provider symbol 作为跨模块身份；
- `instrument_type`、`product_family`、expiry、strike、option_right 等字段能够被 contract 完整表达；
- `underlying_instrument_id` 指向不存在的 Instrument 时，refresh 被拒绝；
- 期权的 expiry、strike、option right 组合满足领域校验；
- 相同 Instrument 的重复刷新不产生重复实体；
- Instrument 的生命周期或内容变化可以通过既定的 Reference change 语义被消费者识别；
- Market 和 Execution 可以只依赖 Reference contract 获得 Instrument 事实。

### 3.4 Listing

Reference 负责维护 Instrument 在 Exchange 上的挂牌关系及有效区间。

最终验收条件：

- Listing 引用的 Instrument 和 Exchange 必须存在；
- `effective_from` 和 `effective_to` 区间合法；
- 同一 Listing 的重复刷新保持稳定 ID；
- Listing 的状态和有效期可以按指定时间点查询；
- Listing 被移除或失效时，旧记录不会从历史中消失；
- Listing 变更不会被错误当成实时行情事件；
- snapshot 中的 Listing 与对应 Instrument、Exchange 关系一致。

### 3.5 Market 定义和交易规则

Reference 负责 Market 的静态定义和执行前置规则，不负责 Market 的实时数据。

最终验收条件：

- Market 同时具备稳定的 `market_id`、`market_key`、Instrument、Listing 和 Exchange 关系；
- base asset、quote asset、market type、asset type 可被查询；
- price tick、quantity tick、precision、minimum quantity、minimum notional、contract size 可被 contract 完整读取；
- 交易规则字段不能出现非法负值或无法解析的数值；
- Market 引用的 Instrument、Listing、Exchange、Asset 均存在；
- `resolve_market` 在唯一匹配时返回单条记录，在零条或多条匹配时返回明确错误；
- Market 的历史有效区间可以通过 `as_of` 查询；
- Market snapshot 不包含 Quote、Trade、OrderBook 等实时行情状态；
- Execution 可以使用该规则完成 preflight 所需的静态校验。

### 3.6 FinancialProduct

FinancialProduct 作为 Reference 目录能力，只表示可发现、可描述的产品事实，不负责申购、赎回或收益结算。

最终验收条件：

- Product 具有稳定的 `product_id` 和 provider product ID；
- 产品类型、名称、关联资产、币种、金额限制、APR、锁定期、到期时间和状态可被读取；
- 关联的 Asset、currency Asset 和 issuer（如有）满足引用校验；
- 产品目录变化可以通过 snapshot 查询；
- 如果产品变化对消费者有意义，则进入正式 change event；
- Reference 不持有产品余额、订单、申购状态或收益账本；
- 产品的 provider API payload 不穿透到 Application 或 Contract。

### 3.7 ExecutionAccess

ExecutionAccess 表示某个标准 Instrument 的 provider 执行路径，不是第二套 Market。

最终验收条件：

- Access 具有稳定的 `access_id`；
- Access 必须引用存在的 Instrument；
- provider、product family、provider symbol、settlement asset 和有效区间可被读取；
- 同一个 Instrument 可以拥有多个 provider execution access；
- Access 变更不会覆盖或伪装成 Market 变更；
- Execution 可以通过 Reference contract 查询可用执行路径；
- Reference 不负责实际下单、订单状态或 provider order handle。

## 4. Provider 同步交付验收

### 4.1 Integration 与 Reference 的边界

Integration 负责 provider 连接、鉴权、原始 payload 读取和标准化；Reference 负责将标准化结果变成 Reference domain。

最终验收条件：

- Reference domain 不依赖 provider SDK 类型；
- Application API 不暴露 provider payload、HTTP response 或连接实例；
- 每个 provider 都通过统一的 Reference source seam 接入；
- provider 选择、endpoint、credential 和模式只在 composition 中决定；
- 新增 provider 时不需要修改其他业务模块的查询契约。

### 4.2 全量同步

最终验收条件：

- 每次 provider refresh 的语义是 configured provider/product 的完整目录；
- provider 不接受具体 symbol 作为 Reference 全量同步过滤条件；
- pagination 必须收集到完整结果或明确失败；
- 未完成分页的结果不得进入正式 reconcile；
- refresh 结果为空时，系统可以区分“provider 权威返回空目录”和“请求失败”；
- 全量同步完成后，缺失记录才允许进入 delist/deactivate 判断；
- 同一输入目录重复刷新不会产生 generation 或事件抖动。

### 4.3 多 provider 合并和冲突

最终验收条件：

- 每条内部记录都能追溯到来源 provider；
- 同 ID 冲突有稳定、可测试的优先级规则；
- 冲突不会静默地由最后写入者覆盖；
- 冲突至少进入日志、健康信息或诊断结果；
- 合并后的 catalog 通过统一领域校验后才进入 Actor；
- provider 的来源信息不会破坏跨模块 canonical ID。

### 4.4 Provider 失败和 last-known-good

最终验收条件：

- provider 请求失败时保留该 provider 最近一次成功目录；
- 进程重启后，last-known-good 目录仍可恢复；
- provider 暂时失败不会把其全部 Market 标记为 delisted；
- provider 连续失败会反映在 provider health 和 freshness 中；
- provider 返回权威空目录时，才允许对其旧记录执行下架；
- 恢复成功后，Reference 能重新 reconcile，而不需要人工清库；
- provider circuit open、恢复探测和 backoff 都有自动化测试。

## 5. Actor、状态和持久化验收

### 5.1 单一可变状态 Owner

最终验收条件：

- `ReferenceActor` 是 catalog 可变状态的唯一 Owner；
- provider worker 不直接修改 catalog；
- publisher 不直接修改 catalog；
- server、CLI、read model 都通过 Application 访问状态；
- 不存在第二套长期维护的 catalog state owner；
- 并发 refresh 被串行化或明确拒绝，不会产生 generation 回退。

### 5.2 SQLite 恢复

最终验收条件：

- 进程重启后可以恢复 catalog、generation、event_sequence 和生命周期历史；
- WAL、busy timeout 和持久化配置符合运行要求；
- lifecycle history 是 append-only；
- 生命周期查询支持有界分页，不会每次加载无限历史；
- provider sync state 与业务 catalog 分离保存；
- SQLite 损坏或 schema 不兼容时返回明确的 Persistence error；
- 持久化失败时内存中的未提交状态不会被当作已提交状态继续发布。

### 5.3 原子 refresh commit

一次有变化的 refresh 必须原子提交：

1. 当前 catalog；
2. 新生成的 lifecycle history；
3. pending publication outbox；
4. generation 和 event sequence。

最终验收条件：

- 事务提交前进程崩溃时，旧状态和旧 outbox 仍然一致；
- 事务提交后进程崩溃时，catalog、history 和 outbox 都可恢复；
- 不会出现 catalog 已更新但事件不存在；
- 不会出现事件已存在但 catalog 没有对应变化；
- outbox 可以逐事件确认，不会因为一个批次失败而删除整个队列。

## 6. Lifecycle 和事件交付验收

### 6.1 事件范围

项目必须明确选择以下一种契约：

- `reference.events` 是完整 Reference change stream；或
- `reference.events` 只保证 Market lifecycle。

推荐完整 Reference change stream。

最终验收条件：

- 契约文档明确列出事件覆盖的实体类型；
- 每种事件都有稳定 event type 和 payload 语义；
- 事件包含 generation、event sequence、event time 和受影响对象 ID；
- 事件不包含 provider 原始 payload；
- 消费者可以根据事件知道何时需要重新读取完整 snapshot。

### 6.2 Sequence 和 replay

最终验收条件：

- event sequence 在重启后单调递增且不复用；
- 生命周期历史可以按 sequence 和时间分页；
- replay 按持久化顺序返回事件；
- event publication 重试不会产生重复业务事件；
- 消费者可以使用 `(stream_id, sequence)` 做幂等处理；
- 发现 gap、旧水位或非法事件时，可以回到完整 snapshot 恢复；
- event stream 没有消费者时，不保留没有明确语义的伪事件总线。

### 6.3 管理变更

最终验收条件：

- 正式的 asset/instrument/listing 管理命令与 provider refresh 使用同一提交路径；
- 管理变更拥有 generation；
- 管理变更按约定产生 event；
- 管理变更进入 outbox；
- snapshot、event 和 SQLite 状态的 watermark 一致；
- 发布失败不会回滚已提交的当前状态，但必须保留 pending outbox。

## 7. Snapshot 和 Contract 交付验收

### 7.1 Contract 边界

最终验收条件：

- 其他业务模块只依赖 `kairos-reference-contract`；
- Contract 不依赖 `kairos-reference-service`；
- Contract 不包含 Actor、SQLite、provider client 或服务错误；
- Contract 提供 typed snapshot reader；
- Contract 提供 typed event decoder/subscriber；
- Contract 提供必要的 query/command DTO 和错误类型；
- Contract 校验 file identifier、schema version、payload 完整性和 snapshot generation。

### 7.2 Snapshot 完整性

最终验收条件：

- catalog、entities、assets、instruments、listings、markets、financial_products、execution_accesses 八个 view 都可读取；
- 所有 view 的 generation 一致；
- manifest 只有在所有 view 发布成功后才可见；
- manifest generation 与各 view generation 不一致时，客户端拒绝消费；
- manifest 使用临时文件、fsync 和原子 rename；
- generation 没有变化时不重复编码 snapshot；
- snapshot slot 不足时返回明确错误，不产生部分可消费快照。

### 7.3 Market 消费 Reference

最终验收条件：

- Market 启动时先从 Reference snapshot 建立本地 projection；
- Reference event 到达后，Market 按 watermark 更新本地 projection；
- 发现 event gap 时重新读取 Reference snapshot；
- 动态订阅只依赖 Market 本地 Reference projection，不在每个 tick 读取 Reference JSON；
- Reference snapshot 是 Market 数据面的唯一恢复来源；不存在 snapshot、snapshot 无法读取或 watermark 不一致时，Market 必须进入 projection error/stale，禁止回退到 Reference UDS 查询；
- Reference 恢复失败时，Market 明确进入 projection error 或 stale 状态；
- Market 不直接读取 Reference SQLite、服务内部类型或 mmap 原始格式。

### 7.4 Execution 消费 Reference

最终验收条件：

- Execution 通过 Reference contract 读取 Market 定义和交易规则；
- preflight 记录使用的 Reference generation/event sequence；
- Reference snapshot 是 Execution 数据面的唯一来源；snapshot stale 或不完整时，Execution 按 freshness policy 拒绝或降级，禁止回退到 Reference UDS 查询；
- Execution 不自己维护第二套市场规则；
- Execution 不把 Reference snapshot 当作账户授权、Risk reservation 或订单写入命令。

## 8. Application、CLI 和 Server 验收

### 8.1 Application API

最终验收条件：

- Application 暴露业务请求、查询、结果和错误；
- Application 不暴露 provider client、SQLite record、publisher 或 Actor；
- `refresh`、`query`、`resolve_market`、lifecycle query 和 read model 语义稳定；
- `ReferenceApplication` 和 `ReferenceReadModel` 的查询结果不发生逻辑漂移；
- Application 可以被 server、CLI 和测试 fixture 复用。

### 8.2 Server

最终验收条件：

- server 只负责输入适配、生命周期和调用 Application；
- server 不维护第二套 catalog；
- server 只提供 health、provider、refresh、publish、管理变更和 stop 等控制面；目录、市场、记录和生命周期数据不再通过 UDS 查询；
- 控制队列有界，满载时立即拒绝，而不是无限堆积；
- provider refresh 不阻塞 health 和控制面；
- server 重启不会丢失已提交 catalog 或 pending publication。

### 8.3 CLI

最终验收条件：

- CLI 与 server 使用相同的 Application 查询语义；
- CLI 支持一次性 refresh、query、markets、show 和事件查询；
- 结构化结果写入 stdout，错误返回非零退出码；
- CLI 不复制业务 reconcile 逻辑；
- CLI 的 provider 选择和凭证处理符合 Workspace 规则。

## 9. 运营和可观测性验收

最终验收条件：

- 可以观察每个 provider 的 fetch latency、last attempt、last success、failure count 和 stale 状态；
- 可以观察 refresh run duration；
- 可以观察 reconcile duration；
- 可以观察 SQLite commit duration；
- 可以观察 snapshot payload size 和 publication latency；
- 可以观察 outbox depth；
- 可以观察 control queue depth；
- health/readiness 能区分 ready、degraded、stale、failed；
- provider 局部失败不会把整个服务错误地标记为健康；
- 日志中包含 actor、provider、generation、event sequence 和 operation 类型。

## 10. 业务边界验收

以下检查必须通过：

- Reference 中没有 Quote、Trade、Bar、Greeks、Rate、OrderBook 的可变业务状态；
- Reference 中没有订单、成交、余额、持仓或风险 reservation 状态；
- Market 不拥有 Reference canonical catalog；
- Execution 不拥有第二套 Market 交易规则；
- Reference UDS 不提供目录、市场、生命周期或单记录查询；这些数据只能通过 mmap snapshot 读取；
- 其他模块没有导入 Reference `services/`、`domain/` 或 server 私有文件；
- Domain 不依赖 Integration、Transport、FlatBuffers 或其他业务模块实现；
- provider SDK 类型不会穿透到 application 或 contract；
- 没有为了统一依赖注入而新增无业务意义的 protocol mirror、manager 或 coordinator。

## 11. 最终验证命令

Reference 完成交付前，至少执行：

```text
cargo test -p kairos-reference-contract -p kairos-reference-service -p kairos-integration
cargo fmt --all -- --check
git diff --check
cargo test --workspace
uv run pytest -q
```

同时应执行静态检查：

```text
rg -n "reference.*services|kairos_reference::services|reference.sqlite|reference\.manifest" crates/business crates/kairos-integration kairospy
```

静态检查的目标不是禁止所有匹配，而是确认：

- 跨模块没有绕过 `kairos-reference-contract`；
- SQLite 只在 Reference composition/services 内使用；
- manifest 只由 Reference contract writer 发布、由 contract reader 消费；
- 没有重复的 catalog state owner。

## 12. 最终交付判定

Reference 只有同时满足以下条件，才算完成交付：

1. 目录实体和边界已经冻结；
2. 全量同步、provider 失败、last-good 和冲突规则有自动化测试；
3. catalog、lifecycle、outbox 能原子恢复；
4. snapshot 八视图和 manifest 能一致读取；
5. event sequence、generation 和 gap recovery 已验证；
6. Market 和 Execution 已通过 Reference contract 消费；
7. CLI、server、health 和运营指标可用；
8. 运行时和静态架构检查通过；
9. 不存在跨模块私有实现依赖；
10. 所有管理变更都遵守同一套 generation、event、outbox 和 snapshot 规则。

满足以上条件后，Reference 才可以被视为其他业务模块可信赖的基础目录，而不是一个“能从交易所拉取 exchangeInfo 的同步脚本”。
