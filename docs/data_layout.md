# 市场数据湖目录与时间规范

所有本地数据位于仓库根目录的 `data/`，采用四层模型：

```text
data/
├── source/       # 供应商原始响应和下载凭证
├── canonical/    # 统一市场事实模型
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
- `manifest.json`：分区文件、行数、大小、文件哈希及整个数据集哈希；
- `event_year=YYYY/event_month=MM/part-00000.csv`：按事件时间分区的数据。

## 时间契约

- 全部使用 UTC 和 ISO 8601；
- 所有窗口使用右开区间 `[start,end)`；
- Bar 必须包含 `period_start`、`period_end`、`event_time` 和 `available_time`；
- `event_time` 表示 Bar 完成时间，`available_time` 表示回测最早可见时间；
- CLI 的 `--end YYYY-MM-DD` 表示包含该自然日，lineage 中转换为次日零点的右开边界；
- `coverage.json` 分开记录请求窗口和实际观测窗口，缺失必须列出具体时间段。

## Features 与 Studies

`features` 按经济含义而非策略命名，lineage 必须引用 canonical dataset ID 和内容哈希。`studies` 只保存某个假设专属的 `study_spec.json`、结果、报告和图表。

`data/` 默认被 Git 忽略。团队共享时应使用对象存储或数据版本工具；Git 只保存下载器、schema 生成逻辑、规范和小型测试 fixture。

## 代码所有权

- `trading.adapters.*.historical`：供应商历史数据下载；
- `trading.data`：Dataset Catalog、仓库、Canonical pipeline 和元数据；
- `trading.features`：point-in-time safe 的跨策略特征，不允许保存未来标签；
- `research/*`：生成未来标签、样本切分、假设检验和报告，只通过 Dataset ID 读取受管数据。

标准工作流：

```bash
python3 -m trading data prepare-btc-options --start 2021-03-24 --end 2026-07-14
python3 -m trading data inspect --dataset market.ohlcv.crypto.binance.btc-usdt.1d
python3 -m trading features build --feature-set btc-iv-rv-v1
python3 -m research.btc_options_vrp.study
```
