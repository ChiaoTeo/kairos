# Workspace 作用域与 Market Runtime 设计

状态：架构决策

日期：2026-08-07

本文补充
[`account-execution-workspace-design.md`](account-execution-workspace-design.md)，
专门说明 Workspace、Launch、Launch Instance 和 Market Runtime 的作用域关系。

## 1. 总体结论

系统不新增一种独立的“Launch Workspace”。Workspace 仍然是项目级资源边界；Launch 是一次运行的逻辑定义；Launch Instance 是一次真实运行的隔离边界。

`Market Instance` 不是额外的业务抽象层。它表示一个正在运行的 Market runtime，通常就是一个 `kairos-market-server` 进程及其运行状态。

更具体地说，Market 进程不判断自己属于 shared 还是 instance。它只接收一个已经由
System/launch 选择好的 Runtime Context，并在该 context 下创建 socket、snapshot、日志
和状态资源。shared 与 instance 的选择只存在于 launch composition 和 Strategy 的 endpoint
解析中；Market application/domain 不携带这个部署语义。

Market 是否 Workspace 级共享，取决于运行模式：

- live 模式允许由一个 Workspace 级的 `kairos-market-server` 复用交易所连接并向多个策略分发行情；
- backtest 模式必须由每个 Launch Instance 独立运行 Market server，拥有独立的回放状态；
- paper 模式根据数据来源选择 live feed 或 instance-owned replay，不能因此改变 Account/Execution 的 instance 隔离要求。

当前已接入的 Binance Equity 行情属于 REST quote polling：Binance Stocks Trading
通过 `/sapi/v1/equity/market/quote` 获取最新 bid/ask，Market 将它包装为统一的
`MarketStreamConnection`，由 instance-local Market runtime 定时轮询。该连接需要
Binance API key；下单等签名操作仍额外需要 API secret。

Reference 是默认可启动的 Workspace 全局目录服务。Binance Spot、USDM Futures、
COIN-M Futures 和 Options 的 source 由 Reference 内置，不需要在 Workspace manifest
中声明。Massive 等需要凭证的 provider 在发现对应 Workspace credential 后自动加入；
只有需要显式启用/禁用或指定凭证时才写 Reference override：

```toml
[reference.providers.massive]
credential_id = "massive-readonly"

[reference.products.binance.equity]
enabled = true
credential_id = "binance-equity-readonly"
```

Reference 不读取 `market.connections`；Market 也不要求用户维护连接目录。
Market composition 内置 Binance、OKX 等公共行情能力，并在启动时自动发现
Workspace credential，按 provider/product 加入需要鉴权的行情能力。用户只需要配置
必要的 credential 元数据或通过环境变量提供敏感值：

Massive 的连接地址属于 Workspace 配置，不放在 `.env`：

```toml
[market.massive]
rest_base_url = "http://api.massiveprivateserver.site"
websocket_base_url = "http://socket.massiveprivateserver.site"
option_underlying = "AAPL"
```

两个地址都可省略，系统会使用内置默认值；命令行 `--endpoint` 仅作为一次性运行的覆盖。
`option_underlying` 用于避免启动时扫描整个期权市场；策略订阅其他标的时，将其改为对应的标的代码。

```toml
# .kairos/credentials/binance-equity-readonly.toml
[credential]
id = "binance-equity-readonly"
provider = "binance"
```

Market 进程从 Workspace 的 `credentials/<id>.toml` 读取非敏感元数据，再通过同名的
`KAIROS_CREDENTIAL_<ID>_API_KEY` / `..._API_SECRET` 环境变量或外部密钥存储取得敏感值。

具体订阅标的属于策略，而不是 `paper.market`。策略通过
`context.subscribe(...)` 声明 symbol、exchange、market type 和可选 identity；Market
进程只根据策略的订阅请求创建 descriptor 并启动对应 provider subscription。这样同一
个连接可以服务多个策略和多个标的。

Market process 启动时加载内置能力目录，策略首次订阅某个
`venue + market_type + asset_type` 时才懒加载对应连接；因此一个 Market process 可以同时承载
Binance、OKX 和 Massive 的多个产品连接。

Reference 是 Workspace 级全局进程，不属于任何 launch instance。使用
`kairos-reference-server --workspace <workspace>` 时，它使用 Reference 内置的
provider/product source registry，为每个启用的 provider/product 创建独立 source，再合并成一份
全局 catalog；多个 Market instance 共享这份 catalog。Reference 的全局路由维度同样包含
`venue + market_type + asset_type`，因此 OKX crypto spot 与 equity spot 不会合并。

本地运行 Reference/Market 前，Workspace 需要先启动共享的 Aeron Media Driver：

```bash
cargo run -p kairos-transport --bin kairos-aeron-driver
cargo run -p kairos-reference --bin kairos-reference-server -- --workspace <workspace>
cargo run -p kairos-market --bin kairos-market-server -- --workspace <workspace> --provider workspace
```

`kairos-aeron-driver` 使用与客户端相同的默认 Aeron directory；部署时也可以通过
`kairos-aeron-driver --aeron-dir <dir>` 和 Reference 的 `--aeron-dir <dir>` 指向同一目录。Aeron 是 Workspace/System
运行时基础设施，不属于 Reference 或 Market 的业务配置。

Market 的运行作用域由 launch 配置显式决定：

```toml
[live.market]
scope = "shared" # shared | instance，默认 shared
```

`backtest.market.scope` 默认是 `instance`；如果策略需要独立行情处理、独立
order book 或性能隔离，可以将 live/paper 的 scope 设为 `instance`。这样是
同一个物理 Workspace 下的不同 Runtime Context，而不是创建新的 Workspace 类型。

## 2. 作用域模型

```text
Workspace
├── Workspace-scoped resources
│   ├── kairos.toml
│   ├── accounts / credentials / profiles
│   ├── reference catalog
│   ├── historical data sources
│   └── shared live market connection capability
└── Launches
    └── Launch Instance
        ├── Strategy runtime
        ├── Account runtime
        ├── Execution runtime
        └── Market runtime when the mode requires local state
```

### Workspace

Workspace 是项目级资源容器，负责：

- manifest 和 workspace identity；
- 配置、credential、profile 的根路径；
- reference catalog 和可复用数据源；
- 进程、socket、health、日志等通用资源路径；
- system 使用的 launch registry 和 operation journal 路径。

Workspace 不负责账户、订单、风险或行情业务规则，也不创建业务状态的全局拥有者。

### Launch

Launch 是一次运行的逻辑定义，例如：

```text
config/launches/btc-sma.toml
```

它描述：

- mode：backtest、paper 或 live；
- strategy reference 和参数；
- account reference；
- execution 配置；
- 数据源或时间窗口。

Launch 配置本身不是运行进程，也不拥有运行时业务状态。

### Launch Instance

Launch Instance 是一次实际运行，例如：

```text
launches/backtest/btc-sma/instances/run-001/
launches/backtest/btc-sma/instances/run-002/
```

每个 instance 必须独立拥有：

- strategy 参数和运行状态；
- account balance、position、PnL 和 reconcile 状态；
- execution order namespace、fills、session 和 audit；
- backtest 的时间、cursor、checkpoint 和 replay 状态；
- instance 日志、恢复信息和生命周期状态。

## 3. Market 的两种运行拓扑

### 3.1 Live：Workspace 级共享 Market Service

可以由 Workspace Runtime Context 启动一个共享的：

```text
kairos-market-server --mode live
```

它可以负责：

- 维护交易所 WebSocket/REST 连接；
- 统一处理认证、重连、限流和 provider lifecycle；
- 接收实时行情；
- 维护共享的最新 observation 或 order book；
- 向多个 strategy/launch consumer 分发行情。

多个策略可以共享底层 provider connection，但不能因此共享属于某个策略的消费状态。

需要按 consumer 或 launch 隔离的状态包括：

- 订阅关系；
- 消费 cursor；
- freshness 视图；
- replay 或恢复位置；
- 策略所需的派生 projection。

因此 live 的共享对象应主要是连接和原始行情 feed；策略消费上下文仍然需要隔离。

### 3.2 Instance-local：Launch Instance 级 Market Service

回测以及显式选择 `scope = "instance"` 的运行，必须由每个 instance 独立运行：

```text
kairos-market-server \
  --mode backtest \
  --launch-id btc-sma \
  --instance-id run-001
```

它必须拥有自己的：

- market data source selection；
- start/end window；
- replay clock；
- event sequence；
- order book；
- cursor 和 checkpoint；
- completion/failure lifecycle。

历史数据文件本身可以放在 Workspace 级的 `data/` 下复用，但回测对数据的读取状态必须位于 instance 目录下。

```text
.kairos/
├── data/
│   └── binance-btcusdt/
└── launches/
    └── backtest/
        └── btc-sma/
            └── instances/
                └── run-001/
                    └── market/
                        ├── cursor.json
                        └── checkpoint.json
```

## 4. 业务状态所有权

Market 的部署方式不改变业务状态所有权：

| 状态 | 所有者 |
| --- | --- |
| provider connection lifecycle | Integration / composition |
| shared live raw feed | Market live service |
| live consumer subscription/cursor | 对应 consumer 或 launch runtime |
| backtest replay state | Launch Instance 的 Market runtime |
| account balance/position/equity | Account Instance |
| external order lifecycle/fills/audit | Execution Instance |
| launch lifecycle and process state | kairospy/system |
| reference catalog | Reference application / Workspace-scoped storage |

Account 和 Execution 不因共享 live Market 而变成 Workspace 级服务。它们仍然必须按 Launch Instance 隔离。

## 5. 目录建议

```text
.kairos/
├── kairos.toml
├── accounts/
├── credentials/
├── profiles/
├── config/
│   └── launches/
├── state/
│   ├── operations.jsonl
│   ├── launch-index.json
│   └── account-locks/
├── reference/
├── data/
├── market/
│   └── connections/
└── launches/
    └── <mode>/<launch_id>/
        ├── current.json
        └── instances/<instance_id>/
            ├── state.json
            ├── command.json
            ├── run.sqlite
            ├── launch.log
            ├── sockets/
            ├── health/
            └── market/
```

`market/connections/` 只保存 Workspace 级连接资源或连接元数据；实例级的 cursor、checkpoint、projection 和运行日志必须放到 instance 目录。

## 6. 实现约束

- 不创建 `LaunchWorkspace`、`MarketWorkspace` 等平行 Workspace 类型。
- `Workspace` 只提供通用资源和路径能力。
- `InstanceWorkspace` 只表示一个 Launch Instance 的资源上下文，不是新的业务状态层。
- Market 进程只消费被选定的 Workspace/Runtime Context，不在 Market application/domain 内判断 shared/instance。
- `kairos-market-server` 可以代表 Workspace 级 live service，也可以代表 instance 级 backtest runtime。
- live 连接可以共享，但 launch/consumer 的订阅和消费状态必须隔离。
- historical data 可以 Workspace 级复用，但 replay cursor、checkpoint 和时间状态必须 instance-owned。
- Account、Execution、Risk 的可变业务状态必须继续按 Launch Instance 隔离。
- System 负责决定启动拓扑，不把 live/backtest 选择下沉到 Workspace crate。

## 7. 推荐启动拓扑

```text
live:
Workspace
└── kairos-market-server (shared)
    ├── Launch Instance A
    │   ├── Account
    │   ├── Execution
    │   └── Strategy
    └── Launch Instance B
        ├── Account
        ├── Execution
        └── Strategy

live with isolated market:
Workspace
└── Launch Instance A
    ├── kairos-market-server (instance-local)
    ├── Account
    ├── Execution
    └── Strategy

backtest:
Workspace
└── Launch Instance A
    ├── kairos-market-server (local replay)
    ├── Account
    ├── Execution
    └── Strategy
```

最终判断标准不是“每个模式启动几个 Market 进程”，而是：

> 共享 provider connection 和只读数据源可以复用；任何影响一次运行结果的可变状态，都必须属于对应的 Launch Instance。

## 8. 后续实现顺序

1. 将本文决策补回主设计文档，明确 live shared service 与 backtest instance runtime 的区别。
2. 冻结 Rust/Python 共用的 Workspace 磁盘路径契约。
3. 让 `kairos-market-server` 统一接收 `--mode`、`--launch-id`、`--instance-id`。
4. 先实现 backtest instance 的独立 cursor/checkpoint 验收。
5. 再实现 live shared feed 的多 consumer 分发和隔离订阅。
6. 最后由 kairospy/system 选择和编排两种启动拓扑。
