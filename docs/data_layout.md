# 市场数据湖目录与时间规范

所有本地数据位于仓库根目录的 `data/`，采用五层模型：

```text
data/
├── source/       # 供应商原始响应和下载凭证
├── canonical/    # 统一市场事实模型
├── curated/      # 标准清洗、对齐、快照和跨市场数据产品
├── features/     # 可跨策略复用的派生特征
└── studies/      # 具体研究的参数、结果、图表和报告
```

## Source

路径使用显式键值分区，不依赖含糊的文件名：

```text
source/provider=binance/dataset=spot_klines/symbol=BTCUSDT/interval=1d/
  event_year=2025/event_month=01/{payload.zip,receipt.json}
```

`receipt.json` 记录 provider、dataset、URL、请求参数、下载时间、响应字节数、SHA-256，以及请求窗口。原始 payload 不做修改。

## Canonical

主干使用稳定的金融市场数据类型：`reference`、`market/trades`、`market/quotes`、`market/order_books`、`market/ohlcv`、`derivatives/option_quotes`、`analytics/vendor_greeks` 和 `analytics/vendor_volatility_indices`。

```text
canonical/market/ohlcv/asset_class=crypto/venue=binance/instrument=BTC-USDT/interval=1d/
canonical/analytics/vendor_volatility_indices/provider=deribit/underlying=BTC/index=DVOL/interval=1d/
```

每个数据集必须包含：

- `schema.json`：schema ID、字段、类型、单位、主键和时间语义；
- `lineage.json`：来源数据集、请求窗口、转换器和输入数据哈希；
- `coverage.json`：实际覆盖窗口、缺口、重复值和完整度；
- `quality.json`：验证检查、指标、是否通过以及质量等级依据；
- `manifest.json`：分区文件、行数、大小、文件哈希及整个数据集哈希；
- `capabilities.json`：point-in-time、盘口、成交和可支持的验证级别；
- `usage.json`：primary time、默认 view、维度和已知限制；
- `release.json`：Release、Schema、Transform、provider、venue、hash 和状态；
- `event_year=YYYY/event_month=MM/part-00000.parquet`：按事件时间分区、Zstandard 压缩的数据。

CSV 是迁移期兼容格式。新读取入口优先选择 Parquet；确认旧消费者已经迁移后，通过
`kairos data migrate-parquet --delete-csv` 删除经过行数校验的 CSV 副本。

## 时间契约

- 全部使用 UTC 和 ISO 8601；
- 所有窗口使用右开区间 `[start,end)`；
- Bar 必须包含 `period_start`、`period_end`、`event_time` 和 `available_time`；
- `event_time` 表示 Bar 完成时间，`available_time` 表示回测最早可见时间；
- CLI 的 `--end YYYY-MM-DD` 表示包含该自然日，lineage 中转换为次日零点的右开边界；
- `coverage.json` 分开记录请求窗口和实际观测窗口，缺失必须列出具体时间段。

## Features 与 Studies

`curated` 保存可跨策略复用的标准清洗、日历对齐、期权链快照和显式跨 venue 产品。
`features` 按经济含义而非策略命名，lineage 必须引用输入 Release ID 和内容哈希。`studies`
只保存某个假设专属的标签、样本切分、`study_spec.json`、数据快照、结果、报告和图表。

`data/` 默认被 Git 忽略。团队共享时应使用对象存储或数据版本工具；Git 只保存下载器、schema 生成逻辑、规范和小型测试 fixture。

## 代码所有权

- `kairos.connectors.*.historical` / `kairos.connectors.*.historical`：供应商历史数据下载；
- `kairos.data`：Dataset Catalog、Product/Release、查询、Feed、获取计划和元数据；
- `kairos.connectors.*.datasets` / `kairos.connectors.*.datasets`：Provider Connector，执行 Source 到 Canonical 发布；
- `kairos.features`：point-in-time safe 的跨策略特征，不允许保存未来标签；
- `studies/*`：源码研究工作区，生成未来标签、样本切分、假设检验和报告，只通过 Dataset ID 或逻辑名称读取受管数据。

## Catalog 与研究读取

`data/catalog/datasets.json` 是 Catalog V3 Registry。Logical Product、不可变 Release、Schema Version、
Transform Version、provider、venue、状态和 Alias 是独立字段；研究结果必须冻结 Release ID 和内容
哈希，不能只记录可变 Alias。

研究代码统一使用 `ResearchDataClient`：

```python
from kairos.data import DataView, OptionQuoteFields, OutputFormat, ResearchDataClient
from kairos.data.products import Datasets

data = ResearchDataClient("data")
quotes = data.get(
    Datasets.MARKET_EVENTS_OPTIONS_US_SPXW,
    start="2026-07-01T00:00:00Z",
    end="2026-07-02T00:00:00Z",
    event_types=("quote",),
    fields=OptionQuoteFields.TOP_OF_BOOK,
    view=DataView.RAW_AS_RECEIVED,
).collect(OutputFormat.PANDAS)
```

Arrow 是默认返回格式，也支持 `pandas`、`polars`、`rows`。表格研究使用 `get()`，事件回测使用
`replay()`，定频 MarketSnapshot 回测使用 `replay_snapshots()`，SQL 分析使用 `sql()`。

Massive Canonical Parquet 除保留完整 `payload_json` 外，还把 bid/ask/price/size/OHLCV、
交易 ID、交易所和条件等常用字段保存成物理列，使 Arrow 和 DuckDB 能执行列裁剪。

标准工作流：

```bash
python3 -m kairos data plan --dataset market.ohlcv.crypto.binance.btc-usdt.1d --provider binance --venue binance --start 2021-03-24T00:00:00+00:00 --end 2026-07-15T00:00:00+00:00
python3 -m kairos data acquire --dataset market.ohlcv.crypto.binance.btc-usdt.1d --provider binance --venue binance --start 2021-03-24T00:00:00+00:00 --end 2026-07-15T00:00:00+00:00
python3 -m kairos data acquire --dataset analytics.vendor_volatility_index.deribit.btc-dvol.1d --provider deribit --venue deribit --start 2021-03-24T00:00:00+00:00 --end 2026-07-15T00:00:00+00:00
python3 -m kairos data inspect --dataset market.ohlcv.crypto.binance.btc-usdt.1d
python3 -m kairos data catalog
python3 -m kairos data doctor
python3 -m kairos data plan --dataset market.ohlcv.crypto.binance.btc-usdt.1d --start 2025-01-01T00:00:00+00:00 --end 2025-02-01T00:00:00+00:00
python3 -m kairos data migrate-parquet --dry-run
python3 -m kairos features build --feature-set btc-iv-rv-v1
python3 -m studies.btc_options_vrp.study
```
