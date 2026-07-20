# 研究数据使用指南

研究代码只依赖 `DatasetClient` 和类型化 Dataset Product。不要拼接 `data/` 物理路径，也
不要直接读取 Source 响应。物理目录存在不代表数据已经注册、验证或获准研究使用。

## 发现数据

```python
from kairospy.data import DatasetClient

data = DatasetClient("data")
products = data.search(asset_class="option", underlying="SPX")
for product in products:
    print(data.describe(product))
    print(data.coverage(product))
```

`describe()` 同时展示结构化维度、primary time、候选来源、当前 Release、质量等级和所有历史
Release。用户不需要先记住完整 Logical Key。

## 表格探索

```python
from kairospy.data import DataView, OptionQuoteFields, OutputFormat, DatasetClient
from kairospy.data.products import Datasets

data = DatasetClient("data")
frame = data.get(
    Datasets.MARKET_EVENTS_OPTIONS_US_SPXW,
    start="2026-07-15T13:30:00Z",
    end="2026-07-15T20:00:00Z",
    event_types=("quote",),
    fields=OptionQuoteFields.TOP_OF_BOOK,
    view=DataView.RAW_AS_RECEIVED,
).collect(OutputFormat.POLARS)
```

`get()` 返回惰性 `DataQuery`。`collect()` 支持 Arrow、Polars、Pandas 和 rows；`stream()` 返回
有界 RecordBatch；`explain()` 展示冻结 Release、时间字段、过滤条件及是否进行下推。

## 稳定事件回放

```python
feed = data.replay(
    Datasets.MARKET_EVENTS_OPTIONS_US_SPXW,
    start=start,
    end=end,
    event_types=("quote", "trade"),
    view=DataView.RAW_AS_RECEIVED,
)

for event in feed:
    ...
```

## MarketSnapshot 回测

```python
feed = data.replay_snapshots(Datasets.CURATED_MARKET_SNAPSHOTS_OPTIONS_US_SPXW)

for market in feed.between(start, end):
    ...
```

`replay()` 和 `replay_snapshots()` 在构造时冻结 Release ID 与内容哈希，之后不再解析 Alias、访问
Catalog、联网或读取 wall clock。`RunMode.BACKTEST` 强制禁止数据获取。

## 本地缺失数据

先生成计划，不联网：

```python
plan = data.plan(product, start=start, end=end)
print(plan.missing)
print(plan.candidates)
print(plan.selected)
```

明确允许后补齐缺失分区：

```python
from kairospy.data import AcquirePolicy

frame = data.get(
    product,
    start=start,
    end=end,
    acquire=AcquirePolicy.IF_MISSING,
).collect(OutputFormat.POLARS)
```

Connector 必须执行：

```text
Source → Canonical → Quality Gate → immutable Release → Catalog → Query
```

新数据与旧 Release 按主键合并后发布完整新快照，不覆盖旧 Release，也不会用另一个 venue
静默填补缺口。只提供当前快照的供应商不能伪装成历史回补能力。

Massive 的 universe 必须由配置显式给出，平台不会猜测合约或扩大下载范围。设置
`MASSIVE_API_KEY` 后，先 plan 再 acquire：

```bash
kairospy data plan --connector-config examples/data/massive_connector.example.json \
  --dataset market.events.options.us.spxw --provider massive --venue opra \
  --start 2026-07-17T13:30:00+00:00 --end 2026-07-17T20:00:00+00:00
kairospy data acquire --connector-config examples/data/massive_connector.example.json \
  --dataset market.events.options.us.spxw --provider massive --venue opra \
  --start 2026-07-17T13:30:00+00:00 --end 2026-07-17T20:00:00+00:00
```

示例 ticker 必须替换为研究批准的 point-in-time universe。缺少配置时 Connector 不可用；缺少
凭证时命令在网络请求前失败。

比较两个不可变 Release：

```bash
kairospy data compare --first ds_old --second ds_new
```

输出分别比较身份、Schema、Coverage、Quality 和 Lineage。

## 时间规则

- 时间必须带时区，内部统一为 UTC；
- 查询窗口固定为 `[start,end)`；
- Dataset 必须声明 primary time，读取层不会猜测时间字段；
- 研究与回放默认使用 `available_time`；
- `raw-as-received` 用于真实回放；`corrected-final` 只能显式选择；
- Feature 必须 point-in-time safe；未来收益、标签和样本切分只能存在于 Study。

## 名称、Release 与 Alias

Logical Key 描述经济事实；Release ID 标识不可变内容；Schema Version 和 Transform Version 分开；
Alias 只用于交互探索。正式报告至少记录：

- Logical Key；
- Release ID 和内容 SHA-256；
- provider 和 venue；
- Schema/Transform Version；
- source selection policy；
- coverage、quality level、view 和查询窗口；
- 代码与依赖版本。

未注册目录、`registered`、`validated` 或 `quarantined` Release 都不能被研究客户端读取，只有明确
批准的 Release 可以进入研究。

## 唯一公共入口

研究只使用 `get()`、`iter_rows()`、`replay()` 和 `replay_snapshots()`；底层 Repository 仅供 Connector
和存储实现使用。旧的 `load()`、`scan_events()` 与重复历史数据加载入口已经删除，避免用户判断多套同义接口。

## 环境与迁移

```bash
pip install -e '.[query,notebook]'
kairospy data catalog
kairospy data migrate-parquet --dry-run
kairospy data migrate-parquet
```

默认迁移保留 CSV。所有旧消费者迁移并验证后，使用 `--delete-csv` 删除已通过行数校验的兼容
副本。Source 层永远不参与格式迁移。
