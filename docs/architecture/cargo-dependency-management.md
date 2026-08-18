# Cargo 依赖管理规范

文档类型：当前架构规范。可执行约束同时记录在仓库根目录的 `AGENTS.md`。

本仓库以根目录 `Cargo.toml` 作为 Rust workspace 依赖的唯一版本与来源清单。
集中管理不改变模块边界：每个 crate 仍必须在自己的清单中显式声明实际使用的依赖。

## 根清单负责什么

`[workspace.package]` 统一维护所有成员共享的：

- `version`
- `edition`
- `license`

`[workspace.dependencies]` 统一维护：

- 第三方直接依赖的版本和来源；
- workspace 内部 crate 的路径；
- 全部调用方都需要的基础 feature；
- 应在整个 workspace 禁用的 default feature。

根清单中的条目只是可继承的依赖目录，不会自动给任何 crate 增加依赖边。

## 成员清单负责什么

成员必须通过 `workspace = true` 引用依赖：

```toml
[dependencies]
serde.workspace = true
kairos-primitives.workspace = true

tokio = {
    workspace = true,
    features = ["macros", "rt-multi-thread", "sync"],
}
```

成员清单可以声明：

- 该 crate 专属的附加 feature；
- `optional = true`；
- 依赖所属的 `dependencies`、`dev-dependencies`、`build-dependencies` 或
  target-specific dependency section。

成员清单不得重复声明 `version`、`path`、`git` 或 registry 来源。

## Feature 选择

Feature 应遵循最小能力原则：

- 所有调用方必需的 feature 放在根清单；
- 只有部分调用方需要的 feature 留在成员清单；
- `blocking`、数据库 migration/macro、多线程 runtime、provider-specific
  transport 等能力不得为了书写方便而在根清单全局开启。

例如，根清单固定 `reqwest` 的版本、关闭默认 feature，并启用所有当前调用方都需要的
JSON 与查询参数能力：

```toml
reqwest = {
    version = "0.13.4",
    default-features = false,
    features = ["json", "query"],
}
```

同步控制客户端按需开启 `blocking`，Integration 的异步 HTTP 路径则不启用它。

## 新增或升级依赖

1. 在根 `[workspace.dependencies]` 增加或修改版本、来源和公共 feature。
2. 在实际使用它的成员清单中以 `workspace = true` 引用，并添加局部 feature。
3. 如果新增 workspace crate，同时把其 package path 加入根依赖目录。
4. 更新并审阅 `Cargo.lock`；区分直接依赖变化与第三方传递依赖变化。
5. 运行：

```bash
python3 scripts/check/check_workspace_dependencies.py
cargo check --workspace --all-targets --all-features
cargo test --workspace
cargo fmt --all -- --check
git diff --check
```

## 传递依赖

`[workspace.dependencies]` 只治理本仓库的直接依赖，不能强制第三方 SDK 使用同一版本
的传递依赖。`cargo tree --workspace --duplicates` 中由上游 SDK 引入的多版本依赖，应
优先通过升级或替换上游解决。没有兼容性验证时，不使用 `[patch]` 强行压平依赖树。

CI 会运行 `scripts/check/check_workspace_dependencies.py`，阻止成员重新引入独立版本、
相对 path 或未登记的直接依赖。
