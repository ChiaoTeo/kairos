# Decision 0042：终态 Activity 与持续 Control 分离

- Status: Accepted
- Date: 2026-08-28
- Refines: [Decision 0014](0014-unified-textual-workbench.md)
- Scope: Kairos Workbench Activity Stream、Interaction Region、结果 presentation、复制与 Transcript

## Context

Workbench 已经使用单一 Activity Stream 保存有限操作的终态结果，并用 Interaction Region 展示当前选择、
输入、确认和 Control。但各 flow 的正文和持续输出逐渐形成了多套约定：

- Market、Reference 和 Strategy 状态把多个彩色 Panel 嵌套进一条 Activity；
- 部分结果直接打印 Application mapping 或 Python `repr`；
- Operations 日志临时写入 Activity Stream，而 Market 自动刷新和 Strategy attach 使用 Interaction Region；
- 模型验证把每轮“你/模型”消息追加成 Activity，重新形成聊天 transcript；
- `audit_summary` 同时承担展示标题，成功标记也混合表达调用成功和业务健康；
- preview、partial、accepted、result unknown、Artifact、时间和证据上下文没有统一语义。

如果继续由每个 flow 自行决定容器、状态和证据格式，同一条 Activity Stream 会表现为多个产品，也无法可靠
复制给 Agent 或在窄终端重建。

## Decision

### 1. Activity Stream 只保存不可变终态

有限操作最多产生一条终态 `ActivityRecord`。导航、Running、自动刷新、日志行和模型验证消息不进入
Activity Stream。Activity 冻结 display title、scope、created-at、outcome、脱敏正文、审计摘要、等价命令和
可选 Artifact；显示编号单调递增且不复用。

Activity 标记只表达用户交互怎样结束：完成、失败、取消、需要注意或通知。被查询对象的健康、降级和
新鲜度由正文结论独立表达。部分成功、已受理和结果未知不得伪装成普通成功。

### 2. 持续输出统一由 Interaction Region Control 承载

Market 自动刷新、服务日志、Launch attach 和模型验证使用当前上下文唯一的 `ControlInteraction`。结构化
快照原地替换，日志使用容量有界的 `LiveBuffer`，支持等待首帧、跟随、暂停、未读、丢弃、轮转、清空、
复制和失败恢复。完整日志或完整会话继续由其业务/平台 owner 持有。

离开上下文必须取消 owner Worker；迟到且 generation、Workspace 或上下文不匹配的结果被丢弃。显式日志或
attach 会话结束时只追加一条摘要 Activity。Market 自动刷新只有在用户明确保存快照时才产生 Activity。

### 3. Workbench 统一结果骨架，不统一业务内容

Workbench 使用六类结果模板：状态概览、对象详情、集合列表、操作结果、检查与诊断、持续控制；空结果、
等待/已受理、部分成功/需要注意、失败/结果未知和 Artifact 作为五种覆盖状态。

所有终态结果按“对象、结论、关键事实、影响、下一步动作”组织，不嵌套装饰性结果 Panel，不默认打印 raw
mapping。Market Quote、Order Book、Account 费率等 owner-specific renderer 保留业务布局，但服从标题、
证据、复制、窄屏和可访问性规则。

### 4. 共享 presentation mechanics，不建立通用业务 renderer

Workbench 只共享无状态的结论、事实表、章节、下一步、时间、duration、count 和 percentage formatter。
业务状态翻译、字段选择、精度和结论继续由所属 flow 根据 owner Application/Contract 事实决定。

不增加 renderer registry、跨业务 trait、通用 mapping-to-page 转换器或新的业务 facade。

### 5. 新功能先归类和复用

新增 Workbench 功能必须先确认业务所有者和用户问题，将结果归入上述六类模板及五种状态覆盖，再寻找已有
生产 flow、owner renderer 或 presentation primitive。不能完整表达时先做保持语义的最小改造；只有现有
模式无法真实回答当前用户问题时才增加模式。新增模式必须说明当前调用者、复用为何不成立、最小新增语义
以及行为、文案、无障碍和快照证据，视觉不同或假设的未来调用者不构成理由。

## Consequences

- Activity 历史稳定、可选择、可复制，不再被日志刷新或模型消息污染。
- Operations 日志需要从 ActivityStream 的瞬态分支迁入 Interaction Region，迁移后删除该 live 分支。
- `ActivityRecord` 和 `OperationSpec` 需要分离展示标题、审计摘要和 scope，并增加 attention 终态。
- 各 flow 必须把 preview、partial、accepted、result unknown 和 Artifact 显式映射为产品结论。
- 状态页和复杂结果需要从 raw mapping 投影为 owner-specific presentation view，而不是引入通用 renderer。
- Transcript 继续保存语义操作和终态摘要；完整日志、对话和 Artifact 的生命周期仍由原 owner 负责。

## Verification

- architecture tests 禁止运行状态嵌套装饰性 Panel、默认 raw `Pretty(result)` 和新增 ActivityStream live 调用；
- flow tests覆盖代表性的状态、集合、操作、诊断和持续 Control，以及 empty、attention、failure 和 Artifact；
- Live Control tests覆盖首帧、暂停、未读、丢弃、轮转、清空、失败、过期结果和结束摘要；
- copy tests证明 scope、时间、二维布局和 Artifact 可以安全线性化；
- 60×20、80×24 和宽屏快照证明核心结论不依赖颜色或横向滚动；
- Python 类型检查和 Workbench 全量测试作为交付门槛。
