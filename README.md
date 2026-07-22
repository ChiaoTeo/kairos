# Kairos

> A clean data-workspace-to-run toolkit for multi-asset trading systems.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Package](https://img.shields.io/badge/package-kairospy-111827?style=flat-square)
![Status](https://img.shields.io/badge/status-local%20deterministic%20ready-16A34A?style=flat-square)

Kairos 是一套面向量化数据准备、研究代码、策略运行和交易编排的 Python 工具。

它把数据准备、用户代码、运行快照和账户事实分开管理。核心目标不是“快速下单”，而是让数据输入、策略入口、执行过程和结果证据都可以被复现、检查和回放。

第一次使用建议从本地确定性流程开始，不需要账户、不联网、不会真实下单。

## ✨ 项目介绍

Kairos 关注四件事：

- 🧪 **研究可复现**：用冻结的数据 release、时间语义和 artifact 记录每次研究。
- 📈 **回测可审计**：策略运行会产出带 hash 的 Run Artifact，方便检查、比较和 replay。
- 🧭 **边界清晰**：策略只表达经济意图，执行、风控、账户和账本由独立模块处理。
- 🛡️ **运行谨慎**：Paper/Testnet/Live 需要经过 readiness、soak、reconciliation 和 kill switch 证据链。

当前本地确定性生命周期已经可用：

```text
Data -> Workspace -> Code -> Run -> Replay -> Audit
```

## 🚀 核心功能

- 📦 **项目初始化**：`kairospy init` 在任意空目录创建自己的 Kairos 项目。
- 🔍 **数据治理**：管理 Data Product、Dataset Release、质量等级、别名和数据审计。
- 🗂️ **Workspace**：一个工作区绑定一组数据，研究代码和策略代码都复用同一份数据视图。
- ⚙️ **Strategy Protocol**：策略是用户代码入口，不是 Kairos 内置工作区。
- 🧪 **Run**：用 Workspace snapshot 和 strategy entrypoint 生成可审计运行记录。
- 👻 **Shadow Run**：计算完整决策和假设 Intent，但不提交订单。
- 🧾 **Ledger / Portfolio**：用不可变账本事实派生持仓、现金、费用和风险视图。
- 🔌 **Connector 边界**：支持模拟环境，并规划 Binance、IBKR、Massive 等外部接入。

## ⚡ 使用简介

普通用户不需要复制本仓库。安装包只包含 Kairos 产品库和 CLI；研究代码、因子代码、策略代码由用户放在自己的项目目录中。

安装：

```bash
python3 -m pip install kairospy
python3 -m pip install 'kairospy[data,massive]'
```

创建一个自己的项目：

```bash
mkdir my-kairospy-project
cd my-kairospy-project
kairospy init
kairospy project status
kairospy doctor
kairospy workspace create alpha
```

也可以用交互式初始化：

```bash
kairospy init --interactive
```

配置外部 provider：

```bash
export KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY='...'
export KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY='...'
export KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET='...'

kairospy configure massive
kairospy configure binance --environment testnet
```

直接运行 `kairospy configure` 会进入交互式向导；`kairospy config show/path/set/unset/validate` 是底层 TOML 配置入口。`kairospy doctor` 会输出状态表和可执行的 Next Steps。CLI 默认输出面向人类的专业表格，脚本和 CI 使用稳定 JSON：

```bash
kairospy doctor
kairospy --format json doctor
```

统一下载 governed Data Product：

```bash
kairospy data acquire
```

该命令会交互式选择可下载的数据集、时间窗口和 full-market/指定 instruments，并先展示统一 acquisition plan。底层 provider 可以不同：Binance USD-M 永续小时线使用 public archive zip，Massive 美股小时线使用 REST paginated aggregates；CLI 会统一显示 `task_type`、`total_tasks`、`cached_tasks`、`uncached_tasks` 和 `resume_supported`。

先 dry-run，再正式下载：

```bash
kairospy data acquire \
  --dataset market.ohlcv.equity.us.massive.1h.adjusted \
  --start 2026-01-02T14:30:00+00:00 \
  --end 2026-01-02T16:30:00+00:00 \
  --provider massive \
  --venue us-securities \
  --instrument equity:us:AAPL \
  --max-requests 10 \
  --dry-run

kairospy data acquire \
  --dataset market.ohlcv.equity.us.massive.1h.adjusted \
  --start 2026-01-02T14:30:00+00:00 \
  --end 2026-01-02T16:30:00+00:00 \
  --provider massive \
  --venue us-securities \
  --instrument equity:us:AAPL \
  --max-requests 10 \
  --yes
```

Massive 期权小时 OHLCV 不传 `--instrument` 时会下载 OPRA minute aggregates Flat Files 并本地聚合成全市场 1h bars：

```bash
kairospy data acquire \
  --dataset market.ohlcv.option.us.massive.1h.raw \
  --start 2026-01-02T14:30:00+00:00 \
  --end 2026-01-02T21:00:00+00:00 \
  --provider massive \
  --venue opra \
  --max-requests 10 \
  --yes
```

如果只需要少量显式 OPRA option tickers，可以加 `--instrument`，此时使用 Massive REST hourly aggregates：

```bash
kairospy data acquire \
  --dataset market.ohlcv.option.us.massive.1h.raw \
  --start 2026-01-02T14:30:00+00:00 \
  --end 2026-01-02T21:00:00+00:00 \
  --provider massive \
  --venue opra \
  --instrument O:NVDA260130C00100000 \
  --instrument O:NVDA260130P00100000 \
  --max-requests 10 \
  --yes
```

项目根目录的 `.env` 会自动加载，并且不会覆盖 shell 中已经存在的环境变量。全市场下载建议先使用 `--dry-run` 和 `--max-requests/--max-instruments` 控制范围。

可选安装富文本和交互增强：

```bash
python3 -m pip install 'kairospy[cli]'
```

如果你是从源码参与开发，使用 uv 同步 editable 开发环境：

```bash
uv sync --extra data --extra query --extra notebook --extra cli --extra massive
uv run kairospy --help
uv run kairospy tutorial sma
uv run pytest
```

静态命名和打包边界检查：

```bash
./scripts/check_naming_static.sh
```

运行一个无凭据、无下单的 SMA 教程：

```bash
kairospy tutorial sma
```

运行统一回测入口：

```bash
kairospy run backtest --strategy sma-cross-v1 --fixture --fast 5 --slow 15
kairospy run backtest --strategy sma-cross-v1 --fixture --fast 5 --slow 15 --control
```

`--control` 会显示 Run Control 控制台和 Run Summary，包含 pipeline、metrics、artifact、audit hash 和可执行的下一步命令；`--format json` 仍只输出稳定 JSON。

Python API 示例：

```python
from kairospy import Workspace

workspace = Workspace.open_or_create("alpha")
workspace.data.bind("bars", dataset="market.equity.us.ohlcv.1d")

# Strategy code is ordinary user code. Run it with:
# kairospy run start --config configs/runs/backtest.example.toml
```

## 📚 其他

- 工作区设计：[docs/workspace_experience_consolidation.md](docs/workspace_experience_consolidation.md)
- 当前状态：[docs/current_product_status.md](docs/current_product_status.md)
- 系统架构：[docs/architecture.md](docs/architecture.md)
- 数据接入：[docs/connector_data_integration_usage.md](docs/connector_data_integration_usage.md)
- PyPI 发布：[docs/release_pypi.md](docs/release_pypi.md)

> ⚠️ Fixture 和 synthetic backtest 只能证明机制可运行，不能证明策略有效，也不能替代 Paper/Testnet/Live 的外部验收。

## 🌟 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ChiaoTeo/kairospy&type=Date)](https://www.star-history.com/#ChiaoTeo/kairospy&Date)
