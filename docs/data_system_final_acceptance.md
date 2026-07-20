# 数据系统最终验收报告

日期：2026-07-17  
范围：数据平台、研究数据访问、冻结输入和策略回测数据链路  
结论：通过

## 1. 验收结论

当前数据系统已经可以在完全离线、无交易所 API Key 的条件下支持：

- 数据产品发现和说明；
- 不可变 Release 查询；
- Study 输入冻结；
- MarketSnapshot 回放；
- 正式研究输入治理；
- Q3/Q4 数据门禁；
- 确定性策略回测；
- Artifact 输入、质量和 content hash 审计。

API Key 仅用于未来新增或刷新外部数据，不是消费现有冻结 Release、执行研究或运行回测的前置条件。

## 2. Catalog 与产品模型

```text
Products: 16
Releases: 27
Aliases: 4
Errors: 0
Warnings: 0
Q0: 3 quarantined
Q1: 0
Q2: 19
Q3: 5
Q4: 0
```

验收结果：

- DataProductContract 是统一产品定义；
- Product、Release、Alias、Schema、Transform 和物理位置身份分离；
- Release 显式声明 storage kind 和 layout version；
- Q0 Release 被隔离，正式研究/回测不能消费；
- Catalog strict health 通过。

## 3. 数据质量

已实现并测试的 typed Quality Profile：

- OHLCV；
- Quote；
- Trade；
- Market Event；
- Option Snapshot；
- Feature；
- Reference；
- MarketSnapshot。

Trade 和 Market Event 大数据检查使用 DuckDB 流式聚合，不需要把全量数据装入内存。

## 4. 用户路径验收

以下路径已实际执行成功：

```text
data search       -> 返回 4 个 crypto 匹配产品
data describe     -> 解析 BTC-USDT 1d 产品和选中 Release
data query        -> 返回受治理 Release 的 3 行样例
data freeze       -> 冻结 2 个 Release 到同一个 Study Input Snapshot
backtest sma      -> 读取 1,925 条 Q3 Bar 并生成确定性 Artifact
spxw-reference-scenario -> 回放 4 个 Q3 MarketSnapshot 并生成 conservative/stress Artifact
audit-artifact    -> 核验 Release ID、content hash、Q3/Q4 和批准状态
```

## 5. Golden 证据

### BTC-USDT 1d SMA

```text
Release: market.ohlcv.crypto.binance.btc-usdt.1d
Quality: Q3 / approved_for_backtest
Bars: 1,925
Backtest audit: 0594df273f18e113f9658ab0dcca5c77a4f135314b34acc8f7a60011fd1efa90
Input audit: 009bcb95771ca3485396cfe2e5359c8cd1ada73c9e116758470738fd07627687
```

### SPXW MarketSnapshot Golden

```text
Consumed Release: ds_c5cd07c6a542b4af665709b6
Content hash: c5cd07c6a542b4af665709b6581b7417a66f3171dc6292f312a352f650921460
Quality: Q3 / approved_for_backtest
Slices: 4
Pipeline audit: 94bc5133a7b5822c3f354cb32c6988b9e53d5e07ac0972d3d97b1033309c194d
Input audit: dc542279b2aa4c64d66cdeb3d4d2c00440d3ef93286451652b4a255f5dab73ef
```

## 6. 自动化验收

```text
Data/study/backtest focused tests: 92 passed
Repository full tests: 331 passed, 3 external integration tests skipped
Catalog strict health: passed
compileall: passed
git diff --check: passed
```

静态扫描确认：

- Domain 无 Data/Catalog/Storage/Study/Backtest 反向依赖；
- DatasetRepository、StudySnapshotCollectionStore、SurfaceRepository 不再用于运行路径；
- `data/history`、`data/datasets`、`data/surfaces` 旧目录不存在；
- CSV 不再作为正式数据事实源。

## 7. 使用边界

以下事项不影响数据平台验收通过：

- 没有 Binance 或 IBKR API Key；
- 没有 Q4 Production Release；
- 没有完成 Live/Paper Runtime 验收。

以下事项需要外部数据后才能扩展：

- 获取新的 Binance、IBKR、Massive 或其他 Provider 数据；
- 自动增量刷新和在线 SLA；
- SPXW 策略统计有效性。

当前 SPXW 数据只有 1 个交易日、4 个一分钟切片，因此只能证明数据治理、回放和回测机制，不能证明策略统计有效性。该限制已显式保留，不影响现有数据产品用于其他研究和确定性回测。

## 8. 最终判定

数据系统收敛、领域边界和数据产品化改造已完成数据范围验收。当前产品可以使用本地冻结 Release 进行研究和策略回测，不需要交易所 API Key。
