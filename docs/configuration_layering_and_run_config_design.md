# Kairos 配置分层与 RunConfig 设计

状态：Implemented and under verification  
日期：2026-07-22  
适用对象：`kairos.toml`、环境变量、Data Product 配置、Workspace 投影、Run 启动配置和 runtime evidence

## 1. 背景

当前项目已经形成了比较清晰的产品边界：

```text
Data -> Workspace -> Code -> Run -> Replay -> Audit
```

配置分层正在向这个边界收敛：`kairos.toml` 承载 project、path、credential ref、provider service、account binding 和 Data Product 扩展；可复用启动意图进入 `configs/runs/*.toml`；CLI 不再把额外 Data Product / provider 扩展放在独立参数里传入。

这个文档要解决的问题是：

- Data 需要行情数据账号或 vendor 凭据，例如 Massive、OKX/OKEX、Binance public/private market data。
- 实盘系统需要交易账号，交易账号和行情账号不能混为一类。
- Workspace 是 Data 的子集合投影，只绑定 Dataset / Live View 的本地名字。
- Run 是抽象概念，但一个可复用的 run 配置会被反复启动；一次实际执行需要独立命名和独立审计。
- 项目初始化应该给用户清楚的配置文件结构，而不是把所有运行配置都堆进 `kairos.toml`。

## 2. 当前状态

当前权威入口：

| 文件 | 当前职责 | 问题 |
|---|---|---|
| `kairospy/infrastructure/configuration.py` | 发现并解析 `kairos.toml`，加载 `.env`，解析通用 `env:`，提供 `ProjectConfigLoader` / `ConfigValue` / `CredentialRef` | 不承载 provider/account credential 业务规则 |
| `kairospy/integrations/config.py` | 解析 `ProviderServiceConfig`、`AccountBinding` 和 provider/account credential refs | provider-specific credential schema 收口在 integrations 层 |
| `kairospy/surface/project.py` | `kairospy init` 生成 `kairos.toml`、`.env.example` 和 `configs/runs/*.example.toml` | 默认保持 live fail-closed |
| `kairospy/data/extensions/bootstrap.py` | 注册内置 Data Product 和项目配置中的 Data Product / provider extension | 额外 provider extension 从 `kairos.toml` 自动发现，不再通过独立 CLI 参数传入 |
| `kairospy/workspace/repository.py` | Workspace manifest、data aliases、WorkspaceData facade | 边界正确，只应继续保持 data projection 语义 |
| `kairospy/runtime/run_config.py` | `RunConfig`、`RunConfigResolver`、`RunConfigValidationReport` | 用户侧 reusable run intent 入口 |
| `kairospy/runtime/run_instance.py` | `RunInstance`、`RunManifestBuilder` | RunInstance manifest 构造和冻结事实结构 |
| `kairospy/runtime/live_config.py` | 从 RunConfig 生成 `LiveRuntimeBindingConfig` | 不再读取 project-level `[runtime.live]` |
| `kairospy/integrations/live_ports.py` | 将 RunConfig live binding 转为 execution/account/recovery port，将 Data Product Live View 转为 market EventSource | 通过 integrations credential resolver 获取交易 secret |
| `kairospy/surface/cli/main.py` | `run start --config`、`run config validate/explain`、`providers doctor`、`accounts doctor`、`workspace inspect` | CLI 只暴露新 RunConfig 启动路径 |

结论：现有代码的领域边界大体正确，但配置 schema 缺少统一分层。下一步应先固定新配置 contract，再按硬切换实施，避免长期维护双 schema。

## 3. 命名决策

### 3.1 推荐命名

| 概念 | 推荐名称 | 用户是否直接感知 | 含义 |
|---|---|---:|---|
| 项目配置 | `ProjectConfig` / `kairos.toml` | 是 | 项目身份、路径、provider/account 引用和默认策略 |
| 凭据引用 | `CredentialRef` | 间接 | 指向环境变量、Keychain、Vault 等 secret 来源，不保存 secret 值 |
| provider 服务配置 | `ProviderConfig` / `ProviderServiceConfig` | 是 | 外部 provider 的 service、resource、环境、endpoint、rate limit、credential ref |
| 数据产品配置 | `DataConfig` / `DataProductConfig` | 是 | Data Product、source selection、historical acquisition、live view |
| 账户绑定 | `AccountBinding` | 是 | 某个可交易账户、environment、credential、权限、capital scope |
| 工作区 | `Workspace` | 是 | Data 子集合投影，本地名字到 Dataset / Live View 的绑定 |
| 可复用运行配置 | `RunConfig` | 是 | 可反复启动的运行配置文件 |
| 启动请求 | `RunRequest` | 内部 | 启动时由 RunConfig、CLI overrides、ProjectConfig 解析得到 |
| 实际执行实例 | `RunInstance` | 是 | 一次真实执行，有唯一 `run_id` |
| 执行冻结事实 | `RunManifest` | 是 | 该 RunInstance 启动时冻结的事实记录 |
| 执行结果和审计 | `RunArtifact` | 是 | 结果、指标、上下文 hash、evidence refs |
| 模式适配器 | `RunProfile` | 内部 | backtest / simulation / live 的 runtime adapter |

### 3.2 为什么用 RunConfig

`RunDefinition` 不够像量化系统常用术语；`RunSpec` 更像 Kubernetes 或通用基础设施术语；`RunRecipe` 易懂但不够严肃；`RunProfile` 已经是本项目内部 runtime adapter，不能再给用户配置使用。

常见量化系统的命名倾向如下：

| 系统 | 用户侧配置或入口 | 一次执行 |
|---|---|---|
| QuantConnect LEAN | project / algorithm，CLI 使用 `lean backtest <projectName>` | backtest，live deployment |
| NautilusTrader | `BacktestRunConfig`、`TradingNode` config | backtest run / node execution |
| Freqtrade | configuration file / bot config | backtesting、dry-run、live |
| Backtrader | `Cerebro` engine 配置、strategy、data feeds | `cerebro.run()` |
| Zipline | algorithm + simulation params | `run_algorithm()` / backtest |
| Qlib | Experiment 管理实验，Recorder 记录单次 run | recorder instance |
| vn.py | 策略实例、策略参数、CTA 策略引擎 | 策略初始化、启动、停止 |

因此，Kairos 用户侧采用 `RunConfig` 最符合直觉，也最少解释成本。

最终心智：

```text
RunConfig + overrides
  -> RunRequest
  -> RunInstance
  -> RunManifest
  -> RunArtifact
```

## 4. 配置分层

### 4.1 分层总览

| 层 | 推荐物理位置 | 是否提交 Git | Owner | 内容 | 禁止内容 |
|---|---|---:|---|---|---|
| Project | `kairos.toml` | 用户项目默认是；Kairos 源码仓库本地文件否 | infrastructure / surface | 项目身份、路径、默认配置、provider/account 引用 | 具体 RunInstance 状态、secret 值、secret 值本身 |
| Secrets | `.env`、Keychain、Vault | 否 | local operator / secret store | API key、secret、passphrase、token | workspace、dataset、策略参数 |
| Provider services | `kairos.toml` 或 `configs/providers/*.toml` | 随所在文件策略 | integrations | provider type、service、endpoint、rate limit、credential ref | Data Product 语义、订单状态 |
| Data products | `kairos.toml` 或 `configs/data/*.toml` | 随所在文件策略 | data | Data Product、source selection、acquisition、live view | 交易账户、下单权限 |
| Accounts | `kairos.toml` 或 `configs/accounts/*.toml` | 默认否或按安全策略 | integrations / runtime | AccountRef、environment、credential ref、permission、capital scope | secret 值、portfolio mutable state |
| Workspace | `.kairos/workspace/<name>/workspace.json` | 通常否 | workspace | Dataset / Live View 的本地投影和 params | provider secret、trading account、runtime db |
| RunConfig | `configs/runs/*.toml` | 是 | surface / runtime | 可重复启动的运行配置 | 某次执行的事实状态、secret 值 |
| RunInstance | `.kairos/run/<run_id>/` | 通常否或作为 artifact | runtime | runtime db、state、logs、resolved config | 可复用默认配置 |
| Governance | `.kairos/governance/` | 视团队流程 | governance | readiness、promotion、audit evidence | provider DTO、策略可变状态 |

### 4.2 核心原则

- Data credential 和 trading credential 必须分开。Massive 这类行情 vendor 的 key 默认只用于 Data acquisition / Live View；OKX、Binance、IBKR 的交易 key 必须通过 AccountBinding 显式进入 Run。
- Workspace 只绑定 Data。它不下载数据，不保存数据副本，不保存 provider secret，不持有实盘账户锁，不持有 runtime database。
- RunConfig 是用户意图，不是执行事实。它可以反复运行；每次启动都会生成新的 RunInstance。
- RunManifest 是执行事实。它必须冻结解析后的 workspace snapshot、strategy hash、params、account binding hash、market binding hash、readiness/promotion refs 和 config hash。
- 环境变量只用于 secret 和机器本地 endpoint override。不要把 workspace、dataset、account、mode、strategy 参数藏进环境变量。
- Live 默认 fail closed。没有 readiness、promotion、account binding、recovery evidence 时，不能误进入真实下单路径。

## 5. `kairospy init` 推荐生成结构

`kairospy init` 应生成稳定的项目壳，同时新增可复用 RunConfig 示例。

推荐结构：

```text
kairos.toml
.env.example
configs/
  runs/
    backtest.example.toml
    paper.example.toml
    live.example.toml
.kairos/
  project.json
  data/
  workspace/
  run/
  governance/
```

默认生成的用户项目 `.gitignore` 不忽略 `kairos.toml`：它是可审阅、可版本化的项目级配置 contract。默认只忽略 `.kairos/` 下运行状态，并保留 `.kairos/project.json` 作为项目元数据。Kairos 源码仓库自身为了避免本地调试配置入库，会在仓库根 `.gitignore` 忽略 `/kairos.toml` 和 `/.kairos/`。默认不生成真实 live 配置，不写真实 account id，不写真实 credential。`live.example.toml` 只作为模板，并保持 fail-closed。

如果后续 provider/data/account 配置膨胀，可以再扩展为：

```text
configs/
  providers/
    massive.example.toml
    okx.example.toml
  data/
    massive-us-equity.example.toml
  accounts/
    okx-live.example.toml
  runs/
    backtest.example.toml
    paper.example.toml
    live.example.toml
```

但第一阶段不建议默认生成太多文件。默认只生成 `configs/runs/*.example.toml`，保持用户入口轻。

## 6. ProjectConfig schema

`kairos.toml` 作为项目级入口。它描述稳定项目环境，不描述某一次 run。

示例：

```toml
schema_version = 1

[project]
name = "alpha"
timezone = "UTC"

[paths]
lake_root = ".kairos/data"
workspace_root = ".kairos/workspace"
run_root = ".kairos/run"
governance_root = ".kairos/governance"
reference_catalog = ".kairos/data/reference/catalog.json"

[credentials.massive_marketdata_primary]
purpose = "market_data"
kind = "api_key"
api_key = "env:KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"

[credentials.okx_trading_live_main]
purpose = "trading"
kind = "api_key_secret_passphrase"
api_key = "env:KAIROS_OKX_TRADING_LIVE_MAIN_API_KEY"
api_secret = "env:KAIROS_OKX_TRADING_LIVE_MAIN_API_SECRET"
passphrase = "env:KAIROS_OKX_TRADING_LIVE_MAIN_PASSPHRASE"

[providers.massive]
type = "data_vendor"
enabled = true

[providers.massive.services.historical_market_data]
credential = "massive_marketdata_primary"
venues = ["us-securities", "opra"]
timeout_seconds = 30
max_retries = 4

[providers.okx]
type = "exchange"
enabled = true
aliases = ["okex"]

[providers.okx.services.market_data]
environment = "live"
credential = ""
public = true

[providers.okx.services.execution]
environment = "live"
credential = "okx_trading_live_main"

[accounts.okx_main]
account_ref = "okx:derivatives:main"
provider = "okx"
environment = "live"
credential = "okx_trading_live_main"
permissions = ["account:read", "order:write", "order:cancel"]
allowed_products = ["spot", "perpetual"]
capital_scope = "main-live"
```

规范化规则：

- 不把历史字段作为可选写法。新项目只生成新 schema，运行时 resolver 也只接受新 schema。
- 旧项目升级时通过一次性迁移命令或手工修改转换到新 schema，不在运行时维护双解析路径。
- `okex` 只作为输入 alias，写入配置和内部 provider id 一律使用 `okx`。

## 7. RunConfig schema

RunConfig 是可复用运行配置文件。推荐目录为：

```text
configs/runs/
```

推荐启动命令：

```bash
kairospy run start --config configs/runs/sma-backtest.toml
kairospy run start --config configs/runs/okx-live.toml --confirm-live
```

### 7.1 Backtest RunConfig

```toml
schema_version = 1

[run]
name = "sma-backtest"
mode = "backtest"
workspace = "alpha"
entrypoint = "strategies.sma_cross:build"

[params]
fast = 20
slow = 50

[backtest]
start = "2025-01-01T00:00:00+00:00"
end = "2026-01-01T00:00:00+00:00"
initial_cash = "1000000"
base_currency = "USD"
fill_model = "deterministic-bar-v1"
fee_model = "configured-or-venue-default"

[guards]
freeze_workspace = true
require_data_quality = "Q3"
fail_on_missing_data = true
```

### 7.2 Paper RunConfig

```toml
schema_version = 1

[run]
name = "btc-paper"
mode = "paper"
workspace = "alpha"
entrypoint = "strategies.btc_momentum:build"

[params]
lookback = 48
risk_budget = "0.02"

[bindings]
account = "binance_testnet_spot"
market = ["ticks"]

[paper]
environment = "testnet"
execution_driver = "binance-testnet"
recovery_policy = "recover-and-reconcile"

[guards]
require_live_view_freshness = true
require_account_query = true
require_reconciliation = true
```

### 7.3 Live RunConfig

```toml
schema_version = 1

[run]
name = "okx-live-limited"
mode = "live"
workspace = "alpha"
entrypoint = "strategies.spot_perp_carry:build"

[params]
max_gross_exposure = "50000"
max_order_notional = "1000"

[bindings]
account = "okx_main"
market = ["ticks", "funding"]
execution = "okx_main"

[live]
provider = "okx"
execution_driver = "okx-live"
recovery_policy = "recover-and-reconcile"
service_supervision = "explicit"

[guards]
confirm_live_required = true
require_readiness = true
require_promotion = true
require_account_lock = true
require_order_recovery = true
require_reconciliation = true
start_reduce_only = true

[evidence]
readiness = "governance:readiness/okx-live-limited.json"
promotion = "governance:promotion/okx-live-limited.json"
```

说明：

- `bindings.account` 指向 ProjectConfig 或 account config 中的 `AccountBinding`。
- `bindings.market` 指向 Workspace 里的 live data local names，或显式 live view binding。
- `live.provider` 和 `live.execution_driver` 表示本 run 期望接入的 provider/service，不保存 credential。
- `evidence.*` 引用治理产物，不直接把 readiness/promotion 大段内容塞进 RunConfig。

## 8. 解析与优先级

启动时解析顺序：

```text
ProjectConfig
  + optional provider/data/account config files
  + RunConfig
  + CLI overrides
  + environment secret resolution
  -> RunRequest
  -> RunInstance
```

推荐优先级：

| 优先级 | 来源 | 说明 |
|---:|---|---|
| 1 | CLI overrides | 仅允许有限字段，例如 `--run-id`、`--param key=value`、`--confirm-live` |
| 2 | RunConfig | mode、workspace、entrypoint、params、bindings、guards |
| 3 | ProjectConfig | paths、providers、accounts、defaults |
| 4 | built-in defaults | 保守默认值，live 必须 fail closed |
| secret only | `.env` / Keychain / Vault | 只解析 secret 值，不参与业务选择 |

禁止行为：

- 不允许环境变量覆盖 `mode`、`workspace`、`account`、`entrypoint`。
- 不允许 RunConfig 存放 API secret。
- 不允许 RunManifest 存放 API secret。
- 不允许 Workspace manifest 存放 account binding。

## 9. RunInstance 和 RunManifest

每次启动 RunConfig 都创建新的 RunInstance：

```text
.kairos/run/
  run_20260722_153012_abc123/
    run_manifest.json
    resolved_config.toml
    workspace_snapshot.json
    strategy_source.json
    runtime/
      runtime.sqlite3
    artifacts/
    logs/
```

`run_manifest.json` 应至少包含：

```json
{
  "schema_version": 1,
  "run_id": "run_20260722_153012_abc123",
  "run_config": {
    "path": "configs/runs/okx-live.toml",
    "hash": "..."
  },
  "project_config_hash": "...",
  "workspace": {
    "name": "alpha",
    "snapshot_hash": "..."
  },
  "strategy": {
    "entrypoint": "strategies.spot_perp_carry:build",
    "hash": "..."
  },
  "params_hash": "...",
  "bindings": {
    "account_binding": "okx_main",
    "account_binding_hash": "...",
    "market_binding_hash": "...",
    "execution_binding_hash": "..."
  },
  "guards": {
    "confirm_live": true,
    "readiness_ref": "governance:readiness/okx-live-limited.json",
    "promotion_ref": "governance:promotion/okx-live-limited.json"
  }
}
```

`resolved_config.toml` 可以保存解析后的非 secret 配置，方便审计和 replay。所有 credential 字段必须只记录 redacted source，例如 `env:KAIROS_OKX_TRADING_LIVE_MAIN_API_KEY`。

## 10. Doctor 与验证命令

推荐新增或收敛以下诊断入口：

```bash
kairospy config validate
kairospy providers doctor massive
kairospy accounts doctor okx_main
kairospy workspace inspect alpha
kairospy run config validate configs/runs/okx-live.toml
kairospy run config explain configs/runs/okx-live.toml
kairospy run start --config configs/runs/okx-live.toml --confirm-live
```

诊断职责：

| 命令 | 检查内容 |
|---|---|
| `config validate` | TOML schema、路径、credential ref 是否可解析 |
| `providers doctor` | provider service 是否配置、endpoint、entitlement、rate limit、health |
| `accounts doctor` | account ref、credential ref、permission、account query、environment 隔离 |
| `workspace inspect` | Dataset / Live View 投影、release hash、freshness |
| `run config validate` | RunConfig schema、workspace、entrypoint、binding 引用、guard 字段 |
| `run config explain` | 展示解析后的 RunRequest，不启动 runtime |
| `run start` | 创建 RunInstance，冻结 manifest，执行 runtime gate |

`doctor` 不应让用户因为没有 live trading credential 就看到无关 warning。Data 用户只检查 Data 所需 provider；live 用户才检查 AccountBinding、execution、recovery 和 promotion。

## 11. 环境变量命名

新命名建议：

```text
KAIROS_<PROVIDER>_<PURPOSE>_<ENVIRONMENT>_<ALIAS>_<FIELD>
```

示例：

```text
KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY
KAIROS_OKX_TRADING_LIVE_MAIN_API_KEY
KAIROS_OKX_TRADING_LIVE_MAIN_API_SECRET
KAIROS_OKX_TRADING_LIVE_MAIN_PASSPHRASE
KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY
KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET
```

旧命名处理：

```text
MASSIVE_API_KEY
BINANCE_TESTNET_API_KEY
BINANCE_TESTNET_API_SECRET
BINANCE_LIVE_API_KEY
BINANCE_LIVE_API_SECRET
IBKR_HOST
IBKR_PORT
IBKR_CLIENT_ID
IBKR_ACCOUNT
```

这些旧命名不进入新的 `kairospy init` 模板，也不作为新 resolver 的后备解析路径。旧项目需要通过一次性迁移把 `.env` 和 `kairos.toml` 改为 `KAIROS_*` 命名。

## 12. 硬切换实施计划

### 阶段 1：文档和模板

- 新增本设计文档。
- `kairospy init` 生成 `configs/runs/backtest.example.toml`、`paper.example.toml`、`live.example.toml`。
- README 主路径改为 `run start --config ...`，不再把 `--workspace ... --entrypoint ...` 作为推荐用户入口。

### 阶段 2：typed config model

新增内部模型：

```text
infrastructure/configuration.py
  ProjectConfigLoader
  ConfigValue
  CredentialRef

integrations/config.py
  ProviderServiceConfig
  CredentialResolver

runtime/run_config.py
  RunConfig
  RunConfigResolver
  RunConfigValidationReport

runtime/run_instance.py
  RunInstance
  RunManifestBuilder
```

要求：

- `KairosProjectConfig` 只负责 TOML 读取、路径和通用 `env:` 解析。
- Massive/Binance/OKX/IBKR credential schema 迁到 integrations resolver。
- RunConfig resolver 不直接构造 provider SDK，只生成 `RunRequest` 和 binding evidence input。

### 阶段 3：CLI 支持

- `kairospy run start --config <path>`。
- `kairospy run config validate <path>`。
- `kairospy run config explain <path>`。
- CLI overrides 只允许小范围覆盖，不允许绕过 live guards。

### 阶段 4：替换旧 live 配置路径

`[runtime.live]`、`[runtime.live.provider_binding]`、`[runtime.live.market_binding]` 不再作为有效的新配置路径。新的 live binding 只由 RunConfig 解析产生：

```text
RunConfig [live] + [bindings]
  -> RunRequest
  -> LiveRuntimeBindingConfig
  -> LiveRuntimeComponents
```

`runtime.live` 不再作为 project-level 常驻配置，而是每次 RunInstance 的 resolved runtime binding evidence。

可以提供一次性 `kairospy config migrate` 辅助旧项目转换配置，但启动路径不支持旧 schema。

### 阶段 5：doctor 收敛

- `doctor` 默认只检查 project 和 data minimal readiness。
- `providers doctor` 检查 provider service。
- `accounts doctor` 检查交易账户。
- `run config validate/explain` 检查某个 RunConfig 是否能启动。
- Live 启动前必须先形成 readiness/promotion evidence refs。

### 阶段 6：删除旧入口文档和散落读取

- 文档、示例、`.env.example` 只展示新 schema 和 `KAIROS_*` env。
- public config path 不再暴露 provider-specific helper，例如 `massive_config()`、`binance_credentials()`。
- CLI 和 integration 入口统一通过 `CredentialRef` / `AccountBinding` resolver 获取 secret，不直接读取旧 env 名。

## 13. 验收标准

配置分层完成后，应满足：

- `kairospy init` 生成项目级 `kairos.toml` 和 `configs/runs/*.example.toml`。
- `Workspace` manifest 只包含 data binding，不包含 account、credential、runtime db。
- Data acquisition 可以使用 market data credential，不要求 trading account。
- Paper/live execution 必须通过 AccountBinding 解析交易账号，不直接散落读取 `BINANCE_*`。
- `RunConfig` 可以重复启动，每次生成唯一 RunInstance。
- `RunManifest` 能重建本次执行的非 secret 输入事实。
- `RunProfile` 仍只作为内部 mode adapter，不暴露为用户配置名称。
- `doctor` 输出按用户目标分层，不因缺少无关 live credential 干扰 data/backtest 用户。
- 旧 `providers.*` credential 字段和 `[runtime.live]` 不再是有效新配置；旧项目必须一次性迁移到新 schema。
