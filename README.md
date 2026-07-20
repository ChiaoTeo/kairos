# Kairos

> A clean research-to-run toolkit for multi-asset trading systems.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Package](https://img.shields.io/badge/package-kairospy-111827?style=flat-square)
![Status](https://img.shields.io/badge/status-local%20deterministic%20ready-16A34A?style=flat-square)

Kairos 是一套面向量化研究、回测、模拟运行和交易编排的 Python 工具。

它把一个交易想法从 `Study` 逐步推进到 `Factor`、`Strategy`、`Run Artifact` 和可审计的账户事实。核心目标不是“快速下单”，而是让研究输入、策略版本、执行过程和结果证据都可以被复现、检查和回放。

第一次使用建议从本地确定性流程开始，不需要账户、不联网、不会真实下单。

## ✨ 项目介绍

Kairos 关注四件事：

- 🧪 **研究可复现**：用冻结的数据 release、时间语义和 artifact 记录每次研究。
- 📈 **回测可审计**：策略运行会产出带 hash 的 Run Artifact，方便检查、比较和 replay。
- 🧭 **边界清晰**：策略只表达经济意图，执行、风控、账户和账本由独立模块处理。
- 🛡️ **运行谨慎**：Paper/Testnet/Live 需要经过 readiness、soak、reconciliation 和 kill switch 证据链。

当前本地确定性生命周期已经可用：

```text
Study -> Factor -> Strategy -> Backtest -> Simulation -> Shadow -> Paper Fixture -> Replay -> Audit
```

## 🚀 核心功能

- 📦 **项目初始化**：`kairos init` 在任意空目录创建自己的 Kairos 项目。
- 🔍 **数据治理**：管理 Data Product、Dataset Release、质量等级、别名和数据审计。
- 🧠 **Study / Factor**：从假设、数据绑定、特征计算到可冻结的研究证据。
- ⚙️ **Strategy Release**：注册、检查、晋级和回滚可运行策略版本。
- 🧪 **Backtest / Simulation**：在冻结数据和 replay 时钟上进行确定性验证。
- 👻 **Shadow Run**：计算完整决策和假设 Intent，但不提交订单。
- 🧾 **Ledger / Portfolio**：用不可变账本事实派生持仓、现金、费用和风险视图。
- 🔌 **Connector 边界**：支持模拟环境，并规划 Binance、IBKR、Massive 等外部接入。

## ⚡ 使用简介

安装：

```bash
python3 -m pip install kairospy
python3 -m pip install 'kairospy[data,massive]'
```

创建一个自己的项目：

```bash
mkdir my-kairos-project
cd my-kairos-project
kairos init
kairos project status
kairos doctor
python studies/starter.py
```

配置外部 provider：

```bash
export MASSIVE_API_KEY='...'
export BINANCE_TESTNET_API_KEY='...'
export BINANCE_TESTNET_API_SECRET='...'

kairos configure massive
kairos configure binance --environment testnet
```

直接运行 `kairos configure` 会进入交互式向导；`kairos config show/path/set/unset/validate` 是底层 TOML 配置入口。CLI 默认输出面向人类的专业表格，脚本和 CI 使用稳定 JSON：

```bash
kairos doctor
kairos --format json doctor
```

可选安装富文本和交互增强：

```bash
python3 -m pip install 'kairospy[cli]'
```

如果你从源码开发：

```bash
python3 -m venv pyenv
./pyenv/bin/pip install -e '.[data,query,notebook]'
./pyenv/bin/kairos --help
./pyenv/bin/kairos tutorial sma
```

运行一个无凭据、无下单的 SMA 教程：

```bash
kairos tutorial sma
```

运行统一回测入口：

```bash
kairos run backtest --strategy sma-cross-v1 --fixture --fast 5 --slow 15
```

Python API 示例：

```python
from kairos import Kairos

result = Kairos().backtest(
    strategy="sma-cross-v1",
    dataset="fixture:sma-bars-v1",
    parameters={"fast": 5, "slow": 15},
)

print(result.summary())
```

## 📚 其他

- 新手教程：[docs/tutorial_first_study.md](docs/tutorial_first_study.md)
- 当前状态：[docs/current_product_status.md](docs/current_product_status.md)
- 系统架构：[docs/architecture.md](docs/architecture.md)
- 数据指南：[docs/study_data_guide.md](docs/study_data_guide.md)
- PyPI 发布：[docs/release_pypi.md](docs/release_pypi.md)

> ⚠️ Fixture 和 synthetic backtest 只能证明机制可运行，不能证明策略有效，也不能替代 Paper/Testnet/Live 的外部验收。

## 🌟 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=ChiaoTeo/kairos&type=Date)](https://www.star-history.com/#ChiaoTeo/kairos&Date)
