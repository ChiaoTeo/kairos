# Conflux 由 System composition 所有

- Status: Accepted
- Date: 2026-08-30

## Context

Conflux 提供闭合、强类型、单写者的进程运行时。它的 `ConfluxSystem` 同时持有六个业务
Owner Contract 的 client/event stream，以及当前支持的 Integration provider connection
集合。这个闭合集合使编译器能够检查完整资源宇宙，但也意味着 Conflux 了解业务模块清单，
不符合 `crates/platform` 必须业务中立的定义。

把这些业务类型擦除为开放 registry，或为每种资源建立通用 JSON envelope，会丢失已有的
编译期完备性和类型安全。当前也没有多个真实运行时实现证明需要抽象出新的 port。

## Decision

Conflux 归 Kairos System composition 所有，并从 `crates/platform/conflux` 迁移到
`crates/system/conflux`，包名仍为 `kairos-conflux`。System composition 可以装配各 Owner
Contract、Integration connection、transport、indexed view 和 workspace resource，但不得
绕过 Contract 调用业务 main crate 的 Application，也不得拥有业务状态或业务规则。

`crates/platform` 只保留 transport、protocol、indexed-view、integration、workspace 等
业务中立能力。业务模块继续通过自己的 composition 声明具体资源，并通过 Conflux 的类型化
进程上下文使用它们；业务行为仍由该模块的 Application、Services 和 Domain 所有。

## Consequences

- Conflux 对全部业务 Contract 和 provider 的依赖现在与其 System ownership 一致，不再伪装
  成业务中立 platform capability。
- 新业务模块或 provider 会显式修改 System 的闭合资源宇宙，保持编译期可审计性。
- 平台 crate 不得重新引入业务 Contract inventory。
- 本决策不授权业务模块互相依赖 main crate，也不创建开放 registry、类型擦除或第二套 facade。
