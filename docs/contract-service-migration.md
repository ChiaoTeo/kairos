# Contract / Service crate migration

本文档定义业务 crate 从当前平铺结构迁移到按模块分组的
`contract/service` 结构的实施方案。目标是让每个模块只有一个稳定的
跨模块入口，同时保持现有业务行为和运行方式。

## 1. 目标结构

```text
crates/
  transport/                    package: kairos-transport
  protocol/                     package: kairos-protocol
  integration/                  package: kairos-integration
  workspace/                    package: kairos-workspace
  business/
    reference/
      contract/                 package: kairos-reference-contract
      service/                  package: kairos-reference-service
    market/
      contract/                 package: kairos-market-contract
      service/                  package: kairos-market-service
    account/
      contract/                 package: kairos-account-contract
      service/                  package: kairos-account-service
    risk/
      contract/                 package: kairos-risk-contract
      service/                  package: kairos-risk-service
    execution/
      contract/                 package: kairos-execution-contract
      service/                  package: kairos-execution-service
```

`contract` 是模块唯一的跨进程边界，统一定义 commands、queries、results、
errors、events、snapshots、watermarks、schema versions，以及 mmap 和事件
流的读写适配器。

`service` 负责领域状态和运行时实现，内部保持标准结构：

```text
src/
  bin/
  composition/
  application/
  services/
  domain/
```

外层的 `business/` 只是仓库分组目录，不是新的业务层，也不引入额外的
runtime 或 coordinator。每个模块仍然只有一个业务 owner，且只保留
`contract` 和 `service` 两个 Cargo crate；因此可以独立编译，同时又能从
目录上清楚地区分业务 crate 与通用基础设施 crate。

## 2. 依赖和所有权

```text
kairos-{module}-service
    -> kairos-{module}-contract
    -> kairos-transport / kairos-protocol
    -> optional kairos-integration
```

其他模块只能依赖目标模块的 contract：

```text
market-service       -> reference-contract
execution-service    -> account-contract
execution-service    -> market-contract
execution-service    -> reference-contract
execution-service    -> risk-contract
```

禁止依赖其他模块的 service、`domain`、`services`、数据库、Actor 状态，或
直接访问其 mmap、event socket 和内部 UDS JSON。

`composition` 负责选择和组装 contract 的具体实现，但不能绕过 contract。
它负责路径、地址、identity、provider、运行模式和依赖注入；application 负责
业务编排；domain 不依赖 contract、transport、FlatBuffers 或其他业务模块。

## 3. Contract 和 service 的边界

contract 定义“模块对外提供什么、接受什么”：

```text
command / query / response / error
event / snapshot / watermark
schema / version
mmap reader / publisher
event stream subscriber / publisher
```

service 定义“模块内部如何实现”：

```text
domain state / actor / persistence
application use case
composition and runtime setup
private services and integrations
```

contract 不得暴露 service domain entity、数据库记录、SDK payload、Actor
引用、可变内部状态或 service-owned error。

## 4. Reference 迁移映射

| 当前代码 | 目标位置 | 迁移说明 |
|---|---|---|
| `kairos-reference/src/domain` | `business/reference/service/src/domain` | 保持私有领域类型 |
| `kairos-reference/src/application` | `business/reference/service/src/application` | 保持业务用例边界 |
| `kairos-reference/src/composition` | `business/reference/service/src/composition` | 保持运行时组装 |
| `kairos-reference/src/services/storage` | `business/reference/service/src/services` | 保持私有持久化实现 |
| `services/publication/encoder.rs` | `business/reference/contract/src/encoding.rs` | 去除 service domain 依赖 |
| `services/publication/mmap.rs` | `business/reference/contract/src/transport/reference_mmap.rs` | 使用 contract snapshot 类型 |
| `services/publication/aeron.rs` | `business/reference/contract/src/transport/reference_aeron.rs` | 使用 contract event 类型 |
| `services/publication/mod.rs` | `business/reference/contract/src/transport/mod.rs` | 暴露稳定 contract API |
| composition 中的 writer wrapper | `business/reference/service/src/composition` | 保留配置和依赖注入 |
| `kairos-reference/src/bin` | `business/reference/service/src/bin` | 只保留输入适配和启动 |

不能机械搬运整个 `publication` 目录。编码器和跨进程 transport 进入
contract，但 domain 到 contract 的转换、运行配置和 publisher 注入仍属于
service composition。

## 5. Contract 类型拆分

当前发布代码直接接收 service domain：

```rust
publish(&ReferenceCatalog)
publish(&ReferenceCatalog, &[LifecycleEvent])
```

迁移后只接收 contract 自己的公共类型：

```rust
publish(&ReferenceSnapshot)
publish(&ReferenceChanged)
```

转换路径为：

```text
ReferenceCatalog / LifecycleEvent
        -> service application/composition
ReferenceSnapshot / ReferenceChanged
        -> reference-contract encoding and transport
FlatBuffers / mmap / event stream
```

contract 适配器可以依赖 `kairos-transport` 和 `kairos-protocol`，但不得依赖
`reference-service::domain`、`reference-service::services` 或 service 错误。

## 6. 分阶段迁移

### 阶段 A：冻结边界

1. 列出每个模块现有 snapshot、event、command、query 和 application API。
2. 搜索跨模块的 `services/`、私有文件、直接 mmap 和 UDS JSON 依赖。
3. 确定 contract 类型名称、schema version 和 watermark 语义。
4. 暂不删除旧 crate，建立迁移期依赖清单。

### 阶段 B：建立 contract

1. 创建 `crates/business/{module}/contract`，package 名称为
   `kairos-{module}-contract`。
2. 创建 snapshot、event、command、query、result、error 类型。
3. 将 FlatBuffers 编码和 KSS1/mmap、event stream 适配放入 contract。
4. 确认 contract 不依赖对应 service crate。

### 阶段 C：迁移 Reference publication

1. 将 `publication/encoder.rs` 改为只接受 contract 类型。
2. 将 `publication/mmap.rs` 移到
   `business/reference/contract/src/transport/mmap.rs`。
3. 将 `publication/aeron.rs` 移到
   `business/reference/contract/src/transport/aeron.rs`。
4. 在 service application/composition 中完成 domain 到 contract 的转换。
5. 在 composition 中保留路径、identity、stream id 和 publisher 创建。
6. 让 Market、Execution 等消费者切换到 contract client。

迁移期允许 service 保留薄 wrapper，但 wrapper 只能调用 contract，不能保留
第二套编码和发布逻辑。

### 阶段 D：迁移 service

1. 将旧业务 crate 移到 `crates/business/{module}/service`。
2. 将 package 名称改为 `kairos-{module}-service`。
3. 保持 `bin/composition/application/services/domain` 结构。
4. 将 service 对 contract 的依赖改为 path dependency。
5. 删除 service 中重复的 wire schema、编码器和 transport publisher。
6. 更新 bin、测试 fixture 和 workspace 启动脚本。

### 阶段 E：切换跨模块调用

建议顺序：

```text
Reference -> Market
Account / Market / Reference / Risk -> Execution
Risk -> Execution authoritative reservation
Account -> Execution authoritative authorization
```

每次切换必须完成：接入 contract client、删除旧直接访问、增加 watermark 和
gap recovery、确认无生产调用者后删除旧适配代码。

## 7. 兼容、回滚和验证

迁移期间可以保留 `/v1/snapshot` UDS JSON、旧 server binary wrapper 和
service 内部 contract wrapper；它们只能用于诊断、CLI、低频兼容路径，不能
成为新的跨模块生产数据面。

如果低频兼容查询暂时仍通过 UDS HTTP/JSON，客户端、请求参数和响应 read
model 必须放在目标模块的 `contract` crate 中。`service` 不得自行建立
`UnixStream`、拼接 URL、解析 JSON 或读取另一个模块的 `/v1/snapshot`。
稳定状态读取统一迁移到 contract 提供的 mmap reader；事件变化统一通过
contract 提供的 event subscriber。

回滚时只回滚 workspace package 路径和 composition wiring，不回滚已经发布的
contract schema。schema 必须保持兼容或通过版本号演进。

每个 contract crate 必须：

- 独立 `cargo check` 和 `cargo test`；
- 不依赖对应 service crate；
- 用相同 fixture 验证 Rust/Python 解码；
- 验证 file identifier、schema version、generation、sequence；
- 覆盖重复事件、乱序事件和 gap recovery。

每个 service crate 必须：

- 只通过 contract 发布跨进程数据；
- 由 composition 统一组装具体实现；
- application 不依赖 composition；
- domain 不依赖 contract、transport、FlatBuffers 或其他业务模块；
- 不存在第二个 mutable state owner。

迁移完成后执行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

并搜索：直接 service import、跨模块私有路径、直接 mmap、内部 UDS JSON、重复
FlatBuffers encoder 和重复 snapshot publisher。

## 8. 完成标准

一个模块满足以下条件才算迁移完成：

1. `crates/business/{module}/contract` 和 `crates/business/{module}/service` 独立存在；
2. 所有跨模块读写都经过 `kairos-{module}-contract`；
3. mmap/event publisher 与 reader 位于 contract 边界；
4. domain 到 contract 类型的转换位于 service application/composition；
5. service 不向外暴露私有 services、domain 或 transport 实现；
6. 旧平铺 crate 和重复适配逻辑已删除；
7. focused tests 和 workspace checks 全部通过。

## 9. 当前落地状态

当前 workspace 已完成以下迁移，不再保留旧平铺业务 crate 的 workspace
成员：

| 模块 | 已落地内容 |
|---|---|
| Reference | contract model、FlatBuffers encoder、mmap publisher/reader、Aeron event publisher，以及低频 query client |
| Market | contract model、snapshot publisher/reader、Reference change decoder、Aeron subscriber、mmap snapshot publisher |
| Account | contract model、FlatBuffers encoder、mmap snapshot publisher，server 已切换到 mmap 发布 |
| Risk | contract model、FlatBuffers encoder、mmap snapshot publisher，server 已切换到 mmap 发布 |
| Execution | contract model、四个依赖的 typed readers/clients、snapshot publishers、dependency watermarks，以及独立 service crate |
| Python | `kairospy/infrastructure/contracts` 下的通用 KSS1 reader 和五个模块 facade |

迁移期仍允许 service 使用 UDS 作为低频命令/查询和控制面，但 UDS 客户端、
请求参数和响应 read model 必须位于目标 contract crate。新代码不得在 service
中直接建立跨模块 socket、拼装跨模块 JSON 或解析跨模块 JSON。Account/Risk
的现有低频命令已经分别由目标 contract client 封装；它们仍属于控制面，不是
mmap/event 高吞吐数据面。
