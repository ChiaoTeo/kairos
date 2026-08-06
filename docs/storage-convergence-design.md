# 运行存储收敛设计

## 1. 设计结论

一次 launch instance 应该是运行结果的存储边界。运行相关的持久化数据需要收敛到该 instance 下的一套 canonical store；CLI、Textual 和其它研究工具都从这一套 store 查询。

当前不再新增平行的 `RunResult` JSON、Timeline 数据库或前端专属 artifact。已有文件和 SQLite 记录只作为设计输入，直接迁移到目标存储模型。

目标不是把市场历史数据、凭证和所有工作区数据塞进同一个数据库，而是让“一次运行产生的事实”只有一个明确的持久化入口。

## 2. 当前存储盘点

### 2.1 Launch instance 目录

当前运行目录大致包含：

```text
.kairos/launches/<mode>/<launch-id>/
  current.json
  events.jsonl
  state.json
  summary.json
  instances/<instance-id>/
    command.json
    config.normalized.json
    events.jsonl
    launch.log
    live_state.json
    metrics.json
    state.json
    summary.json
    records.jsonl (legacy layout)
```

并不是每个 instance 都拥有全部文件。工作区样例中还存在大量只有 `state.json`、`summary.json`、`events.jsonl` 或 `timeline.jsonl` 的历史实例，说明当前文件布局既承担运行控制，也承担结果投影和兼容输出。

### 2.2 当前记录分类

| 当前记录 | 当前位置 | 当前作用 | 目标分类 |
| --- | --- | --- | --- |
| launch identity / lifecycle | `current.json`、`state.json` | 启停、状态、heartbeat、实例发现 | 运行控制状态 |
| launch event stream | `events.jsonl` | 系统消息或 launch 事件记录 | 运行事实 / 诊断来源 |
| execution audit | `execution.sqlite` | receipt、transition、reservation、订单状态变化 | 迁移至 `run.sqlite` 的执行事实 |
| run summary | `summary.json` | 最终权益、收益、订单数、成交数、指标 | `run.sqlite` metadata 的派生视图 |
| metrics | `metrics.json` | 运行指标 | `run.sqlite` metadata 的派生视图 |
| timeline | `timeline.jsonl` | 时间顺序的综合展示记录 | 迁移至 `run_records(stream=records)` |
| equity / fills / trades / intents | JSONL 输出 | 结果对象的历史导出 | 派生导出 |
| account current | `account/current.json` | 最新账户展示状态 | 当前状态投影 |
| live state | `live_state.json` | 实时运行恢复或状态 | 运行时恢复状态 |
| launch log | `launch.log` | 调试和运行日志 | 操作日志 |
| normalized config | `config.normalized.json` | 复现运行配置 | 运行输入 artifact |
| diagnostics | `.kairos/logs/diagnostics/*.jsonl` | 异常和 traceback | 诊断记录 |

### 2.3 当前实现中的关键事实

- `LaunchOutput.write_result()` 现在写入 `run.sqlite` 的 metadata 和 records。
- 旧的 JSONL 结果输出包括 `equity`、`fills`、`trades` 和 `intent_states`，由 `write_legacy_jsonl` 控制；这些输出属于待删除的旧模型。
- `LaunchOutput.append_history()` 可以将任意命名 stream 写入 JSONL，因此目前存在继续产生新文件名的可能。
- `SqliteOrderAuditStore` 现在在 instance 目录下使用 `run.sqlite`，保存订单执行审计，并提供按订单、实例、账户、symbol、状态和时间的查询。
- execution audit 的 payload 同时保存结构化列和完整 JSON，因此它适合保留执行证据，但不能直接代表整个运行结果。
- projection catalog 使用 `run.summary`、`run.metrics`、`run.records`、`run.equity`、`run.fills`、`run.intents` 和 `run.trades` 作为统一运行读模型。
- 当前 `timeline` 是综合投影，不是唯一事实源，目标迁移后直接删除这一存储概念。

## 3. 收敛原则

### 3.1 一个 instance，一个 canonical store

目标布局：

```text
.kairos/launches/<mode>/<launch-id>/instances/<instance-id>/
  run.sqlite          # 运行事实和研究查询的 canonical store
  config.normalized.json
  launch.log
  state.json          # 运行控制和恢复状态
  exports/            # 明确标记为派生导出
  artifacts/          # 输入、源码、报告等附件
```

`run.sqlite` 是一次运行的研究数据入口。`state.json` 和锁文件仍然可以存在，因为它们属于运行控制面，不是研究事实。

### 3.2 物理收敛不等于模块失去 owner

多个模块可以把自己的表写入同一个 SQLite 文件，但每张表仍由一个业务模块负责：

```text
run.sqlite
  ├── run / lifecycle tables       # launch composition
  ├── execution audit tables      # execution
  ├── strategy decision tables    # strategy
  ├── portfolio/account tables    # account
  └── diagnostics tables          # diagnostics support
```

跨模块调用必须通过 application API；研究查询负责组合公开的查询结果，不能让 CLI 或 Textual 直接拼接其它模块的内部表。

### 3.3 事实、状态和导出分开

```text
Canonical facts
    ↓
Derived views / analysis
    ↓
Exports and surfaces
```

- **Canonical facts**：订单状态变化、成交、策略决策、持仓变化、账户变化和诊断记录。
- **Current state**：为了恢复运行而保存的最新状态，例如 `state.json`、`live_state.json`。
- **Derived views**：summary、metrics、trade analysis、timeline 等查询结果。
- **Exports**：JSON、JSONL、Markdown、CSV 或未来的 Textual 数据接口。

同一份事实不应同时由 SQLite、timeline JSONL 和 summary JSON 分别维护。

### 3.4 研究查询不等于新存储

`run`、`decision`、`trade`、`diagnostics` 可以作为 application 层查询，不必一开始建成四组新的持久化对象。

```text
run.sqlite
  ↓
Research Queries
  ├── run summary
  ├── decision inspection
  ├── trade analysis
  └── diagnostic inspection
```

`timeline` 最终只是按时间查询这些记录的方式，不再是单独的产品和存储模型。

## 4. 目标 SQLite 逻辑结构

第一阶段只需要确定最小表边界，不追求一次性覆盖所有分析指标：

```text
run_metadata
execution_audit
strategy_decisions       # 如果策略研究需要，作为明确缺口补齐
portfolio_observations
diagnostics
```

订单、成交和订单状态变化优先复用现有 `execution_audit`，不复制到新的 orders/fills JSONL。交易、收益曲线、回撤和汇总指标优先由已有事实查询或运行结束时派生，不在第一阶段重复存储。

每条跨记录关联至少需要稳定的：

- `run_id` / `instance_id`
- `record_id`
- `observed_at` / `event_time`
- `causation_id` 或等价的因果关联
- `order_id`、`event_id`、`symbol` 等已有业务关联

具体表名和 schema 需要在盘点现有领域记录后再确定，不能直接把目前 JSON 的字段机械搬进表。

## 5. 哪些内容不进入 run.sqlite

以下内容保持独立：

- 大规模历史市场数据和数据 catalog
- 账户凭证和私密配置
- 工作区全局 launch index
- 进程锁和临时 heartbeat
- 纯调试日志
- 用户明确要求导出的报告文件

但运行必须保存这些外部输入的 identity、版本或路径引用，保证研究结果知道自己依赖了什么。

## 6. 迁移顺序

### Phase 0：确定目标 schema

- 确定 `run.sqlite` 的最小表边界、字段和关联关系。
- 新需求先判断属于事实、状态、派生视图还是导出。
- 不再为旧的 timeline、summary 或 JSONL 结构增加兼容字段。

### Phase 1：迁移运行记录

- 列出 execution audit 的实际字段和 payload 类型。
- 列出 strategy、account、portfolio 当前可持久化的记录。
- 将 `events.jsonl` 中仍属于运行事实的记录写入目标表。
- 将 `summary.json` 和 `metrics.json` 的必要字段转为派生查询或分析逻辑。
- 明确不再迁移 timeline 的展示结构本身。

### Phase 2：切换 canonical store

- 在 instance 目录初始化 `run.sqlite`。
- 将现有 execution audit 直接迁移到该数据库的 execution-owned 表。
- 为运行 metadata、portfolio observations 和 diagnostics 建立明确 owner。
- 删除旧的重复 JSONL 事实输出和旧的 timeline 写入路径。

### Phase 3：删除旧模型并统一查询

- 让 CLI 通过 application query 读取 run.sqlite。
- 用查询生成 summary、trade、equity 和 diagnostics 视图。
- 删除对 timeline JSONL 的直接依赖。
- 删除 `write_legacy_jsonl`、旧 timeline catalog 和相关兼容读取路径。
- 删除旧的 timeline server 和前端相关概念。

### Phase 4：快照与 Textual

- 快照只是查询结果，不增加新的事实存储。
- Textual 逐个浏览查询结果。
- Notebook、CLI、Textual 共享同一套 application query。

## 7. 收敛验收标准

- 对一次 instance，可以明确回答“事实存在哪里”。
- 一个订单不会同时由 SQLite 和 JSONL 维护两个状态源。
- `summary`、`metrics`、`trade` 和 `timeline` 都能说明自己的派生来源。
- 运行控制状态与研究事实不会混在一起。
- 各模块仍然拥有自己的写入和业务语义。
- CLI 和未来 Textual 不直接读取模块内部服务或任意文件。
- 删除某个派生导出文件不会丢失运行事实。
- 新增一个研究视图不需要新增一个长期存储系统。
