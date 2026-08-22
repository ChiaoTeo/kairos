# Decision 0009：Account 观察事实与查询真实性

- Status: Accepted
- Date: 2026-08-22
- Scope: Account 查询视图、Integration 账户/费率能力、Account CLI

## Context

Account CLI 曾把 Integration provider 当作账户业务身份，把 live 当作账户类型，并把未执行的
open-orders 查询表示为空集合。余额、抵押品、交易仓位和理财持有也缺少清晰分类。真实账户的单一
registry `fee_rate` 无法表达按产品、symbol、maker/taker、税费和折扣变化的远端费率。

Account 已拥有 balances、collateral、positions、open orders 和 earn holdings；Integration 已拥有
Provider 认证与外部事实。问题应在现有所有权边界内解决，不引入第二套账户状态或 Portfolio 模块。

## Decision

Account 的一级业务身份是 `account_id`、alias 和 `broker`。`exchange` 与 `environment` 是账户配置，
`integration_provider` 只用于连接路由；禁止从 broker 推导 connector。JSON `provider` 仅保留一个兼容
周期作为 `integration_provider` 的弃用别名。

Account Application 为同包 CLI 组合 overview、assets、positions、earn holdings、open orders 和 fees
查询视图。balances 与 collateral 保持不同 fact role；普通 Spot/Funding 资产不转换成交易仓位。
跨业务调用者继续只读取 Account Contract 拥有的 current view。

配置事实与远端观察事实分开显示。canonical account model 保持 Account 既有枚举；供应商原生模型通过
`provider_account_model` 保留，Portfolio Margin Pro 映射为 canonical `PortfolioMargin`。Integration 的
`AccountProfileQuery` 通过产品专用账户信息端点观察形态；不得因经典 Futures 返回空集合而静默切换到
Portfolio Margin。

多 segment 查询返回 source、mode、观测时间、逐 segment outcome 和 overall completeness。只有
`complete` 的空集合能显示“没有”；partial、unsupported、unavailable 和 unauthorized 必须保留其差异。

Integration 拥有 provider-neutral `FeeQuery` 和 `ExternalFeeSchedule`。具体连接器映射供应商响应，Account
只将外部事实转换为 application DTO。真实费率按 product 和 symbol 查询；VIP tier 不可访问时单独标记
unavailable，不影响已观察费率。registry `fee_rate` 仅用于 paper/simulated，live 值被弃用且不视为远端
事实。

## Consequences

- Account CLI 以 broker/custodian、environment 和 effective access 为主要账户上下文。
- Account overview 同时展示 configured、observed、provider-native model 及一致性，并公开局部失败。
- Binance Simple Earn Flexible 与 Locked 都映射为 earn holdings；到期和流动性保持明确。
- Binance Spot、USD-M 与 COIN-M 连接器实现统一费率 capability；新增生产 Provider 可实现同一 owner
  capability，无需在 Account 新增 provider trait。
- standalone open-orders 调用 Integration `OrderQuery`；未查询或不支持永远不能伪装成零订单。
