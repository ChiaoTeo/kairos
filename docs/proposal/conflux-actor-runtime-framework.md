# Conflux Actor Framework（已被替代）

本文原先描述的宏路由、HTTP-shaped handler、`TypeId` catalog、type-erased dispatch、动态
Contract registry 和 capability projection 已全部废弃，不再作为实现或兼容目标。

当前唯一目标设计见
[`conflux-closed-contract-runtime.md`](./conflux-closed-contract-runtime.md)。核心约束是：

- `ConfluxActor` 本身实现本服务唯一的 `Contract`，二者是同一个业务边界；
- Contract 第一版只抽象 REST `Request + Response` pair；
- Actor 只有一个闭合 `handle(ConfluxEvent) -> Option<RestResponse>` 入口；
- `ConfluxSystem` 是持有 concrete module clients、views、streams 与 provider-native
  connections 的实体，不是泛型或可擦除 registry；
- 资源按具体类型保存在 named lists 中，不使用 `Any`、`TypeId`、`dyn Connection` 或 provider
  operation enum；
- Integration connection 直接实现 Integration-owned `*Query`、`*Command` 和 `*Stream`
  traits，不建立 Principal/capability wrapper。
