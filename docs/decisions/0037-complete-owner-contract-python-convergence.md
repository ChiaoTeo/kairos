# Decision 0037：Owner Contract Python 全量收敛

- Status: Accepted
- Date: 2026-08-27
- Supersedes: [Decision 0036](0036-owner-contract-python-bindings.md)
- Scope: Account、Capital、Execution、Market、Risk；明确不包含 Reference

## Context

Account、Capital、Execution、Market、Risk 已经在各自可独立依赖的 Rust contract crate 中拥有
跨进程 command、query、event、current view、业务错误和传输语义。每个 owner 也已经在
`crates/modules/<owner>/contract/py` 下拥有一个 Pyo3 companion，但当前 companion 主要覆盖
indexed current-view 读取。

`kairospy.infrastructure.contracts` 仍在生产路径中实现同一 contract 的其他部分，包括手写
JSON-RPC method 名称和无类型 request mapping、Python FlatBuffers root 选择和事件解码、重复的
event/current-view record、enum 和 decimal 转换、owner resource path，以及从 native result
重新构造 Python result。即使 Rust 与 Python 访问同一进程和 wire schema，这两套实现仍可能漂移。

Contract test 可以在漂移发生后发现部分问题，但无法让两套生产实现保持强一致。强一致要求每项
contract 语义只有一个可执行实现，Python 必须进入该实现，而不是复刻它。

Reference 不属于本 Decision。其 SQLite catalog reader 和 Python contract 迁移需要单独处理
ownership 和兼容性。

## Decision

对于 Account、Capital、Execution、Market、Risk，owner Rust contract 及其 Pyo3 companion 是
完整 owner contract 面向 Python 的唯一实现。这里的“完整”包括 control、event、current view、
contract-owned type、validation、error、identity、resource resolution 和声明的 transport
semantics。

五个 companion 继续保持为相互独立的 Cargo package：

```text
crates/modules/account/contract/py
crates/modules/capital/contract/py
crates/modules/execution/contract/py
crates/modules/market/contract/py
crates/modules/risk/contract/py
```

它们分别生成名为 `kairospy._native_<owner>_contract` 的私有 ABI3 extension。Extension 是私有的
打包细节，但其暴露的 value 是 canonical runtime Python contract type。普通调用方通过
`kairospy.infrastructure.contracts.<owner>` 的稳定公开名称使用这些 type，不直接导入私有 native
module。

Companion 不是第二套 business contract。它可以拥有 Pyo3 lifecycle、GIL release、exception
构造以及向 owned Python-visible object 的转换，但所有业务含义和 wire interpretation 必须来自
其依赖的 owner contract。Companion 不得重新声明 RPC method 名称、独立解码 owner FlatBuffer，
也不得接受 owner contract 会拒绝的 semantic value。

### Unified owner client

每个 companion 暴露一个与现有 Rust owner client 对应的 owner-named client：

```text
AccountClient
CapitalClient
ExecutionClient
MarketClient
RiskClient
```

Python-visible client 使用与 Rust client 相同的 endpoint facts 构造：control socket、可选 view
root、可选 Aeron endpoint，以及所需的 instance identity。它暴露相同的三类 capability：

```text
client.control
client.events
client.current
```

可以为 lifecycle 和 typing 暴露 capability-specific Python class，但这些 class 必须包装对应的
owner-contract capability，不得建立另一套 connection model。

### Control

Owner contract 的 `conflux_rpc` service definition 是每个 RPC method、parameter list、request
type、response type 和 business error 的唯一声明。

Pyo3 control method 调用由该 service definition 生成的 client。它不调用 generic Python
JSON-RPC client，也不包含 `execution_submit_intent`、`risk_authorize_and_reserve` 等字符串字面量。

Python-visible request 和 response 是 owner-contract Rust value 的 immutable wrapper。Constructor
使用既有 Rust primitive 和 contract constructor 转换字符串与固定宽度数字。Core request 和
response 不接受或返回 `Mapping[str, object]`、`dict[str, Any]`、`serde_json::Value` 或开放式
command envelope。

现有 Python caller 是同步的，因此 contract-generated client 从同一份 `conflux_rpc` 声明生成
synchronous facade，由生成代码拥有 RPC method 名称和 typed serialization。Pyo3 不得通过重复
method 字符串或构造 raw JSON-RPC envelope 恢复同步调用能力。

Transport-level JSON 只是 contract-owned JSON-RPC client 的实现细节，不成为 Python business
DTO。

### Events

Owner event bytes 只由 owner Rust contract 解码。每个 companion 暴露 owner event stream，并为
测试、fixture 和已经完成 framing 的 transport integration 提供显式 `decode_event(bytes)` 入口。

两个入口返回相同的 owned、immutable Python-visible event type。Event kind 选择、metadata
validation、FlatBuffer verification、identity check、enum 转换、decimal 转换和 optional-field
semantics 都在 Rust 执行。Borrowed FlatBuffer root 和 frame buffer 不跨越 Pyo3。

业务 Python 代码不选择 generated FlatBuffers root module，也不导入
`kairospy.infrastructure.protocol.generated` 解释 owner event。它也不拥有 owner stream ID、最大
payload size 或 channel validation；这些 facts 来自 owner contract。

通用 Aeron worker、queue、timeout 和 GIL-release mechanics 可以保留为 platform capability。如果
五个 companion 共享该 capability，它只能暴露 Rust transport mechanics，不得拥有 business
stream identity、event vocabulary 或 decoded result type。

### Current views

Decision 0036 建立的 current-view hard cut 继续有效，并纳入本 Decision。Owner Rust contract 是
environment-path construction、database/key selection、schema verification、metadata/readiness
check、key/value identity validation、FlatBuffer decoding 和 typed result construction 的唯一
实现。

Python 不为诊断目的保留重复的 owner path 或 database constant。调用方需要展示路径时，由 native
view 通过只读 property 暴露已经解析的路径。

Python caller 直接消费 immutable native current-view record。Contract infrastructure 不把它们
再次转换成另一棵 dictionary 或 dataclass。CLI JSON、log、table 或 application-owned domain
vocabulary 所需的转换发生在消费方 Application 或 presentation boundary，并按消费方职责命名。

### Contract types and errors

由 `kairospy.infrastructure.contracts.<owner>` 重导出的 native class，是 owner command、query、
event、current value、result 和 error 的 canonical runtime Python representation。`kairospy` 不再
定义可独立构造的平行 contract dataclass 或 enum。

Native class 的 Python type information 从 binding surface 生成或进行机械校验。除非测试会将
`.pyi` 的公开 class、method、property 和 signature 与加载后的 extension 比对，否则手写 `.pyi`
不能作为一致性证据。

每个 extension 的 build information 至少包含：

- owner name；
- Python contract ABI version；
- package version；
- owner contract 或 schema fingerprint。

公开 facade 在 import 时对不兼容的 owner、ABI version 或 fingerprint fail closed，不存在 Python
fallback。

Native exception 保留稳定的 owner-contract error code，并区分 invalid input、transport
unavailable、current view unavailable/stale、invalid wire data 和 rejected business operation。
Python 不通过 message text 推断 owner error。

### Allowed Python responsibilities

收敛完成后，`kairospy` 仍可以拥有：

- Application 和 Strategy orchestration；
- composition 和 lifecycle coordination；
- CLI、Workbench、JSON presentation 和 redaction；
- consumer-owned domain model 和 calculation；
- 用于缩窄 Application dependency 的 Python protocol；
- native owner capability 外层纯语法性质的 context manager 或 iterator。

这些职责可以把 owner contract result 适配成 consumer-owned model，但不得把该 model 作为第二套
owner contract 发布，也不得重复 owner validation 和 wire semantics。

`kairospy.infrastructure.contracts.<owner>` 仅保留稳定公开 re-export 和纯语法 facade，不再是另一层
实现。

### Prohibited Python production paths

对于本 Decision 范围内的五个 owner，`kairospy` 生产代码不得包含：

- 手写 owner JSON-RPC method 名称或 request envelope；
- 基于 `UnixJsonRpcClient` 的 owner control client；
- owner FlatBuffers event decoding 或 generated-root selection；
- owner request、response、event 或 current-view DTO 的可构造重复定义；
- owner enum、decimal、identity、readiness 或 error-code conversion table；
- owner current-view environment path、database name、key format 或 schema set；
- 从 native result 重建 contract result dictionary/dataclass；
- 在公开 owner facade 和 binding smoke test 之外导入私有 native module；
- conditional import 或运行时回退到 Python 实现。

Test fake 可以实现 application-owned narrow protocol，但不得重新定义完整 owner contract，也不得被
选作生产 fallback。

## Migration

迁移按连续 hard cut 完成。已经迁移的 capability 不保留两条 runtime path。

1. 保持已经完成的 owner-native current-view path，移除残留的 Python path 和 result duplication。
2. 为五个 companion 增加 owned event type、native event decoding 和 owner event stream。迁移 caller
   后删除 Python generated-root decoding、event record 和 business event source implementation。
3. 从每个现有 `conflux_rpc` service definition 生成同步 typed client facade，增加 native control
   request、response 和 error wrapper，并迁移所有 Python caller。
4. 删除五个 owner 的 Python `control.py`、`client.py`、raw request mapping、RPC method string 和重复
   contract DTO。
5. 将每个 `kairospy.infrastructure.contracts.<owner>` package 缩减为 re-export 和明确允许的纯语法
   facade。
6. 删除已经没有 Python boundary 使用的 dependency 和 generated Python protocol artifact。

工作可以按 owner 或 capability 分批交付，但 repository gate 必须明确识别每个尚未迁移的 surface。
Compatibility alias 只可以在一次迁移步骤中保留原 import name，并且必须直接引用同一个 native
class 或 method；它不得调用旧 Python 实现。

## Verification

完成收敛需要四个边界的自动化证据。

### Native contract tests

- 每个 extension 都能从构建后的 wheel 导入；
- build information 包含正确的 owner、ABI 和 fingerprint；
- control request/response 通过生成的 owner client round-trip；
- Rust 编码的 event fixture 解码成预期的 native event type；
- invalid event frame 和 semantic value 产生稳定的 typed exception；
- current-view read 覆盖所有已发布的 indexed family。

### Cross-language contract tests

Rust 和 Python 使用相同的 owner-owned fixture。测试比较 typed field value、event metadata、error
code、absent-value behavior 和 decimal text，不通过 generic JSON business model 转换。

### Architecture checks

`scripts/check/check_python_architecture.py` 拒绝本 Decision 禁止的 Python production path。检查覆盖
import、owner RPC method literal、generated FlatBuffers access、重复 contract record、generic
business mapping，以及在允许 facade 之外导入 private extension。

### Repository gates

迁移只有在 focused native/Python test、wheel smoke test、`make python-type-check`、
`cargo test --workspace`、workspace dependency check、documentation check、formatting check 和
`git diff --check` 通过后才算完成。如果存在无关的既有失败，必须报告具体失败，同时让受影响范围的
最窄检查通过。

## Consequences

- Account、Capital、Execution、Market、Risk 对 Rust 和 Python consumer 只有一个可执行 contract
  implementation。
- Rust contract 变更会触发 binding compile failure、ABI verification failure 或 cross-language
  contract-test failure，而不是与 Python 静默漂移。
- Python 不再需要 owner generated FlatBuffers module 或手写 business JSON-RPC client。
- Wheel 继续包含五个私有 ABI3 extension，而不是一个跨 owner native package，从而保持 owner 的
  dependency 和 release boundary。
- Pyo3 companion 会因暴露完整 owner contract 而增加代码，但这些代码是 owner boundary 内由编译器
  约束的 adaptation，不是独立 Python contract。
- Consumer Application 可能需要把 native owner value 显式适配成自己的 domain 或 presentation
  model；这些 adapter 归 consumer 所有，不得复用为 owner contract facade。
- Reference 在独立 Decision 替代现有路径之前继续使用当前 Python 实现。
