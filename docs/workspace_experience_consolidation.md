# KairoSpy 工作区体验收敛方案

状态：Draft，user-experience first  
日期：2026-07-21  
适用对象：KairoSpy Project、Data Workspace、Run Workspace 的产品体验和 CLI 收敛

本文从真实用户路径出发，重新定义 KairoSpy 的工作区边界。核心结论是：不要把 Study 和 Strategy 做成工作区。普通用户只需要一个绑定数据的 Workspace；真正需要新工作区语义的是 Run。

## 1. 核心判断

KairoSpy 应该保留两个工作区概念：

```text
Project
  -> Workspace: 数据绑定和用户代码上下文
  -> Run Workspace: 某一次执行的隔离运行上下文
```

Study 不需要作为显式产品概念。它只是用户在 Workspace 里写的一些研究、因子、实验代码，以及这些代码产生的 artifact。

Strategy 也不需要作为显式工作区概念。它只是用户在 Workspace 里写的一段可运行策略代码、entrypoint、参数和可选治理产物。

Run 才是新的工作区。因为 Run 需要隔离 execution mode、account、runtime database、live feed、execution gateway、supervisor、日志和运行产物。

换句话说：

```text
Workspace 只和数据绑定。
Study/Strategy 是代码。
Run 是执行工作区。
```

## 2. 目标用户心智

用户要理解的概念应该尽量少：

| 概念 | 用户理解 |
|---|---|
| `Project` | 一个本地项目，包含配置、数据湖、代码和产物 |
| `Provider` | 外部能力来源，例如 Massive、Binance、IBKR |
| `Dataset` | 已准备好的历史数据资产 |
| `Live View` | 已配置的实时数据视图 |
| `Workspace` | 当前项目里的数据绑定上下文 |
| `Run` | 一次 backtest、simulation、shadow、paper 或 live 执行 |

不应该让普通用户默认理解：

```text
Study Workspace
Strategy Workspace
Study Lock
Strategy Lock
Study Candidate
Dataset Release
```

旧项目里可能已经有这些产物，但新设计只提供一次性迁移，不把它们保留为运行时或用户侧概念。

## 3. 默认用户路径

### 3.1 初始化项目

`kairospy init` 只创建项目壳：

```bash
kairospy init my-alpha
cd my-alpha
```

推荐生成：

```text
kairos.toml
.env.example
README.md
.kairos/
  project.json
  data/
  workspace/
  run/
```

不应该默认生成：

```text
studies/starter.py
strategies/starter_sma.py
```

原因是这些目录会提前灌输 Study/Strategy 工作区心智。用户代码应该由用户自己组织，可以放在 `src/`、`research/`、`notebooks/`、`strategies/` 或任意团队路径。

### 3.2 准备数据

用户先配置 provider，再创建 Dataset：

```bash
kairospy configure massive

kairospy data use massive.equity.ohlcv.1d \
  --as bars.us.equity.1d \
  --start 2024-01-01T00:00:00+00:00 \
  --end 2025-01-01T00:00:00+00:00
```

Data 只存一次。后续研究、策略、回测和实盘都复用这个 Dataset。

### 3.3 创建 Workspace 并绑定数据

Workspace 是数据别名上下文。它不复制全局数据，但它有自己的数据视图：

```bash
kairospy workspace create alpha
kairospy workspace bind-data alpha --name bars --dataset bars.us.equity.1d
```

代码里打开 Workspace：

```python
from kairospy import Workspace

ws = Workspace.open_or_create("alpha")
bars = ws.data.get("bars")
```

这里 `bars` 是 workspace-local name，指向全局 Dataset `bars.us.equity.1d`。

用户不应该每次都写：

```python
from kairospy import Data

data = Data(".kairos/data")
bars = data.get("bars.us.equity.1d")
```

更好的体验是：

```python
from kairospy import Workspace

workspace = Workspace.open_or_create("alpha")
bars = workspace.data.get("bars")
```

`workspace.data` 是带 Workspace 上下文的 Data facade。它默认使用项目的全局 `.kairos/data`，但只暴露当前 Workspace 绑定过的本地名字。

### 3.4 写研究代码

研究不需要先创建 Study：

```python
from kairospy import Workspace

workspace = Workspace.open_or_create("alpha")
bars = workspace.data.get("bars").pandas()

def momentum_factor(bars):
    ...

factor = momentum_factor(bars)
workspace.artifact("momentum_factor").write(factor)
```

如果需要治理，可以在 artifact metadata 里记录 hypothesis、input hash、参数、代码 hash。不要把 Study 暴露成必须先创建的工作区。

### 3.5 写策略代码

策略也不需要先创建 Strategy：

```python
from kairospy import Workspace
from my_strategies.sma_cross import SmaCross

workspace = Workspace.open_or_create("alpha")
strategy = SmaCross(bars=workspace.data.get("bars"), fast=20, slow=50)
```

策略是用户代码。CLI 不应该要求用户先 `strategy set-model`。

如果需要治理或发布可运行入口，可以记录 entrypoint：

```bash
kairospy workspace register-entrypoint alpha \
  --name sma-cross \
  --kind strategy \
  --entrypoint my_strategies.sma_cross:SmaCross
```

这是可选治理能力，不是写策略的前置步骤。

### 3.6 创建 Run Workspace

Run 才是新的隔离工作区。策略运行时必须选择一个 Workspace；Run 的本质是用这个 Workspace 的 data view 去驱动某个 strategy entrypoint：

```text
Run = Workspace snapshot + Strategy entrypoint + Mode + Runtime bindings
```

```bash
kairospy run create alpha-backtest \
  --workspace alpha \
  --mode backtest \
  --entrypoint my_strategies.sma_cross:SmaCross
```

或者一步运行：

```bash
kairospy run start \
  --workspace alpha \
  --mode backtest \
  --entrypoint my_strategies.sma_cross:SmaCross
```

这表示：用 `alpha` 工作区里绑定的 `bars`、`quote`、`signals` 等数据，驱动 `my_strategies.sma_cross:SmaCross` 运行。

paper/live 运行绑定 account 和 live view：

```bash
kairospy workspace bind-live alpha --name quote --dataset market.quote.crypto.binance.btc-usdt

kairospy run start \
  --workspace alpha \
  --mode paper \
  --entrypoint my_strategies.sma_cross:SmaCross \
  --account binance-testnet
```

Run Workspace 负责：

- execution mode。
- 固定被选择的 Workspace。
- 固定 workspace data binding snapshot。
- entrypoint snapshot。
- account binding。
- live feed binding。
- execution gateway binding。
- runtime database。
- kill switch。
- freshness gate。
- reconciliation。
- logs、reports、artifacts。

## 4. 边界定义

### 4.1 Project

Project 负责：

- 发现 `kairos.toml`。
- 解析 provider 配置。
- 定位 `.kairos/data`。
- 管理 project-level metadata。

Project 不负责：

- 绑定某个局部数据别名。
- 执行策略。
- 保存某次运行状态。

### 4.2 Data

Data 负责：

- Provider credentials 引用。
- Data Product 选择。
- historical acquisition。
- live view 注册。
- Dataset catalog。
- quality、coverage、freshness、lineage。
- dataset alias。
- snapshot、build record、content hash。

Data 不负责：

- 研究假设。
- 策略模型。
- 风控政策。
- 账户资金。
- 订单执行。
- 某次运行的 runtime state。

### 4.3 Workspace

Workspace 负责：

- 绑定 Dataset 为本地名字。
- 绑定 Live View 为本地名字。
- 提供 workspace-local Data facade：`workspace.data`。
- 保存 workspace-local params。
- 提供代码 API：`Workspace.open_or_create("alpha")`。
- 提供 artifact namespace。

Workspace 不负责：

- 下载数据。
- 保存数据副本。
- 强制区分 Study 和 Strategy。
- 持有 provider secret。
- 持有实盘 runtime database。
- 持有 account lease。

推荐内部结构：

```text
.kairos/workspace/alpha/
  workspace.json
  bindings.json
  params.json
  data/
    aliases.json
    snapshots/
    cache/
  artifacts/
```

### 4.4 Workspace Data Isolation

Workspace 的数据隔离应该是逻辑隔离，不是默认物理复制。

全局 Data Lake 仍然是唯一权威数据存储：

```text
.kairos/data/
  catalog/
  datasets/
  live/
```

Workspace 拥有自己的 data view：

```text
.kairos/workspace/alpha/data/
  aliases.json
  snapshots/
  cache/
```

其中：

- `aliases.json` 记录 workspace-local name 到全局 Dataset / Live View 的绑定。
- `snapshots/` 记录某次 freeze 或 run 使用的 binding snapshot。
- `cache/` 可以放 workspace-local derived cache，但不能替代全局 Dataset。

示例：

```json
{
  "bars": {
    "kind": "dataset",
    "dataset": "bars.us.equity.1d",
    "release_id": "ds_...",
    "content_hash": "..."
  },
  "quote": {
    "kind": "live_view",
    "dataset": "market.quote.crypto.binance.btc-usdt",
    "freshness_seconds": 5
  }
}
```

代码 API：

```python
workspace = Workspace.open_or_create("alpha")

workspace.data.bind("bars", dataset="bars.us.equity.1d")
bars = workspace.data.get("bars")

workspace.data.bind_live("quote", dataset="market.quote.crypto.binance.btc-usdt")
quote = workspace.data.live("quote")
```

这带来三个好处：

- 用户在代码里只关心 `bars`、`quote` 这些本地名字。
- 多个 Workspace 可以绑定同一个全局 Dataset，但使用不同本地名字、参数和 artifact。
- Run start 可以固定当前 Workspace data view，形成可审计的 `workspace_snapshot.json`。

### 4.5 User Code

用户代码负责：

- 研究。
- 因子。
- 实验。
- 策略。
- 参数搜索。
- 自定义报告。

KairoSpy 不应该要求用户把代码放进固定的 `studies/` 或 `strategies/` 目录。

### 4.6 Strategy Design

Strategy 不是工作区，但策略需要有明确 protocol。参考 `kairos_v2` 的设计，策略应该是事件驱动的可运行代码：Run/Hub 提供只读上下文和系统事件，策略返回决策；副作用由 Run runtime 执行。

核心原则：

- 策略不持有 Workspace；Run 构建策略时把 Workspace 解析后的数据句柄传入。
- 策略运行时只读 `StrategyContext`。
- 策略不直接下单、不直接访问 provider、不直接读全局 `.kairos/data`。
- 策略返回 `StrategyDecision`，Run adapter 将 decision 转换成 intent / order plan / rebalance plan。
- readiness、available、unavailable 这类运行状态由 Run runtime 推导后传给策略。

#### 4.6.1 文件级合约

用户策略文件推荐暴露两个对象：

```python
from kairospy import Workspace
from kairospy.strategy import StrategyDecision, StrategyEvent


REQUIRES = {
    "inputs": {
        "bars": {"kind": "dataset", "schema": "ohlcv"},
        "quote": {"kind": "live_view", "schema": "quote", "required_for": ["paper", "live"]},
    },
    "params": {
        "fast": {"type": "int", "default": 20, "min": 1},
        "slow": {"type": "int", "default": 50, "min": 2},
    },
    "decision_contract": "target_position.v1",
    "risk_requirements": {
        "max_gross_exposure": "required",
    },
    "execution_requirements": {
        "order_types": ["market", "limit"],
        "time_in_force": ["day", "ioc"],
    },
}


def build(workspace: Workspace, params: dict) -> "StrategyProtocol":
    return SmaCross(
        bars=workspace.data.get("bars"),
        fast=int(params.get("fast", 20)),
        slow=int(params.get("slow", 50)),
    )
```

`REQUIRES` 是静态需求声明。Run start 先用它校验 Workspace data view、params、mode、account 和 execution capabilities，再加载策略。

`build(workspace, params)` 是 entrypoint。它只从 Workspace 读取已绑定的数据本地名。

#### 4.6.2 Python Protocol 草案

核心包应只保留 protocol，不保留具体策略实现：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID, uuid4


class StrategyEventKind(StrEnum):
    START = "start"
    TICK = "tick"
    DATA = "data"
    FILL = "fill"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class StrategyEvent:
    kind: StrategyEventKind
    timestamp: datetime
    name: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    run_id: str
    workspace_id: str
    mode: str
    now: datetime
    data: "WorkspaceDataView"
    portfolio: "PortfolioView | None" = None
    account: "AccountView | None" = None
    market: "MarketView | None" = None
    readiness: "ReadinessView | None" = None
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    decision_id: UUID
    timestamp: datetime
    contract: str
    actions: tuple[Mapping[str, Any], ...]
    reason: str
    confidence: float | None = None
    valid_until: datetime | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def none(cls, *, timestamp: datetime, reason: str) -> "StrategyDecision":
        return cls(uuid4(), timestamp, "none.v1", (), reason)


class StrategyProtocol(Protocol):
    @property
    def strategy_id(self) -> str: ...

    def on_start(self, context: StrategyContext) -> Sequence[StrategyDecision]:
        ...

    def on_event(
        self,
        event: StrategyEvent,
        context: StrategyContext,
    ) -> Sequence[StrategyDecision]:
        ...

    def on_tick(
        self,
        tick: int,
        context: StrategyContext,
    ) -> Sequence[StrategyDecision]:
        ...

    def on_stop(self, context: StrategyContext) -> Sequence[StrategyDecision]:
        ...

    def can_exit(self, context: StrategyContext) -> bool:
        ...
```

这对应 `kairos_v2` 的思路：

```text
on_start
on_event(CoreEvent)
on_tick
on_strategy_event(Ready / Unavailable / Available)
on_stop
can_exit
```

Python 版合并为：

```text
on_start(context)
on_event(event, context)
on_tick(tick, context)
on_stop(context)
can_exit(context)
```

其中 `Ready / Unavailable / Available` 作为 `StrategyEvent.kind` 传入，不需要单独一个方法。

#### 4.6.3 简化策略也应支持

为了让普通用户容易写策略，Run adapter 应支持更小的 callable：

```python
def decide(context):
    bars = context.data.get("bars").pandas()
    ...
    return StrategyDecision(...)
```

等价适配：

```text
decide(context) -> on_tick(...)
```

因此支持三种 entrypoint：

| Entrypoint | 适用场景 |
|---|---|
| `build(workspace, params) -> StrategyProtocol` | 完整事件驱动策略 |
| `decide(context) -> StrategyDecision | list[StrategyDecision]` | 简单 backtest / signal 策略 |
| `run(workspace, params) -> artifact` | 研究、因子、报告，不进入交易 runtime |

#### 4.6.4 示例策略

```python
class SmaCross:
    def __init__(self, *, bars, fast: int = 20, slow: int = 50) -> None:
        self.strategy_id = "sma-cross"
        self.bars = bars
        self.fast = fast
        self.slow = slow

    def on_start(self, context):
        return ()

    def on_event(self, event, context):
        if event.kind == StrategyEventKind.UNAVAILABLE:
            return (StrategyDecision.none(timestamp=context.now, reason="runtime unavailable"),)
        return ()

    def on_tick(self, tick, context):
        bars = self.bars.pandas()
        signal = compute_sma_cross(bars, fast=self.fast, slow=self.slow)
        if signal == "flat":
            return (StrategyDecision.none(timestamp=context.now, reason="no crossover"),)
        return (
            StrategyDecision(
                decision_id=uuid4(),
                timestamp=context.now,
                contract="target_position.v1",
                actions=({
                    "kind": "target_position",
                    "instrument": "workspace:bars.primary",
                    "target": 1 if signal == "long" else 0,
                },),
                reason=f"sma_cross fast={self.fast} slow={self.slow}",
            ),
        )

    def on_stop(self, context):
        return ()

    def can_exit(self, context):
        return True


def build(workspace, params):
    return SmaCross(
        bars=workspace.data.get("bars"),
        fast=int(params.get("fast", 20)),
        slow=int(params.get("slow", 50)),
    )
```

Run 通过 entrypoint 加载策略，并把选中的 Workspace 传给策略构建函数：

```bash
kairospy run start \
  --workspace alpha \
  --mode backtest \
  --entrypoint my_strategies.sma_cross:build \
  --param fast=20 \
  --param slow=50
```

Run start 时校验：

- Workspace 是否绑定了策略声明的 inputs。
- Dataset / Live View 是否满足 schema、quality、freshness 要求。
- Params 是否可以被序列化并固定。
- Paper/live mode 是否绑定了满足 execution requirements 的 account/provider。

这样策略设计是清楚的，但不需要 Strategy Workspace。

### 4.7 Run Workspace

Run Workspace 负责：

- 隔离一次执行。
- 选择并固定一个 Workspace。
- 固定该 Workspace 的 data view。
- 固定 entrypoint。
- 固定参数。
- 绑定 account。
- 绑定实时 feed。
- 绑定 execution gateway。
- 记录 supervisor state。
- 输出 logs、reports、metrics、orders、fills、reconciliation。

推荐内部结构：

```text
.kairos/run/{run_id}/
  run.json
  workspace_snapshot.json
  entrypoint.json
  runtime.sqlite3
  logs/
  artifacts/
  reports/
```

Run 不需要设计成复杂调度系统。最小设计是一个可审计的执行容器。

#### 4.7.1 Run manifest

`run.json` 记录这次执行的固定输入：

```json
{
  "run_id": "run_20260721_001",
  "workspace": "alpha",
  "mode": "backtest",
  "entrypoint": "my_strategies.sma_cross:build",
  "params": {"fast": 20, "slow": 50},
  "status": "created",
  "created_at": "2026-07-21T00:00:00Z"
}
```

`workspace_snapshot.json` 固定 Workspace data view：

```json
{
  "workspace": "alpha",
  "bindings": {
    "bars": {
      "kind": "dataset",
      "dataset": "bars.us.equity.1d",
      "release_id": "ds_...",
      "content_hash": "..."
    }
  }
}
```

#### 4.7.2 Run lifecycle

Run 只需要几个状态：

```text
created -> prepared -> running -> completed
                         |-> failed
                         |-> stopped
```

含义：

- `created`: manifest 已写入，但未加载策略。
- `prepared`: Workspace snapshot、entrypoint、params、mode 校验通过。
- `running`: 正在驱动 strategy protocol。
- `completed`: 正常结束并写出 reports。
- `failed`: 运行失败，保留错误和最后状态。
- `stopped`: 用户或 kill switch 停止。

#### 4.7.3 Run start 流程

`kairospy run start --workspace alpha --entrypoint ...` 做这些事：

1. 读取 Project config。
2. 打开 Workspace。
3. 固定 Workspace data view 到 `workspace_snapshot.json`。
4. 加载 entrypoint module。
5. 读取 `REQUIRES` 并校验 bindings、params、mode。
6. 调用 `build(workspace, params)` 或适配 `decide(context)`。
7. 创建 `StrategyContext`。
8. 按 mode 驱动策略 lifecycle。
9. 把 decisions、orders、fills、metrics、errors 写入 Run workspace。

#### 4.7.4 Mode 差异

Run mode 只决定 runtime binding，不改变策略代码：

| Mode | 数据来源 | 执行绑定 |
|---|---|---|
| `backtest` | Workspace historical Dataset snapshot | simulated fills |
| `historical-simulation` | historical Dataset + runtime store | simulated venue/runtime |
| `shadow` | Live View | no real orders |
| `paper` | Live View | paper/simulated account |
| `live` | Live View | real account, requires explicit live confirmation |

策略只看到 `StrategyContext` 和 `StrategyEvent`。Run 负责把不同 mode 的数据和执行能力适配成统一上下文。

## 5. CLI 收敛建议

### 5.1 保留和新增

保留：

```bash
kairospy init
kairospy configure
kairospy data use
kairospy data connect
kairospy data inspect
```

新增或强化：

```bash
kairospy workspace create NAME
kairospy workspace bind-data NAME --name LOCAL --dataset DATASET
kairospy workspace bind-live NAME --name LOCAL --dataset LIVE_VIEW
kairospy workspace inspect NAME
kairospy workspace register-entrypoint NAME --name ENTRY --kind strategy --entrypoint module:callable

kairospy run create RUN_ID --workspace NAME --mode MODE --entrypoint module:callable
kairospy run start --workspace NAME --mode MODE --entrypoint module:callable
kairospy run inspect RUN_ID
```

### 5.2 移除 Study/Strategy 工作区命令

设计目标中不保留 Study Workspace / Strategy Workspace，因此不应继续设计这些命令：

```bash
kairospy study open
kairospy study add-data
kairospy study freeze
kairospy strategy open
kairospy strategy add-data
kairospy strategy set-model
kairospy strategy freeze
kairospy run start --study ...
kairospy run start --snapshot ...
```

它们不应该作为兼容 alias，也不应该作为高级治理入口继续存在。否则用户仍然会把 Study 和 Strategy 理解成两个工作区。

需要迁移的能力应进入新的边界：

```text
study add-data      -> workspace bind-data
strategy add-data   -> workspace bind-data
strategy set-model  -> run start --entrypoint 或 workspace register-entrypoint
study freeze        -> workspace artifact freeze
strategy freeze     -> workspace artifact freeze 或 run snapshot
run start --study   -> run start --workspace NAME --entrypoint module:callable
run start --snapshot -> run start --workspace NAME --entrypoint module:callable
```

主路径收敛到：

```text
data -> workspace -> code -> run
```

### 5.3 删除默认脚手架

`init` 不创建：

```text
studies/
strategies/
```

如果用户需要模板，使用显式 scaffold：

```bash
kairospy scaffold research --output research/momentum.py
kairospy scaffold strategy --output src/strategies/sma_cross.py
```

模板命令只生成代码，不创建新的数据工作区。

## 6. 内部迁移方向

### 6.1 Product surface 收敛

当前 product surface 里的 `.kairos/data/studies` 和 `.kairos/data/strategies` 应剔除为工作区模型。保留历史数据迁移脚本可以存在，但新设计不继续支持它们作为运行时概念。

新默认路径应是：

```text
.kairos/workspace/{workspace_id}
.kairos/run/{run_id}
```

Study artifact 可以放在：

```text
.kairos/workspace/{workspace_id}/artifacts/research/{artifact_id}
```

Strategy artifact 可以放在：

```text
.kairos/workspace/{workspace_id}/artifacts/strategies/{artifact_id}
```

但它们不是 workspace。

### 6.2 Run 从 Strategy Snapshot 解耦

Run 不应要求 `Strategy Lock`。

Run 应接受：

- workspace id。
- entrypoint。
- params。
- mode。
- account。
- optional frozen artifact。

Run start 时生成自己的 immutable snapshot：

```text
workspace id
workspace data view snapshot
entrypoint snapshot
params snapshot
data release evidence
live view evidence
runtime contract
```

这份 Run snapshot 才是执行审计的核心。

### 6.3 删除 Study/Strategy Workspace 模型

内部实现应删除这些作为新模型一部分的概念：

```text
StudyWorkspace
StudyCandidate
StrategyWorkspace
StrategyLock as required run input
.kairos/data/studies as workspace root
.kairos/data/strategies as workspace root
```

如果需要读取旧项目，可以提供一次性迁移命令，而不是保留双模型：

```bash
kairospy migrate workspaces --from legacy-study-strategy --to workspace-run
```

迁移后产物进入：

```text
.kairos/workspace/{workspace_id}/artifacts/
.kairos/run/{run_id}/
```

## 7. 文档调整

README next steps 应从：

```text
python studies/starter.py
```

调整为：

```bash
kairospy data start
kairospy workspace create alpha
kairospy workspace bind-data alpha --name bars --dataset bars.us.equity.1d
python src/my_script.py
kairospy run start --workspace alpha --mode backtest --entrypoint my_strategies.sma_cross:SmaCross
```

公开文档中应避免说：

```text
Study workspace
Strategy workspace
```

应该说：

```text
Workspace data binding
Research code
Strategy code
Run workspace
```

## 8. 代码删除清单

本节描述为了落地新模型，应该删除或迁移哪些现有代码。目标不是删除所有名字里带 `study` 或 `strategy` 的代码，而是删除 Study/Strategy 作为工作区的产品模型。

### 8.1 应删除的 CLI 入口

删除 `kairospy/__main__.py` 中作为顶层产品入口的 Study/Strategy workspace 命令：

```text
study open
study add-data
study add-factor
study create
study start
study plan
study freeze
study inspect
study data
study factor-run
study publish-factor
study profile
study scaffold

strategy open
strategy bind-factor
strategy set-risk
strategy set-execution
strategy set-model
strategy set-model-code
strategy freeze
strategy inspect
strategy status
strategy activate
strategy rollback
strategy check-promotion
strategy promote
```

对应代码位置：

- `kairospy/__main__.py` 里 `study = commands.add_parser("study", ...)` 整段。
- `kairospy/__main__.py` 里 `strategy_product = commands.add_parser("strategy", ...)` 整段。
- `kairospy/__main__.py` handlers 中所有 `("study", ...)` 和 `("strategy", ...)` 映射。
- `_study_freeze_dispatch`、`_study_inspect_dispatch`、`_strategy_inspect_dispatch` 这类为旧分支服务的 dispatch helper。

保留或迁移到新入口：

- `study capture`、`study analyze`、`study capture-series` 这类 option capture / data capture 能力，不应挂在 `study` 下。应迁移到 `data capture`、`data analyze` 或 `capture` 顶层。
- `strategy register-builtins` 这类内置示例策略注册，不应作为用户主路径。若仍需要测试 fixture，迁移到测试 helper 或 `scaffold strategy`。

### 8.2 应删除的 Study Workspace 模型

删除 Study 作为 workspace 的模型：

```text
kairospy/study_platform/workspace.py
  StudyWorkspaceStatus
  StudyWorkspace
  StudyWorkspaceRepository
  study-workspaces/
  study-candidates/
```

同时删除或迁移依赖它的 API：

- `kairospy/study_platform/session.py` 中基于 `StudyWorkspaceRepository` 的 `open_study` / `StudySession` 工作区逻辑。
- `kairospy/study_platform/__init__.py` 中导出的 `StudyWorkspace`、`StudyWorkspaceRepository`、`StudyWorkspaceStatus`、`open_study`。
- `kairospy/product_workflow.py` 中创建、冻结、检查 `StudyWorkspace` 的流程。
- `features/us_equity_momentum_diagnostics.py` 中读取 `study-workspaces/.../input_releases.json` 的诊断逻辑。

迁移方向：

```text
StudyWorkspace.input_release_id      -> workspace.data binding
StudyWorkspace.input_content_hash    -> workspace data view snapshot
StudyWorkspace.hypothesis            -> artifact metadata
StudyWorkspace.freeze                -> artifact freeze 或 run snapshot
study-candidates                     -> artifacts/research
```

### 8.3 应删除的 Strategy Workspace 模型

删除 Strategy 作为 workspace / lock 的模型：

```text
kairospy/product_surface.py
  strategy_open
  strategy_bind_factor
  strategy_set_risk
  strategy_set_execution
  strategy_set_model
  strategy_set_model_code
  strategy_freeze
  strategy_inspect
  _strategy_dir
  _strategy_file
  _load_strategy
  _load_strategy_lock
  _assert_strategy_study_consistency
  _strategy_workspace_differs_from_lock
```

删除 Strategy 必须来自 Study Lock 的假设：

```text
Strategy workspace must derive from the current Frozen Study Lock hash
```

迁移方向：

```text
strategy data            -> workspace.data bindings
strategy model           -> entrypoint metadata 或 run --entrypoint
strategy risk            -> strategy REQUIRES.risk_requirements 或 run risk binding
strategy execution       -> strategy REQUIRES.execution_requirements 或 run execution binding
strategy lock            -> run snapshot / optional artifact freeze
strategy promotion       -> run evidence + artifact metadata
```

删除或迁出 `kairospy/strategies` 中的内置策略实现。核心包不应该携带具体交易策略，否则用户会继续把 Strategy 理解成 KairoSpy 内置产品。

应删除或迁出到 `examples/strategies/`、模板或测试 fixture：

```text
kairospy/strategies/btc_iron_condor.py
kairospy/strategies/bull_put_spread.py
kairospy/strategies/cash_and_carry.py
kairospy/strategies/covered_call.py
kairospy/strategies/protective_put.py
kairospy/strategies/sma_cross_strategy.py
kairospy/strategies/sma_cross_study_backtest.py
kairospy/strategies/specs.py
kairospy/strategies/registry.py
kairospy/strategies/deployment.py
kairospy/strategies/promotion.py
kairospy/strategies/event_session.py
```

核心包只保留策略协议和运行合约。建议迁到更准确的位置：

```text
kairospy/strategies/strategy_protocols.py -> kairospy/strategy/protocols.py
kairospy/strategies/runtime.py            -> kairospy/strategy/runtime.py 或 kairospy/run/strategy_adapter.py
```

保留内容应只包括：

- `Strategy` protocol。
- `StrategyContext` / decision context。
- `StrategyDecision` 或更通用的 decision contract。
- 将用户 strategy callable 适配到 Run runtime 的 adapter。

`kairospy/application/strategy_runtime.py`、`strategy_run_loop.py` 中可复用的运行组件可以保留，但要改成由 `run start --workspace ... --entrypoint ...` 调用，而不是由 Strategy Workspace / Strategy Lock 调用。

### 8.4 应拆分 product_surface.py

`kairospy/product_surface.py` 目前同时承载 Data、Study、Strategy、Run 多套产品 surface。新模型下应拆分：

保留并迁移：

```text
data_*        -> kairospy/data/surface.py 或现有 data service
run_*         -> kairospy/run/surface.py
workspace_*   -> 新增 kairospy/workspace/surface.py
```

删除：

```text
study_*
strategy_* workspace functions
ProductPaths.studies
ProductPaths.strategies
```

保留但改名：

```text
ProductPaths.runs       -> RunPaths
ProductPaths.root/data  -> Project/Data paths
```

新增：

```text
ProductPaths.workspaces 或 WorkspacePaths
```

### 8.5 应删除的 init 脚手架

删除 `kairospy/project.py` 中默认创建 Study/Strategy 源码目录和 starter 文件的逻辑：

```text
_directories():
  Path("studies")
  Path("strategies")

_files():
  studies/starter.py
  strategies/__init__.py
  strategies/starter_sma.py

next_steps:
  python studies/starter.py
```

替换为：

```text
kairospy workspace create alpha
kairospy workspace bind-data alpha --name bars --dataset ...
python src/my_script.py
kairospy run start --workspace alpha --entrypoint ...
```

### 8.6 应迁移的测试和示例

删除或重写测试：

- `tests/test_study_workspace.py`
- `tests/test_study_session.py` 中验证 `StudyWorkspaceRepository`、`study-workspaces`、`open_study` 的用例。
- `tests/test_product_cli.py` 中验证 `study add-data`、`study add-factor`、`strategy bind-factor`、`strategy set-model-code` 的用例。
- `tests/test_four_product_surface.py` 中验证 Study/Strategy workspace freeze、lock、promotion 的用例。
- `tests/test_crypto_momentum_study_start.py` 中依赖 `open_study` 或 governed study start 的用例。

迁移为新测试：

```text
test_workspace_create_writes_bindings
test_workspace_data_facade_resolves_local_names
test_workspace_data_does_not_copy_global_dataset
test_run_start_snapshots_workspace_data_view
test_run_start_passes_workspace_to_entrypoint
test_strategy_requires_inputs_are_checked_against_workspace_bindings
test_paper_run_binds_account_and_live_view
```

示例迁移：

- `examples/study_explore.py`、`examples/btc_sma_first.py` 改为 `Workspace.open_or_create(...)`。
- `examples/four_product_user_path.sh` 改为 `data -> workspace -> code -> run`。
- `examples/strategy/*_lifecycle.py` 中创建 `StudyWorkspace` 的流程删除，改为 workspace binding + run entrypoint。

### 8.7 study_platform 的处理方式

`study_platform` 不适合作为新 Workspace 管理的基础复用。原因是它的核心抽象已经绑定旧模型：

```text
StudyWorkspace
StudyWorkspaceRepository
open_study
study-workspaces/
study-candidates/
```

新 Workspace 管理应重新实现，而不是在 `study_platform` 上打补丁。

新增包建议：

```text
kairospy/workspace/
  __init__.py
  model.py          # WorkspaceManifest, WorkspaceBinding, WorkspaceDataView
  repository.py     # WorkspaceRepository
  data.py           # WorkspaceData facade
  artifacts.py      # workspace artifact namespace
  surface.py        # CLI/API surface
```

最小 API：

```python
workspace = Workspace.open_or_create("alpha")
workspace.bind_data(dataset="bars.us.equity.1d")
bars = workspace.data.get("bars")
```

`study_platform` 中可复用的能力应拆出来并改名，而不是被 Workspace 直接依赖：

- option capture。
- option snapshot analysis。
- option universe selector。
- normalized series capture。
- validation gates / claims / robustness。
- market snapshot data store。
- pricing、risk、backtest 中复用的 snapshot/spec 类型。

这些已经从 `study_platform` 包名迁出到更准确的包：

```text
kairospy/study_platform/option_capture.py        -> kairospy/capture/option_capture.py
kairospy/study_platform/spec.py                  -> kairospy/capture/spec.py
kairospy/study_platform/snapshot.py              -> kairospy/capture/snapshot.py
kairospy/study_platform/series.py                -> kairospy/capture/series.py
kairospy/study_platform/normalized_series.py     -> kairospy/capture/normalized_series.py
kairospy/study_platform/validation/*             -> kairospy/validation/*
kairospy/study_platform/data_store.py            -> kairospy/capture/data_store.py
kairospy/study_platform/features.py              -> kairospy/capture/features.py
```

`kairospy/study_platform` 目录本身已删除。删除的是 `Study Workspace` 产品模型和旧包名，不是捕获、验证、特征工程这些能力。

### 8.8 应删除的 strategies 包默认导出

删除 `kairospy/strategies` 整包。

新设计中用户策略来自用户代码 entrypoint，不来自：

旧的 `kairospy.strategies` import path 已删除。

如果需要示例策略，应通过用户项目 scaffold 或 examples 提供：

```bash
kairospy scaffold strategy --template sma-cross --output src/strategies/sma_cross.py
```

这样核心包只提供 `kairospy.strategy` 协议和 runtime adapter，不提供产品化策略。

## 9. 迁移优先级

### 当前落地状态

已落地：

- `kairospy init` 不再创建 `studies/`、`strategies/` 和 starter 文件。
- 新增统一 `kairospy workspace create/bind-data/bind-live/inspect`。
- 新增 `Workspace.open_or_create("name")` 和 `workspace.data` API。
- `run start` 只接受 `--workspace NAME --entrypoint module:callable --mode ...`。
- `run start` 写入 `.kairos/run/{run_id}`，并生成 `workspace_snapshot.json`、`entrypoint.json`、`artifacts/decisions.json` 和 `reports/summary.json`；`run(workspace, params)` 这类研究/报告入口会额外写入 `artifacts/result.json`。
- 顶层 CLI 不再暴露 `study` / `strategy` / `backtest` / `factor` / `tutorial` 命令。
- `run` 子命令不再暴露旧的 Strategy Release 路径：`backtest`、`simulate`、`paper`、`shadow`、`artifact-replay`、`capture-replay`。
- 用户可见的 Data target use 从 `study` / `research` 收敛为 `workspace`。
- `data freeze --study-id` 已改为 `data freeze --workspace`，数据快照 API 已改为 `DataInputSnapshot` / `write_data_snapshot` / `DatasetClient.freeze_snapshot`。
- `RunMode.STUDY` 和 `study_composition` 已删除，非执行态数据读取改为 `RunMode.WORKSPACE`。
- validation 可复用层的公开类型已从 `StudyRegistration` / `StudyValidationResult` 改为 `ExperimentRegistration` / `ExperimentValidationResult`，落盘目录从 `studies/` 改到 `validation/`。
- 策略协议新增到 `kairospy/strategy`。
- `kairospy/strategies` 整包已删除，核心包不再内置 SMA、Bull Put、Covered Call、Cash and Carry、BTC Iron Condor 等策略。
- `kairospy/product_workflow.py`、`kairospy/api.py`、旧内置策略 backtest/reference 封装已删除。
- `kairospy/study_platform` 包名已删除，可复用能力迁到 `kairospy/capture` 和 `kairospy/validation`。
- 旧 `StudyProductApi` / `StrategyProductApi` 和 `product_surface.py` 里的旧 `study_*` / `strategy_*` 产品函数已删除。
- 根项目状态中的旧 `.kairos/data/strategies` 和 `.kairos/data/study-workspaces` 已删除；新状态只保留 `.kairos/workspace/{name}` 和 `.kairos/run/{run_id}`。
- `.gitignore` 不再默认忽略 `studies/` 或 `strategies/`，用户代码目录由用户自己组织和纳入版本管理。

剩余后续增强：

- 一次性迁移命令不再是默认主路径；如果未来需要兼容历史用户项目，应做成显式、可审计的离线工具，而不是恢复旧 workspace runtime。
- `scaffold research` / `scaffold strategy` 可以作为后续便利命令，但不能由 `init` 默认创建目录。

### P0

- 已完成：`kairospy init` 不创建 `studies/`、`strategies/` 和 starter 文件。
- 已完成：统一 `workspace create/bind-data/bind-live/inspect`。
- 已完成：`Workspace.open_or_create("name")` 代码 API。
- 已完成：`run start --workspace ... --entrypoint ...`，不依赖 Strategy Lock。
- 已完成：删除 `run start --study` / `run start --snapshot`，Run 只接受 Workspace + strategy entrypoint。

### P1

- 已完成：移除 `study` / `strategy` 工作区命令在新 CLI 和用户文档中的入口。
- 已完成：更新 README、examples README、CLI help 和 hygiene 测试。
- 后续增强：将代码模板放到显式 `scaffold research` / `scaffold strategy`，不进入默认体验。

### P2

- 已完成：删除早期 `study_platform/workspace.py` 的 `study-workspaces`、`study-candidates` 模型。
- 已完成：Run manifest 使用 Workspace snapshot 作为执行审计核心。
- 后续增强：如果需要历史项目迁移，单独提供离线迁移工具。

## 10. 最终原则

KairoSpy 的默认体验应该是：

```text
Configure provider once.
Prepare data once.
Bind data to one workspace.
Write any code you want.
Run creates the execution workspace.
```

Study 不是产品入口。

Strategy 不是工作区。

Workspace 只绑定数据。

Run 才是执行隔离边界。
