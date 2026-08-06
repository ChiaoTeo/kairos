# 策略研究快照设计

## 1. 设计结论

策略研究的第一步不是实现交互式 App，也不是围绕当前的 `timeline`、projection 或 artifact 继续补功能，而是系统性定义策略研究系统应该保存和呈现什么。

运行输出是研究数据的 canonical artifact；快照是基于运行输出组织出来的研究视图。当前系统中的概念、模块和文件都只是迁移来源，不应反过来决定目标模型。

快照是研究证据，不是 UI 的临时数据结构。它应该基于规范化运行输出回答：

> 在某个运行或决策时刻，市场是什么状态，策略看到了什么，策略想做什么，系统允许什么，最后实际发生了什么？

Textual App 以后只负责浏览、筛选和翻阅这些快照，不负责定义研究语义。

因此正确的依赖关系是：

```text
Strategy Run
    ↓
Canonical Run Output
    ├── RunSnapshot
    ├── DecisionSnapshot
    └── TradeSnapshot
            ↓
       CLI / Textual / Notebook / Report
```

本设计采用“目标模型优先、现有实现迁移”的方式：先确定策略开发者的研究问题、最小事实集合和稳定关系，再决定哪些现有能力保留、合并、改名或删除。

## 2. 目标系统的收敛模型

### 2.1 一个核心对象：Research Run

策略研究的中心对象是 `ResearchRun`，它可以是一次回测、纸上交易或真实运行。不同运行模式共享同一套研究输出合同，区别只体现在数据来源、时钟、执行环境和真实性声明。

```text
ResearchRun
  ├── RunManifest        运行身份与复现条件
  ├── DecisionRecords    策略观察与决策事实
  ├── ExecutionRecords   订单、成交与执行事实
  ├── PortfolioRecords   持仓、现金、权益与风险事实
  ├── TradeRecords       交易生命周期与归因事实
  ├── Diagnostics        数据、策略、风控和运行问题
  └── Artifacts          原始数据、配置、报告和导出物
```

`ResearchRun` 是研究边界，不代表必须实现一个新的运行时 manager 或 coordinator。它首先是一个稳定的业务概念和输出合同。

### 2.2 事实、视图和表面

系统中需要严格区分三层：

```text
Facts / Records
    ↓ derive
Research Views / Snapshots
    ↓ render
CLI / Textual / Notebook / Report
```

- **Facts / Records**：运行过程中发生的、带时间和关联关系的研究事实。
- **Research Views / Snapshots**：面向问题组织的只读研究视图。
- **Surfaces**：把视图转换为文本、JSON、表格或终端界面。

事件日志、数据库表和内部 projection 都属于事实或实现细节；它们不能直接成为研究者必须理解的产品模型。

### 2.3 统一术语

为了避免多个模块分别创造相近概念，目标术语统一如下：

| 研究概念 | 统一含义 | 不再单独作为产品概念 |
| --- | --- | --- |
| Research Run | 一次可识别、可复现的策略运行 | launch instance、viewer session |
| Decision | 策略基于上下文形成的一次意图 | signal event、timeline item |
| Execution | 意图经过约束后形成订单和成交的过程 | order log、fill stream 的割裂视图 |
| Portfolio State | 运行时点上的持仓、现金、权益和风险 | 多个不一致的 account snapshot |
| Trade | 从开仓到平仓的研究归因单位 | 仅按 order 拼接的临时结果 |
| Diagnostic | 影响结果解释的异常或不确定性 | 只存在于日志里的 warning |
| Artifact | 可复用的输入、输出或证据文件 | 前端专属 static artifact |
| Snapshot | 针对研究问题组织的只读证据 | Timeline 页面本身 |

目标是让一个研究问题只对应一个主要概念；已有同义对象应迁移到统一对象下，而不是继续增加兼容层。

### 2.4 按研究问题组织，而不是按技术模块组织

研究者的入口应该是：

- 这次运行的结果是什么？
- 某个决策为什么发生？
- 某笔交易如何贡献收益？
- 实际执行和策略意图差在哪里？
- 结果中哪些部分不可信？

因此系统的查询边界应围绕 `run`、`decision`、`trade`、`execution` 和 `diagnostics` 设计。`timeline`、`projection`、`event`、`store` 是实现这些查询的手段，不应成为顶层研究导航。

## 3. 运行输出规范

### 2.1 运行输出是什么

一次策略运行应产出一个完整、稳定、可序列化的研究 artifact，而不是只返回最终收益或散落在日志中的运行信息。

它至少应包含四个部分：

```text
RunOutput
  ├── manifest       运行身份、版本、数据和参数
  ├── observations   策略观察和决策记录
  ├── outcomes       订单、成交、持仓、权益和交易结果
  └── diagnostics    异常、缺失、拒绝和数据质量
```

运行输出是“这次运行产生了哪些研究事实”；快照是“研究者现在想查看哪一组事实”。

### 2.2 Manifest

`manifest` 用于回答“这次运行是否可复现”：

- `run_id`
- strategy identity and version
- parameters and configuration
- mode: backtest / paper / live
- dataset identity and market universe
- time range and clock / bar interval
- account configuration and initial capital
- code revision or run fingerprint
- created_at / completed_at
- output schema version

如果缺少这些字段，运行结果即使数值正确，也很难用于严肃比较。

### 2.3 Observations

`observations` 记录策略实际做出判断时使用的研究上下文，而不是只记录最终 signal：

- observed timestamp
- instrument
- market data reference or normalized market context
- strategy state or relevant feature values
- signal and signal reason when available
- current position and account context
- target position or order intent

不要求第一版保存所有原始行情，但必须能够知道决策引用了哪个数据时间点和数据集版本。

### 2.4 Outcomes

`outcomes` 记录策略意图经过系统后实际产生的结果，并保持不同层次：

```text
signal
  → target / order intent
  → risk decision
  → accepted order
  → fills
  → position / account state
  → trade result
```

每一层都应有稳定关联 ID，并允许表示“没有发生”。例如 signal 可能没有生成订单，订单可能没有成交，成交可能只完成一部分。

### 2.5 Diagnostics

诊断信息属于正式运行输出，不能只写入日志：

- missing or stale market data
- invalid timestamps or ordering
- rejected or adjusted orders
- partial or unfilled orders
- account state inconsistency
- strategy exception or stopped execution
- incomplete output

诊断至少应包含级别、时间、对象、原因和是否影响结果解释。

### 2.6 输出格式

运行事实由 instance 下的 `run.sqlite` 持久化。JSON、Markdown 和文本是查询结果的导出格式，不是第二个 canonical store。

研究查询可以导出一个目录或逻辑 artifact：

```text
run/
  manifest.json
  observations.jsonl
  orders.jsonl
  fills.jsonl
  positions.jsonl
  trades.jsonl
  diagnostics.jsonl
```

实际物理布局可以调整，但逻辑上应保持上述边界。数据库 schema 变更通过版本管理，导出格式不能依赖当前 Python 类的序列化结果。

## 4. 背景与问题

当前的 Timeline 更接近运行事件和展示投影。它适合记录系统发生过什么，但不一定能直接回答策略开发者的研究问题：

- 这次回测的收益是否可信？
- 某一笔交易为什么发生？
- 策略的意图是否正确传递成订单？
- 亏损来自 signal、仓位控制、成交质量，还是数据问题？
- 某个时间点的市场、策略、账户状态是否可以重现？

如果先做一个事件列表或交互式图表，信息虽然更多，研究路径却未必更清楚。因此需要先把研究对象建模为快照。

## 5. 目标与非目标

### 目标

- 让一次回测可以被快速总结和复核。
- 让单个策略决策可以独立解释。
- 把策略意图、系统约束和实际执行结果分开表达。
- 让异常、缺失数据和不确定性成为一等信息。
- 先支持文本和 JSON 输出，再复用到 Textual、Notebook 或报告。
- 快照带有足够上下文，能够脱离 UI 阅读。

### 非目标

- 第一阶段不实现 Web 前端。
- 第一阶段不实现复杂的交互式图表。
- 快照不是事件日志的完整替代品。
- 不把所有内部对象或原始 vendor payload 暴露给研究者。
- 不在展示层重新计算业务结果。

## 6. 研究对象与信息层次

研究信息按三个层次组织：

```text
RunSnapshot
  ├── DecisionSnapshot
  │     └── Execution evidence
  └── TradeSnapshot
```

### 5.1 运行快照

运行快照描述一次回测或一次可观察运行的整体结果。它解决“这次运行发生了什么，以及是否值得继续分析”。

建议包含：

- `run_id`
- 策略名称、版本和参数
- 数据集、市场、时间范围和周期
- 初始权益、最终权益、收益率
- 最大回撤、风险指标和暴露
- 订单数、成交数、交易数
- 手续费、滑点和其他成本
- 起止时间、运行状态
- 数据质量、执行和账户诊断
- 相关产物的引用，而不是直接嵌入大文件

运行快照应该优先呈现结论，再允许进入决策和交易明细。

### 5.2 决策快照

决策快照是最重要的研究单位，描述策略在一个明确时刻的一次决策链。

建议包含：

```text
DecisionSnapshot
  identity
    run_id
    decision_id
    observed_at
    symbol / instrument

  market_context
    latest quote or bar
    order book context when available
    data timestamp and freshness

  strategy_context
    strategy state
    features or indicators used by the strategy
    signal
    signal strength / reason when available

  portfolio_context
    current position
    cash / equity
    exposure
    pending orders

  intent
    target position
    order intent
    requested quantity and price

  constraints
    risk decision
    adjusted quantity or price
    rejection reason

  execution
    order identity
    order state
    fills
    average fill price
    fees and slippage

  outcome
    position after decision
    equity after decision
    realized / unrealized effect

  diagnostics
    warnings
    missing or stale fields
    invariant violations
```

快照必须能区分以下几个概念：

```text
strategy intent  !=  accepted order  !=  actual fill  !=  account outcome
```

如果某一层没有发生，也要明确表达。例如策略产生了 signal，但没有生成订单；订单生成了，但被风控拒绝；订单被接受了，但没有成交。

### 5.3 交易快照

交易快照描述一个完整的进出场周期，解决“这一笔交易贡献了什么，以及为什么结束”。

建议包含：

- `trade_id`、symbol、方向和数量
- entry / exit 时间和价格
- 持有时间
- 毛收益、净收益
- 手续费、滑点
- entry signal
- exit reason
- 最大有利 excursion
- 最大不利 excursion
- 持仓期间的最大回撤
- 关联的 decision、order 和 fill 引用

交易快照不应覆盖决策快照的所有内容，而应通过稳定 ID 关联回去。

## 7. 快照的公共属性

所有快照都应具备以下属性：

- **可定位**：具有稳定 ID 和明确的业务时间。
- **可复现**：记录策略版本、参数、数据集和运行上下文。
- **可解释**：字段名称表达业务含义，不直接暴露内部实现细节。
- **可降级**：数据不完整时保留快照，并标记 `unknown`、`stale` 或 `missing`。
- **可关联**：运行、决策、交易、订单和成交之间通过 ID 关联。
- **可导出**：同一份快照可以输出文本和 JSON。
- **只读**：快照是观察结果，不承担修改运行状态的职责。

快照中的“没有数据”和“数据为零”必须能够区分。例如没有成交不等于成交数量为零；没有权益估值不等于权益为零。

## 8. 第一阶段的使用方式

第一阶段以 CLI 为主，不做交互式 App。命令形态可以沿用现有 launch/query 风格，目标接口如下：

```bash
kairospy inspect run <run-id>
kairospy inspect decision <run-id> --id <decision-id>
kairospy inspect trade <run-id> --id <trade-id>

kairospy inspect run <run-id> --format json
kairospy inspect decision <run-id> --format json
```

默认文本输出应该适合逐个阅读；JSON 输出应该适合后续测试、Notebook、批量分析和 Textual 使用。

第一阶段不要求一次性实现所有指标。优先保证快照的结构、关联和证据链稳定。

## 9. 文本输出原则

文本输出遵循“摘要在前，证据在后”的顺序：

```text
结论
关键指标
运行上下文
决策 / 交易明细
异常和不确定性
关联对象
```

一个决策快照应该在一个终端页面或一个短文档中读懂，而不是要求研究者先打开多个日志文件再自行拼接。

示意：

```text
Decision D-0042 · 2024-03-12 10:35:00 · SPY

Signal       mean_reversion.long  zscore=-2.14
Position     0 -> target 100
Order        BUY 100 @ market
Risk         accepted
Execution    filled 100 @ 512.40
Cost         fee=4.12  slippage=7.80
Outcome      equity +182.40  position=100

Market       last=512.35  bar=5m  freshness=ok
Warnings     none
Related      order O-1024 · fill F-1024 · trade T-1024
```

## 10. 与目标存储的关系

`run.sqlite` 是运行事实和研究查询的 canonical store。Timeline 不再作为独立存储或产品概念保留：

- Research records：保存运行过程中形成的可观察事实。
- Snapshot：把相关事实组织成研究者可以理解的证据单元。
- CLI：将查询结果转换为文本或 JSON。
- Textual：浏览和导航查询结果，不重新定义业务逻辑。

快照可以引用事件或 artifact，但不应要求调用者理解事件总线、服务实例或 SQLite 内部表才能使用。旧的 `timeline.jsonl`、JSONL 结果输出和 timeline catalog 在迁移后删除。

## 11. 实施顺序

### Phase 1：规范运行存储

- 确认 `run.sqlite` 中 manifest、observations、outcomes 和 diagnostics 的表边界。
- 确认数据库 schema version、ID、时间、关联关系和缺失值语义。
- 确认 JSON / 文本导出的最小结构。

### Phase 2：从运行输出生成快照

- 从规范化运行输出生成 `RunSnapshot`、`DecisionSnapshot` 和 `TradeSnapshot`。
- 增加按运行、决策和交易查询快照的 application API。
- 增加 CLI 输出和 JSON 序列化。
- 为关键关联和缺失数据增加测试。

### Phase 3：研究查询能力

- 按交易结果、signal、symbol、时间范围筛选。
- 增加决策到订单、成交、持仓和权益的关联。
- 增加滑点、费用、拒单和数据质量分析。

### Phase 4：Textual 快照浏览器

- 逐个浏览快照。
- 在运行、决策和交易之间跳转。
- 支持键盘筛选、前后翻页和详情展开。
- 复用 CLI 使用的同一套 application 查询和快照模型。

## 12. 验收标准

第一阶段完成的标准不是“有一个页面”，而是：

- 研究者可以只通过 CLI 看懂一次回测的整体结果。
- 研究者可以定位一笔交易对应的决策和执行链路。
- 策略意图、系统约束和实际成交结果不会混淆。
- 发生拒单、未成交、数据缺失或状态过期时，快照会明确指出。
- 同一快照可以稳定输出为文本和 JSON。
- 后续 Textual 不需要复制或重写研究逻辑。
