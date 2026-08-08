一、需要完成的工作
1. 调整模块职责
最终边界：
Strategy
  → Execution：提交完整策略 Intent
      → Account：查询账户授权、余额、持仓、可用资金
      → Risk：申请/释放/消费 reservation
      → Market/Reference：行情和交易规则校验
      → Integration：下单、撤单、改单、查询成交
      → Account：应用订单和成交事实
职责归属：
Account：余额、锁定余额、持仓、账户交易授权、账户侧订单事实。
Risk：预算和 reservation。
Market：行情和 freshness。
Reference：instrument、交易规则、tick size、lot size 等。
Execution：Intent、执行计划、订单生命周期、成交追踪、执行审计。
Workspace/System：进程生命周期、socket 地址、账户 lease、启动编排。
需要更新旧文档中“Account owns Intent”的描述，统一改为 Execution owns Intent。
2. Strategy 向 Execution 提交完整 Intent
当前 Strategy 的 intent 入口应从 Account socket 调整到 Execution socket。
Intent 必须至少包含：
intent_id
strategy_id
launch_id
instance_id
instrument/market identity
目标数量或目标仓位
账户或账户组
order policy
source snapshot/event identity
幂等信息
Execution 不应只接收已经计算好的 current_quantity、target_quantity 和单笔订单参数，而应接收完整策略意图。
3. Execution 实现完整 Intent 生命周期
Execution 需要负责：
接收和持久化 Intent；
生成执行计划；
将一个 Intent 拆成一个或多个账户、一个或多个订单；
根据当前持仓计算剩余目标；
下单、撤单、改单、重试、补单；
跟踪部分成交和完全成交；
订单失败、过期、未知状态处理；
多账户执行结果汇总；
Intent 完成、部分完成、拒绝、取消、失败等状态转换；
Intent 与 Order、Fill 的关联；
执行审计和恢复。
Execution 不应只执行一次 submit_order 就结束。
4. 实现多账户启动和访问
当前启动流程虽然支持多个账户配置和 lease，但实际上只启动第一个 Account，需要改为：
读取全部 account_refs
→ 获取全部账户 lease
→ 启动全部 Account process
→ 等待全部 ready
→ 启动 Risk/Market/Reference
→ 启动一个 Execution process
→ 给 Execution 注入全部 Account endpoint
→ 启动 Strategy
如果任一账户启动失败：
停止已经启动的账户；
释放已获取的 lease；
launch 标记为 failed；
不启动 Execution 和 Strategy。
5. 建立实例级 endpoint manifest
每个 launch instance 需要记录：
account alias/id → account socket
execution → execution socket
risk → risk socket
market → market socket
reference → reference socket
该资源由 Workspace/System 管理，Strategy 和 Execution 通过它找到其他组件。
要求：
socket 路径不能由 Strategy 自己拼接；
多账户必须有稳定、可区分的 socket；
manifest 在所有组件 ready 后生成；
启停时更新；
StrategyContextBus 根据 manifest 创建 typed clients；
Execution 根据 manifest 访问全部 Account。
6. 实现 Execution 的执行前安全校验
每次真实下单前必须检查：
Account 是否存在；
Account 是否拥有有效 trade lease；
Account 是否允许交易；
余额是否足够；
可用余额扣除锁定后是否足够；
持仓是否满足卖出或减仓要求；
Risk reservation 是否成功；
行情是否存在且足够新鲜；
下单价格是否偏离行情过大；
bid/ask、mark price、last price 是否满足安全阈值；
instrument 是否可交易；
tick size、lot size、最小数量是否合法；
最小名义金额是否合法；
live trading confirmation 是否满足。
余额检查和 reservation 必须具备并发安全性，不能只是读取一次 available 后直接下单。
7. 打通 Account、Risk、Execution 闭环
推荐流程：
Intent accepted
  → Execution preflight
  → Account authorization check
  → Risk reserve
  → Execution creates order
  → Integration submit
  → Execution event
  → Account applies order fact
  → Fill arrives
  → Account applies fill fact
  → Risk consume/release
  → Execution updates Intent
异常时：
下单前失败 → release reservation
订单被拒绝 → release reservation
订单取消 → release reservation
完全成交 → consume reservation
部分成交 → 更新剩余 reservation/目标
8. 发布 Intent snapshot 和 event
ExecutionActor 应发布：
Intent snapshot；
Intent lifecycle event；
Order snapshot；
Order event；
Fill snapshot/event。
snapshot 必须包含：
actor identity；
generation；
event sequence watermark；
intent 状态；
关联 order ids；
parent/child 账户执行关系；
当前目标、已完成数量、剩余数量；
reason/error。
事件必须支持：
从 snapshot watermark 继续消费；
顺序检查；
幂等处理；
恢复和审计。
9. 更新协议、客户端和文档
需要同步修改：
Execution Rust application API；
Execution Unix socket handler；
Python ExecutionSystemClient；
Strategy command bus；
StrategyContext；
FlatBuffers schema/generated bindings；
launch endpoint manifest；
相关架构文档和 command contract。
尤其需要删除或迁移：
Account 的 intent 接收接口；
Account 作为 Intent owner 的描述；
Execution 只接收低级订单参数的接口设计。
二、验收条件
A. 启动与多账户
launch 配置两个或以上账户时，所有 Account process 都能启动。
每个账户都有独立、稳定、可访问的 socket。
Execution 能获得全部账户的 endpoint。
Strategy 能通过 Context 访问指定账户和 Execution。
任一 Account 启动失败时，其他已启动 Account 会被清理，lease 会释放。
Strategy 只有在全部依赖组件 ready 后才会 enable。
launch stop 会停止 Strategy、Execution、Risk、全部 Account，并释放全部 lease。
B. Intent 接口
Strategy 提交完整 Intent 到 Execution socket。
Intent 必须显式带有 strategy_id、launch_id、instance_id、intent_id 和账户范围。
相同幂等键重复提交不会生成重复 Intent。
Execution 可以查询 Intent snapshot、Intent history 和 Intent events。
Account 不再是 Intent 的业务 owner，只接收账户事实。
C. Intent 执行
一个 Intent 可以生成多个账户或多个订单。
Execution 能追踪每个 child execution 与 parent intent 的关系。
部分成交后，Execution 能计算剩余目标并继续执行。
意图完成后状态为 satisfied/completed，而不是仅返回一个已提交订单。
订单拒绝、取消、过期、未知状态会正确反映到 Intent。
撤单、改单、重试和补单都能通过 Intent 状态机审计。
D. 安全校验
账户不存在时不能下单。
无有效 trade lease 时不能下单。
可用余额不足时不能下单。
余额已经被其他订单 reservation 占用时不能重复通过。
行情不存在或过期时不能下单。
价格偏离限制时不能下单。
不符合 tick size、lot size、最小名义金额时不能下单。
Risk reservation 失败时不能调用交易所。
live 模式没有显式确认时不能调用交易所。
所有拒绝原因都有稳定错误 code，而不是只能依赖字符串。
E. 状态一致性
下单成功后，Execution 发布 order event。
Execution 的 order/fill fact 能被 Account 接收。
Account 的余额、锁定金额、持仓能够随订单和成交更新。
订单完成后 reservation 被 consume 或 release。
进程重启后，Intent、Order、Fill 状态可以从 snapshot/journal 恢复。
snapshot + event watermark 能保证消费者不丢事件、不重复应用。
F. 测试与仓库验收
至少新增并通过：
多账户启动测试；
endpoint manifest 测试；
Strategy 通过 Execution 提交 Intent 测试；
多账户 Intent 拆分测试；
余额不足测试；
reservation 冲突测试；
行情过期和价格偏离测试；
部分成交和剩余目标测试；
撤单/改单/重试测试；
Intent snapshot/event 恢复测试；
lease 失效后禁止交易测试。
最终仓库检查：
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check