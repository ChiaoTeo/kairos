# 0047 — Reference 独立 CLI 与服务层复用

- Status: Accepted
- Date: 2026-09-06
- Implementation: 目标边界已确认，尚未完成实现；本文不是现有命令使用指南。

## Context

Reference 管理可持久化的交易对象目录。用户应能通过 CLI 获取、校验并保存数据，命令
完成后退出；之后仅凭有效的数据库即可查询。常驻 Reference process 提供持续维护，
不应成为目录查询或单次落盘的必要前提。

当前 [本地应用入口](../../crates/modules/reference/src/application/cli/local.rs) 已支持直接
打开数据库，但 [CLI](../../crates/modules/reference/src/bin/kairos-reference-cli.rs) 仍先打开
Workspace。[同步 tick](../../crates/modules/reference/src/application/process/runtime/tick.rs)
把工作流推进与进程 phase、定时调度状态混合，单次 refresh 也不代表所选范围已经同步完成。
简单地从 CLI 启动同一个 Actor 或调用一次 tick，不能建立可靠的单次执行语义。

本决策补充 [0046](0046-reference-venue-coverage-and-current-query.md)，保留其 canonical
模型、coverage、来源配置所有权和冲突处理规则。

## Decision

### 应用入口与复用边界

CLI 与常驻进程都通过 Reference application 进入业务能力。application 定义操作范围、
完成条件和业务结果；复用的具体实现放在现有 `services/` 中，负责来源获取、分页恢复、
校验和持久化。领域不变量仍属于 domain，具体资源与 Integration 能力由 composition 组装。

```text
CLI 输入 → composition → application → 同步 service → Reference 存储
常驻进程 → application → Actor → 同一个同步 service → Reference 存储
```

CLI 不直接调用 service 或另写 SQL 导入路径。其他业务 package 仍只依赖 Reference
contract；读取通过 contract-owned typed query，不暴露表结构。网络与鉴权仍由 Integration
拥有，不在 Reference 内重建 Provider 客户端。独立命令可以创建必要的进程内资源，但不应
要求启动 Reference 常驻服务、控制 socket 或周期调度。

当前复用的两个调用方是单次 CLI 操作和常驻同步。现有同步 service 是优先扩展的边界；
不新增通用 Engine、Manager、端口 trait 或第二套工作流来包裹现有实现。

### Actor 与状态所有权

保留 Reference Actor，负责常驻模式的串行命令处理与业务状态所有权。本次调整不取消
Actor，也不要求 CLI 复用 Actor 的消息循环；两种运行方式复用的是具体同步 service。

同步进度、待提交候选和目录可变状态始终只有一个所有者。常驻模式由 Actor 独占并驱动
service，service 内部的工作流状态也处于 Actor 的所有权之下，不在 Actor 中再复制一份。
单次模式由 application 在操作期间独占 service，CLI 只调用 application。这里复用的是
同一套实现，不是让两个运行方式共享一个可变实例或同时维护同一数据库的写入状态。

数据库写入所有权必须覆盖状态加载、同步、提交和关闭的完整执行期间，CLI 与服务使用
同一互斥机制。取得所有权之前不得迁移数据库或修改来源配置。仅依靠 SQLite 单个事务的
写锁不足以保护两个独立同步状态机；已有写入者时拒绝本地写入，只读查询仍可使用。

### 单次落盘的完成语义

一次同步固定所选 source 与 declared scope，推进至该次扫描完成校验和提交，或进入失败、
取消、超时等明确终态。正常分页中的“仍在同步”应以业务进度表达，不作为同步成功；
无变更也只有在所选范围完成后才是成功结果。单次执行不等待下一轮周期同步。

两种入口共用 fetch → stage → validate → atomic promote。目录事实、membership、coverage、
watermark 与 outbox 按既有提交边界保持一致。原子性以实际提交的完整范围为单位，不承诺
所有来源组成一个大事务。多来源部分失败时，保留成功提交，逐项报告失败并返回非零退出码。

中断不发布未完成候选，也不把未完成范围标为完整；已提交数据继续可读。续跑遵守既有
cursor/staging 恢复规则，不能假定所有来源都支持断点恢复。CLI 的成功表示落盘完成；
未投递事件保留在 outbox，后续发布继续处理，不把缺少在线订阅者视为落盘失败。

### CLI 产品语义

产品围绕目录动作组织，不要求用户先选择 standalone 或 connected 模式。

本次核心能力是按用户目标获取并保存目录：普通入口以 get、query/search 和 update 表达
获取、查询与更新。首次获取在明确的目标数据库内完成必要初始化、来源选择与持久化，
用户不必先创建 source、命名来源或传递计划文件。来源管理是内部机制或高级运维能力。
重复获取同一目标复用已有定义；更新只作用于已准备目标。实际来源和范围必须满足用户
目标，需要扩大范围时明确请求选择，不能静默扩大或把覆盖不足报告为完成。

| 用户动作 | 语义与必要依赖 |
| --- | --- |
| 获取并保存目录 | 用户指定目标，完成必要建库、来源配置与获取，等待实际范围提交后退出 |
| 更新已有目录 | 使用已准备目标的来源依据，不隐式增加新目标或切换来源 |
| 查询与解析 | 只读有效数据库，不自动联网；返回 coverage 与 watermark |
| 查看目录状态 | 展示已获取范围、更新时间、完整性及来源问题，不等同于进程健康 |
| 持续维护 | 经现有 System 生命周期入口启动，继续维护同一目录与来源配置 |

CLI 支持直接指定数据库；Workspace 是默认路径和配置的便利来源，而非本地查询的前提。
联网同步除数据库外仍需来源定义、连接配置及适用凭据。查询数据库缺失或版本不兼容时
明确报错，不通过只读操作隐式初始化或迁移。

服务停止不直接意味着数据过期或不可用。新鲜度与覆盖结论由证据决定，消费方按用途决定
是否接受。未获取范围应显示“尚未获取”，不能显示为“范围内没有”。查询必须先按条件
过滤再分页，不从截断的全目录片段推断不存在或唯一匹配。

同步显示来源、范围和进度；终态输出提交情况、变更统计、coverage、watermark 和数据库
位置。机器结果使用结构化 JSON，进度写入 stderr，退出码区分成功与未完整完成。
本地写入遇到服务占用时明确报告占用；若以后提供委托执行，必须显式选择且保持等待完成
语义，不随环境静默切换执行方式。具体子命令及参数拼写在实现时对齐现有 CLI。

### 操作进度与持续维护状态

单次 get/update 的有限进度由共用 service 产生、application 聚合，CLI 与 Workbench 消费
同一语义结果。常驻 Actor/process 另提供持续维护状态，包含来源健康、当前工作与调度；
System 提供进程和连接状态。三种事实不得互相替代：进程退出不证明目标已提交，服务存活
不证明来源健康，停止服务不直接证明目录失效。

Workbench 的单次获取使用 RunningInteraction，最多保存一条终态 Activity；服务状态/日志
跟随使用现有 ControlInteraction。取消获取要结束本次业务操作并回收执行资源，退出服务
观察只取消观察 Worker，不能停止服务或取消 Actor 当前工作。进度百分比仅在实际总量已知
时表达对应阶段，获取完成后仍须等待校验与提交才能报告成功。

## Consequences

- 用户可以先建立和使用目录，再选择持续维护；CLI 与服务产生相同业务语义的数据。
- 需要拆开工作流进度与进程调度状态，不能将现有一次 refresh 返回值当成完整同步结果。
- 单写入者模型限制并发写入入口，但避免 Actor 与 CLI 维护两套目录状态；读者继续通过
  短只读事务读取已提交 generation。
- 文件导入等后续落盘入口必须复用相同业务校验和提交边界；本决策不承诺具体导入格式。
- Actor 保留为常驻模式的状态所有者；实现后同步更新 Reference README，说明单次模式
  的 application 所有权与 service 复用边界，不把目标设计提前宣称为现有能力。

验收证据应覆盖：无服务时 CLI 同步后可查询、两种入口的目录与 coverage 一致、分页未完成
不会报告成功、中断不暴露半成品、多来源部分失败如实报告、并发写入被拒绝，以及服务随后
能继续同步和处理待发布事件。首条产品闭环是“按目标获取并落盘 → 无服务查询 → 更新已准备
目录 → 按需选择服务接续维护”；初始化和来源配置由获取用例内部完成。
