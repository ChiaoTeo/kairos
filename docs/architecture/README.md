# Architecture documentation

这里记录跨模块且当前生效的架构说明。业务模块自己的职责和实现边界应优先放在所属 crate 的
README；跨进程字段和编码规则应放在 `schemas/`。

- [Capital management boundary](capital-management.md)
- [CLI boundary](cli-boundary.md)
- [Kairos Workbench 产品设计](workbench-product-design.md)
- [Cargo 依赖管理规范](cargo-dependency-management.md)
- [Conflux JSON-RPC control boundary](conflux-jsonrpc-control.md)
- [查询、当前视图与派生数据命名](read-model-and-query-naming.md)

已经作出的重要架构选择及其理由记录在
[`../decisions/`](../decisions/README.md)，不在这里重复历史实施过程。
