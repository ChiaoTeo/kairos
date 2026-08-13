# Strategy Application 构建设计

## 1. 文档目的

本文定义 Python Strategy runtime 如何获得 Reference、Market、Account、Risk 和 Execution
Application。

当前 Strategy composition 直接构造其他业务模块的 command client、mmap reader 和 projection，
导致 Strategy 必须了解每个模块的基础设施、部署和禁用细节。本文的调整原则很简单：

> 每个业务模块负责提供自己的 Application 构建方式；Strategy 只选择需要的构建方式并使用构建结果。

本文只处理当前 Strategy instance 的 Application 构建问题。Research 的数据研究、多策略运行和
批量回测接口尚未定型，不在本次设计范围内。本次实现不得因此提前增加
`ResearchApplications`、通用 Application registry 或跨场景 factory framework。

## 2. 当前问题

当前 `compose_strategy_applications` 接收：

```python
market_commands: MarketCommandClient
execution_commands: ExecutionCommandClient | None
market_snapshots: MmapMarketSnapshotReader
reference_client: ReferenceClient | None
account_projections: Mapping[AccountId, AccountMmapProjection]
execution_projection: ExecutionMmapProjection | None
risk_projection: RiskMmapProjection | None
```

然后由 Strategy 构造五个业务 Application：

```python
StrategyApplications(
    reference=ReferenceApplication(reference_client),
    market=MarketApplication(market_commands, market_snapshots, ...),
    account=AccountApplication(account_projections),
    risk=RiskApplication(risk_projection),
    execution=ExecutionApplication(execution_commands, execution_projection, ...),
)
```

这让 Strategy 知道：

- Market 由 command client 和 mmap reader 构成；
- Execution 的 command 和 projection 是两份依赖；
- Account 按 `AccountId` 保存 mmap projection；
- Risk 使用单个可选 projection；
- Reference 当前读取 SQLite client；
- 每个模块缺失时分别传 `None`、空 mapping 或其他默认值；
- socket、snapshot、live safety 和 backtest callback 如何装配。

这些都不是 Strategy 的业务知识。它们发生变化时，只应修改对应业务模块的构建实现。

`StrategyClientBundle` 把上述对象放进一个 dataclass，只减少了参数数量，没有减少耦合。随着模块
能力增加，它会继续膨胀。

## 3. 所有权结论

### 3.1 各业务模块拥有自己的 Application

Reference、Market、Account、Risk 和 Execution 各自负责：

- Application 的公开业务 API；
- Application 的业务 request/result/error；
- 创建 Application 所需的模块配置；
- 具体 client、reader、projection 和 adapter 的构建；
- 正常、只读、禁用等真实构建方式；
- 基础设施结果到模块业务结果的映射。

Strategy 不复制这些构造规则，也不为其他模块定义 mirror config。

### 3.2 Strategy 只拥有 Strategy runtime

Strategy 负责：

- 加载用户 Strategy；
- Strategy identity 和 params；
- callback lifecycle；
- Strategy state、clock、journal 和 control server；
- 将已经构造完成的业务 Application 暴露到 `StrategyContext`；
- 驱动 Strategy event loop。

Strategy 不负责：

- 创建其他业务模块的 command client；
- 创建或解释其他模块的 mmap projection；
- 定义其他模块的 disabled behavior；
- 解释其他模块的 snapshot 文件布局；
- 读取其他模块的 live safety 环境变量；
- 调用其他模块的私有方法。

### 3.3 Launch/System 负责实例级事实

Launch/System 继续拥有：

- canonical launch config；
- Workspace 和 InstanceWorkspace；
- 进程启动、停止、回滚和 readiness；
- component manifest；
- shared/instance topology；
- 实际 endpoint；
-跨业务 backtest 时间推进；
-进程死亡后的 orphan cleanup。

Launch/System 向各模块构建入口提供已经规范化的配置和实际运行资源，但不替模块创建内部
Application dependency。

## 4. 构建方式而不是统一构建框架

每个业务模块只为当前存在的调用场景提供少量、明确、类型化的构建方式。例如：

```python
market_composition.build_strategy_access(...)
account_composition.build_strategy_access(...)
risk_composition.build_strategy_access(...)
execution_composition.build_strategy_access(...)
reference_composition.build_strategy_access(...)
```

如果某模块确实存在其他调用场景，可以增加另一个具体入口：

```python
reference_composition.build_workspace_reader(...)
market_composition.build_cli_access(...)
execution_composition.build_disabled(...)
```

只有出现真实调用者时才增加构建方式。不得预先设计：

- `ApplicationFactory[T]`；
- `ModuleBuilder` 基类；
- 动态 service registry；
- 按字符串查找 Application 的容器；
- 包含所有模块配置的通用 `CompositionContext`；
- 所有字段均为 optional 的通用 `Applications`。

各模块的构建入口可以有不同签名，因为它们解决的问题本来不同。不为形式统一而增加无意义参数。

## 5. Application 与 composition 的边界

“业务 App 提供构建方式”不表示 Application 类自己导入 infrastructure。

以下方式不允许：

```python
class MarketApplication:
    @classmethod
    def from_workspace(cls, workspace, config):
        commands = MarketCommandClient(UnixJsonCommandClient(...))
        snapshots = MmapMarketSnapshotReader(...)
        return cls(commands, snapshots)
```

这会造成：

```text
application -> infrastructure
application -> composition
```

正确方向仍然是：

```text
module composition -> module application -> module services/domain
```

因此“模块提供构建方式”的具体含义是：

- Application 类定义业务能力；
- 模块自己的 composition 定义 concrete construction；
- 模块 config/value type 根据语义归 Application、Domain 或 composition；
- Strategy composition 调用模块 composition，得到 Application；
- Application 不导入 composition。

## 6. 代码归属

### 6.1 长期结构

长期每个业务模块应符合仓库标准结构：

```text
kairospy/<module>/
  application/
  composition/
  services/
  domain/
```

例如：

```text
kairospy/market/
  application/
    application.py
    models.py
    config.py
  composition/
    strategy_access.py
```

### 6.2 当前迁移结构

当前 Python 包已经使用：

```text
kairospy/application/market/
kairospy/application/account/
kairospy/application/risk/
kairospy/application/execution/
kairospy/application/reference/
```

本次任务不应顺便重排整个 Python package。可以先在相应业务目录增加私有构建文件：

```text
kairospy/application/market/composition.py
kairospy/application/account/composition.py
kairospy/application/risk/composition.py
kairospy/application/execution/composition.py
kairospy/application/reference/composition.py
```

这些文件在语义上属于对应业务模块的 composition，不是 Application API。它们不得从该业务
模块面向策略作者的公开 `__init__.py` 导出。

后续整体整理 Python 模块目录时，再机械迁移到标准的 `<module>/composition/`。本次不建立中央
`kairospy/composition` 目录，也不建立中央 module registry。

## 7. Config 归属

Config 不能因为参与 Application 构建就全部放进 Strategy，也不能全部放进 Application。
归属取决于配置表达的语义。

### 7.1 Canonical Launch config

Launch application 拥有跨模块期望状态，例如：

```python
@dataclass(frozen=True, slots=True)
class NormalizedLaunchSpec:
    mode: LaunchMode
    strategy: LaunchStrategySpec
    market: LaunchMarketSpec
    accounts: tuple[LaunchAccountSpec, ...]
    risk: LaunchRiskSpec
    execution: LaunchExecutionSpec
    backtest: LaunchBacktestSpec | None
```

它描述：

- 哪些组件启用；
- Market 使用 shared 还是 instance scope；
- 启用哪些账户；
- 使用什么 Risk profile；
- Execution 是否允许交易；
- backtest 数据窗口和 seed。

它是唯一持久化配置来源。不得为 Strategy 再生成一份重复的五模块 config 文件。

### 7.2 模块业务配置

会改变模块业务行为、可以独立校验的配置由对应 Application/Domain 定义。

例如 Execution：

```python
@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    allow_trading: bool
    max_order_notional: Decimal | None
    require_limit_orders: bool
```

例如 Risk：

```python
@dataclass(frozen=True, slots=True)
class RiskProfileRef:
    profile_id: str
```

Launch config 可以保存对应值，但应通过显式转换得到模块拥有的业务类型：

```python
execution_policy = launch.execution.to_execution_policy()
```

不得在 Strategy 中重新定义 `StrategyExecutionConfig` 或 `StrategyRiskConfig`。

### 7.3 模块构建配置

socket、snapshot path、transport 和 projection schema 是具体构建细节，归模块 composition。

例如：

```python
@dataclass(frozen=True, slots=True)
class ExecutionRuntimeResources:
    command_endpoint: Path
    execution_snapshot: Path
    intent_snapshot: Path
```

该类型如果需要存在，也只能在 Execution composition 内部使用，不能进入
`ExecutionApplication` 公共 API 或 `StrategyContext`。

多数资源不需要单独定义 Config，可以直接由 `InstanceWorkspace` 和类型化 endpoint 推导。

### 7.4 Strategy config

Strategy 只拥有自己的配置：

```python
@dataclass(frozen=True, slots=True)
class StrategyConfig:
    strategy_ref: str
    params: Mapping[str, object]
```

Market、Account、Risk 和 Execution 的配置不能为了方便统一放入 `StrategyConfig`。

`replay_end` 属于 Backtest/Launch，live safety 属于 Execution，Market scope 属于 Launch/Market
composition；它们都不是 Strategy 配置。

### 7.5 Config 禁止包含的对象

任何 Config 都不得包含：

- command client 或 SDK client；
- mmap reader 或 projection；
- event stream；
- callback 或 closure；
- Application、Actor 或 service instance；
- 已打开文件、socket 或数据库连接；
- credential value 或 vendor payload。

否则 Config 只是新的 dependency bundle。

## 8. Workspace、Config 与 Manifest

三者必须继续区分。

### 8.1 Workspace：资源命名规则

`Workspace` 和 `InstanceWorkspace` 提供确定性资源路径，例如：

```python
workspace.paths.reference_database()
instance.socket("execution")
instance.snapshot("execution", "execution.snapshot")
instance.state("strategy", "state.json")
```

Workspace 不表示进程已经启动，也不表示资源已经 ready。

### 8.2 Config：期望状态

Config 表示希望启动什么、选择什么 policy。它不能证明某个 endpoint 实际存在。

### 8.3 Manifest：实际运行事实

component manifest 表示当前 instance 实际发布了哪些 endpoint。

Launch/System 应只解析一次 manifest，并提供类型化结果：

```python
@dataclass(frozen=True, slots=True)
class InstanceEndpoints:
    market: MarketEndpoints
    accounts: Mapping[AccountId, AccountEndpoint]
    risk: RiskEndpoint | None
    execution: ExecutionEndpoint | None
```

各模块构建入口只获得自己的 endpoint 切片，不自行重复解析原始 manifest，也不接收完整
`InstanceEndpoints`。

例如：

```python
execution_composition.build_strategy_access(
    instance=instance,
    endpoint=endpoints.execution,
    policy=execution_policy,
    requester=identity,
)
```

如果 config 表示 Execution enabled，但 endpoint 缺失，构建失败。如果 config 表示 disabled，
则构建明确的 unavailable Application，不因为残留 endpoint 自动启用。

## 9. 各业务模块的目标构建入口

以下签名表达责任边界，不要求为了统一而完全照搬命名或字段。

### 9.1 Reference

Reference 当前只需要 Workspace 即可定位 canonical read model：

```python
def build_strategy_access(
    workspace: Workspace,
) -> ReferenceApplication:
    client = ReferenceClient(
        database_path=workspace.paths.reference_database(),
    )
    return ReferenceApplication(client)
```

该实现位于 Reference composition。Strategy 不知道 SQLite client 或数据库文件名。

如果未来出现真实的历史 point-in-time caller，可以由 Reference 增加独立构建方式；本次不预先
设计。

### 9.2 Market

Market 需要同时返回 Strategy 使用的 Application 和 Strategy event loop 使用的 event stream：

```python
@dataclass(frozen=True, slots=True)
class StrategyMarketAccess:
    application: MarketApplication
    events: EventStream


def build_strategy_access(
    *,
    workspace: Workspace,
    instance: InstanceWorkspace,
    endpoint: MarketEndpoints,
    config: MarketAccessConfig,
    requester: StrategyIdentity,
) -> StrategyMarketAccess:
    ...
```

Market 构建实现负责：

- shared/instance endpoint；
- command transport；
- snapshot reader；
- event stream；
- subscription owner identity；
- snapshot/event join point；
-底层 command result 到 Market-owned result 的映射。

Strategy 不接收 `MarketCommandClient` 或 `MmapMarketSnapshotReader`。

### 9.3 Account

```python
def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    endpoints: Mapping[AccountId, AccountEndpoint],
    enabled_accounts: tuple[AccountId, ...],
) -> AccountApplication:
    ...
```

Account 构建实现负责：

- 校验 enabled account 与 actual endpoint；
- 为每个账户定位正确 projection；
- 隐藏 snapshot 文件名和 schema；
- 定义未启用账户的业务错误。

不得由 Strategy 构造 `AccountId -> AccountMmapProjection`。

### 9.4 Risk

```python
def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    endpoint: RiskEndpoint | None,
    enabled: bool,
) -> RiskApplication:
    ...
```

Risk 构建实现负责正常 projection 和 unavailable Application。Strategy 不传
`RiskMmapProjection | None`。

### 9.5 Execution

```python
def build_strategy_access(
    *,
    instance: InstanceWorkspace,
    endpoint: ExecutionEndpoint | None,
    policy: ExecutionPolicy,
    enabled: bool,
    requester: StrategyIdentity,
) -> ExecutionApplication:
    ...
```

Execution 构建实现负责：

- command client；
- execution/intent projection；
- live safety policy；
- request identity；
- unavailable Application；
-底层 result 到 Execution receipt 的映射。

Strategy 不知道 Execution 的 command 和 query 是否由一个还是多个对象实现。

## 10. Strategy 顶层组合

Strategy process composition 仍然存在，但只做实例级汇总，不再实现其他模块的构造规则。

目标代码接近：

```python
def compose_strategy_process(
    workspace: Workspace,
    *,
    launch_id: str,
    instance_id: str,
    mode: str,
) -> StrategyProcessComposition:
    instance = workspace.instance(mode, launch_id, instance_id)
    launch = load_normalized_launch_spec(instance)
    endpoints = resolve_instance_endpoints(instance)

    entrypoint = load_strategy(
        launch.strategy.ref,
        root=workspace.paths.project_root,
        params=launch.strategy.params,
    )
    identity = StrategyIdentity(
        entrypoint.strategy.strategy_id,
        launch_id,
        instance_id,
    )

    market = market_composition.build_strategy_access(
        workspace=workspace,
        instance=instance,
        endpoint=endpoints.market,
        config=launch.market.to_access_config(),
        requester=identity,
    )

    applications = StrategyApplications(
        reference=reference_composition.build_strategy_access(workspace),
        market=market.application,
        account=account_composition.build_strategy_access(
            instance=instance,
            endpoints=endpoints.accounts,
            enabled_accounts=launch.accounts.enabled_ids,
        ),
        risk=risk_composition.build_strategy_access(
            instance=instance,
            endpoint=endpoints.risk,
            enabled=launch.risk.enabled,
        ),
        execution=execution_composition.build_strategy_access(
            instance=instance,
            endpoint=endpoints.execution,
            policy=launch.execution.to_policy(),
            enabled=launch.execution.enabled,
            requester=identity,
        ),
    )

    host = StrategyHost(
        entrypoint.strategy,
        launch_id=launch_id,
        instance_id=instance_id,
        applications=applications,
        runtime=StrategyRuntimeDependencies(
            market_events=market.events,
            state_path=instance.state("strategy", "state.json"),
        ),
        journal=build_strategy_journal(instance),
        params=launch.strategy.params,
    )
    return build_strategy_process_result(instance, entrypoint, host)
```

顶层代码知道本次 Strategy 需要 Reference、Market、Account、Risk 和 Execution，这是合理的跨业务
实例组合。它不知道这些 Application 内部如何构造。

`StrategyApplications` 只是 StrategyContext 固定能力的类型化集合，不是通用 service locator，
也不作为 Research 的预设接口。

## 11. StrategyHost 调整

### 11.1 接收完成的 Application

```python
class StrategyHost:
    def __init__(
        self,
        strategy: Strategy,
        *,
        applications: StrategyApplications,
        runtime: StrategyRuntimeDependencies,
        ...,
    ) -> None:
        ...
```

删除 Host 内的 `compose_strategy_applications()` 调用。

### 11.2 缩小 runtime dependency

```python
@dataclass(frozen=True, slots=True)
class StrategyRuntimeDependencies:
    market_events: EventStream
    application_events: tuple[EventStream, ...] = ()
    state_path: Path | None = None
    history_root: Path | None = None
    backtest: BacktestRuntime | None = None
```

其中不能出现其他业务模块的 client 或 projection。

### 11.3 删除 Market 私有接口调用

Market Application 提供公开业务方法：

```python
class MarketApplication:
    def subscription_status(self, request_id: str) -> SubscriptionStatus: ...
    def release_strategy_subscriptions(self) -> SubscriptionReleaseResult: ...
    def join_point(self, view: str = "market.current") -> MarketJoinPoint: ...
```

删除 Strategy Host 对以下方法和对象的依赖：

```python
market._command_status(...)
market._release_owner()
MmapMarketSnapshotReader
```

返回值必须是 Market-owned result，不暴露底层 transport `CommandResult`。

## 12. 可用性语义

StrategyContext 保持稳定字段：

```python
context.reference
context.market
context.account
context.risk
context.execution
```

不改成大量 `Application | None`。对应业务模块提供明确的 unavailable 构建方式或在
`build_strategy_access()` 中根据 `enabled` 构建 unavailable Application。

例如：

```python
ExecutionApplication.unavailable(
    "execution is disabled for this launch"
)
```

规则：

- config disabled：合法 unavailable capability；
- config enabled 但 endpoint 缺失：composition failure；
- unavailable query：模块拥有的稳定 capability error；
- unavailable command：模块拥有的 rejected result，delivery certainty 为 `NOT_SENT`；
- Strategy 不读取 manifest 或 socket 判断能力。

具体 unavailable Application 的创建仍由模块 composition 负责。Application 可以定义业务错误和
结果，但不自行解析 endpoint。

## 13. Backtest 和停止流程

### 13.1 Backtest

当前以下 callback 不应继续散落在 `StrategyClientBundle`：

```python
backtest_market
backtest_account_mark
backtest_time_advance
```

它们属于跨业务 Backtest/Launch 编排。后续收口为当前调用方需要的具体 `BacktestRuntime`，由
Launch composition 构建。StrategyHost 只驱动时间线，不知道 Account、Risk、Execution 的 socket
和 control client。

本次不借此设计通用 coordinator、hook registry 或第二套 backtest runner。

### 13.2 Orphan cleanup

正常 Strategy shutdown 可以通过已经注入的 MarketApplication 释放自身 subscription owner。

Strategy 进程已死亡时，Launch/System 负责：

```text
stop Strategy
  -> invoke Market-owned owner cleanup
  -> stop Execution/Risk/Account
  -> release account leases
```

`StrategyProcessApplication` 不再手工创建 `MarketCommandClient`。Market 模块应提供当前 cleanup
调用方所需的明确构建/调用入口；不为 cleanup 建立通用管理器。

## 14. Research 边界

Research 当前尚未完善，本次不设计其接入方式。

只保留以下架构约束：

- 模块 Config 和构建实现归对应业务模块，不能放在 Strategy 私有目录；
- `StrategyApplications` 是 instance-scoped Strategy context，不提升为全局业务容器；
- 不因为猜测 Research 需求而增加历史、多实例或批量 factory；
- Research 后续若只需要数据，应通过 Data/Reference 的正式公开边界；
- Research 后续若需要运行回测，应通过 Launch/Backtest 边界；
- 只有出现真实 Research caller 后，相关业务模块才增加对应的具体构建方式。

这样本次实现不会阻塞未来 Research，也不会提前设计尚未确认的接口。

## 15. 迁移计划

### Phase 1：固定 Config 和 endpoint 边界

- 类型化加载 normalized launch config；
- 类型化解析 component manifest；
- 明确 Workspace、Config 和 Manifest 的区别；
- 将 live safety 环境变量解析移出 Strategy；
- 增加 config enabled/endpoint missing 测试。

退出标准：Strategy 不直接解释原始 config/manifest mapping。

### Phase 2：StrategyHost 接收完成品

- 修改 Host 接收 `StrategyApplications`；
- 建立最小 `StrategyRuntimeDependencies`；
- 暂时在 Strategy process composition 调用现有 constructor；
- 删除 `compose_strategy_applications()`；
- 从 `StrategyClientBundle` 移除业务 client/projection。

退出标准：Host 可以完全使用 fake Applications 测试。

### Phase 3：Market 收回构建

- 增加 Market `build_strategy_access()`；
- 构建 MarketApplication 和 EventStream；
- 增加公开 subscription status/release/join point；
- 删除 Host 对 mmap reader 和 Market 私有方法的依赖；
- 删除 Strategy 中的 Market infrastructure import。

退出标准：Market 构建规则只存在于 Market composition。

### Phase 4：迁移其他模块

依次迁移：

1. Reference；
2. Risk；
3. Account；
4. Execution。

每个模块：

- 增加自己的 `build_strategy_access()`；
- 定义最小 config/policy；
- 隐藏 concrete client/projection；
- 定义 unavailable behavior；
- 删除 Strategy 中对应的旧字段、import 和构造代码。

退出标准：Strategy process composition 只调用各模块构建入口。

### Phase 5：Backtest 与 cleanup

- 将 backtest callback 收口为 Launch-owned runtime；
- 将 orphan cleanup 移到 Launch/System；
- 删除 Strategy 对其他模块 control transport 的构造；
-覆盖正常停止、异常退出和 partial-start rollback。

### Phase 6：清理

删除：

- `StrategyClientBundle`，或将其替换为纯 Strategy runtime dependency；
- `compose_strategy_applications()`；
- Strategy 中的 mmap projection、command client 和 Unix transport import；
-重复 config/manifest 解析；
- Market 私有方法的跨模块调用；
-旧 backtest callback 字段。

不保留无调用者的兼容 facade。

## 16. 验证方案

### 16.1 Focused tests

- paper、live、backtest 的 Strategy 启动；
- shared/instance Market；
- 零账户和多账户；
- Risk/Execution disabled；
- config enabled 但 endpoint 缺失；
- AccountId 使用正确 projection；
- Market snapshot/event join；
- subscription pending、ready 和 owner release；
- live Execution safety；
- Strategy 正常停止、异常退出和 orphan cleanup；
- Host 使用 fake Application，不需要 infrastructure fixture。

### 16.2 静态边界

迁移完成后：

```bash
rg -n "kairospy\.infrastructure" kairospy/application/strategy
rg -n "Mmap|UnixJsonCommandClient|Projection" kairospy/application/strategy
rg -n "\._release_owner|\._command_status" kairospy/application/strategy
rg -n "StrategyClientBundle|compose_strategy_applications" kairospy tests
```

除明确记录的过渡 allowlist 外应无结果。

还应检查每个业务模块的 Application 文件不反向导入 composition：

```bash
rg -n "from .*composition|import .*composition" \
  kairospy/application/{reference,market,account,risk,execution}/application.py
```

### 16.3 Repository checks

```bash
uv run pytest -q tests/test_strategy_host.py tests/test_strategy_process.py
uv run pytest -q
cargo test --workspace
cargo fmt --all -- --check
git diff --check
```

如果有无关既有失败，报告准确失败并保留 focused verification。

## 17. 完成定义

本任务完成需要满足：

1. 每个业务模块拥有自己的 Strategy Application 构建入口；
2. 构建入口只为真实场景存在，没有统一 factory framework；
3. Application 不导入 composition 或 infrastructure；
4. StrategyHost 接收已经完成的业务 Application；
5. Strategy 不构造其他业务模块的 client、reader 或 projection；
6. Strategy 不定义其他业务模块的 mirror config；
7. Launch config 是唯一持久化配置来源；
8. 每个业务模块拥有自己的 policy/config 类型；
9. Workspace、Config 和 Manifest 的语义分离；
10. 各模块只接收自己的 config 和 endpoint 切片；
11. disabled behavior 由对应业务模块定义；
12. Market 私有方法和 mmap join 不再泄露到 Strategy；
13. Backtest callback 和 orphan cleanup 从 Strategy infrastructure bundle 移除；
14. 旧 bundle、旧构建函数和重复解析路径已经删除；
15. focused、全仓和静态架构检查通过。

最终结构可以概括为：

```text
Launch/System
  -> 提供 normalized config、Workspace 和实际 endpoint
  -> 调用 Reference/Market/Account/Risk/Execution 各自的构建入口
  -> 把完成的 Application 交给 StrategyHost

StrategyHost
  -> 运行 Strategy
  -> 不知道其他业务 Application 如何构建
```
