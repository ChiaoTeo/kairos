# 外部通知能力技术设计

## 1. 文档目的

本文定义 KairosPy 外部通知能力的模块边界、技术方案、渠道适配方式、配置格式和实施步骤。

首期支持以下通知渠道：

- 飞书机器人 Webhook；
- 企业微信机器人 Webhook；
- Telegram Bot API。

通知用于输出系统运行状态、账户状态、订单与成交情况、连接健康状态以及风险事件。通知属于观察和外部输出能力，不参与交易决策，也不能阻塞交易主循环。

## 2. 设计结论

通知能力建立为独立模块，不直接归入交易、账户或 System 模块：

```text
Market / ExternalAccount / Execution / System Events
                         ↓
                    MonitorActor
                         ↓
              Notification Application API
                         ↓
                 Notification Protocol
                         ↑
       Feishu / WeCom / Telegram Adapters
                         ↑
                    Composition
```

核心原则：

1. Monitor 负责观察事实和产生通知意图；
2. Notification application 负责通知用例和业务策略；
3. Infrastructure 负责厂商协议、HTTP 请求和消息格式；
4. Composition 负责根据配置选择具体渠道；
5. 通知失败不得影响下单、成交处理和账户状态更新；
6. 跨模块只依赖目标模块的 `application/` API，不导入 `services/` 或具体适配器。

## 3. 与现有架构的衔接

当前仓库已经具备通知能力所需的几个基础：

- `MonitorActor`：运行时观察者和状态投影拥有者；
- `MonitorOutputCoordinator`：汇总账户视图、周期输出和连接健康信息；
- `MessageBus`：传递系统、行情、执行和账户相关事实；
- `ViewStore`：提供账户当前状态、权益、盈亏、持仓和未完成订单等展示数据；
- `composition`：负责运行模式和具体基础设施的组装。

因此，不应让 ExternalAccount、Execution 或 System 直接调用飞书、企业微信或 Telegram。通知触发应通过事件和 Monitor 观察链路完成。

## 4. 模块结构

建议新增以下目录：

```text
kairospy/application/usecases/notification/
├── __init__.py
├── application/
│   ├── __init__.py
│   ├── config.py         # 配置模型和校验
│   ├── formatting.py     # 厂商无关文本格式
│   └── service.py        # 请求、结果和公开通知用例
├── domain/
│   ├── __init__.py
│   └── policy.py         # 级别、路由、去重和频率策略
├── protocol.py           # NotificationSender 等消费者契约
└── services/
    ├── __init__.py
    ├── routing.py        # 渠道路由
    ├── retry.py          # 重试策略
    ├── deduplication.py  # 重复通知抑制
    └── resilience.py     # 限流和熔断

kairospy/application/actor/notification/
└── application/actor.py  # 队列、投递循环和摘要周期的状态拥有者

kairospy/infrastructure/notifications/
├── __init__.py
├── transport.py          # 统一 HTTP 传输封装
├── feishu.py             # 飞书 Webhook 适配器
├── wecom.py              # 企业微信 Webhook 适配器
└── telegram.py           # Telegram Bot API 适配器
```

### 4.1 Application API

公开 API 应使用业务类型，不暴露 SDK 类型、厂商 payload 或 service 实例。例如：

```python
@dataclass(frozen=True, slots=True)
class NotificationRequest:
    category: NotificationCategory
    title: str
    body: str
    level: NotificationLevel = NotificationLevel.INFO
    deduplication_key: str | None = None


class NotificationApplication(Protocol):
    async def send(self, request: NotificationRequest) -> NotificationResult:
        ...
```

实际类型名称可以在实现阶段根据项目既有命名规范调整。重要的是，调用方只表达“发送什么业务通知”，不表达“调用哪个 Webhook”。

### 4.2 Protocol

`NotificationSender` 是通知模块的消费者契约，应放在通知模块自己的 `protocol.py`：

```python
class NotificationSender(Protocol):
    async def send(self, request: NotificationRequest) -> None:
        ...
```

飞书、企业微信和 Telegram 都实现这个最小接口，但实现类保持在 Infrastructure 内部。

### 4.3 NotificationActor

当通知启用时，`NotificationActor` 是通知运行时状态的唯一拥有者，负责：

- 持有有界发送队列；
- 将高优先级告警和普通摘要区分处理；
- 在独立投递循环中调用 Notification application；
- 记录队列深度、丢弃数量、成功数量、失败数量和最近错误；
- 按配置周期读取 Monitor 已发布的账户视图并生成摘要。

业务事件处理只负责向 Actor 入队，不等待外部 HTTP 请求。这样通知慢、网络异常或渠道重试都不会占用交易事件的业务处理路径。

## 5. 通知类型

首期建议支持以下类型：

| 类别 | 典型内容 | 默认级别 |
| --- | --- | --- |
| `system.lifecycle` | 启动、停止、正常结束 | INFO |
| `system.error` | 未处理异常、运行失败 | ERROR |
| `connection.health` | 连接断开、恢复、降级 | WARNING |
| `execution.order` | 下单、撤单、拒单 | INFO/WARNING |
| `execution.fill` | 部分成交、完全成交 | INFO |
| `risk.alert` | 风控拒绝、预算不足 | WARNING/ERROR |
| `account.snapshot` | 权益、现金、持仓、盈亏摘要 | INFO |
| `trading.summary` | 周期交易统计和收益汇总 | INFO |

不建议将每一条行情或每一个内部调试事件直接发送到外部渠道。行情应继续留在内部数据流，只有经过策略筛选的状态变化才进入通知链路。

## 6. 可靠性设计

### 6.1 不阻塞交易链路

通知发送应采用有界异步队列或通知 Actor 的内部队列。交易事件进入通知队列后即可返回，外部 HTTP 请求由通知侧处理。

通知侧需要拥有以下运行状态：

- 待发送数量；
- 最近一次成功时间；
- 最近一次失败时间和错误类型；
- 每个渠道的健康状态；
- 当前重试次数。

如果后续要求跨进程恢复、进程崩溃后不丢通知，再引入持久化 outbox；首期不必为了通知引入数据库。

### 6.2 重试、退避和去重

- 对连接超时、临时网络错误和 HTTP 5xx 做有限重试；
- 对 HTTP 4xx、鉴权错误和参数错误不做盲目重试；
- 使用指数退避和最大延迟；
- 为成交、异常和恢复事件提供去重键；
- 对账户摘要使用“同一时间窗口只发送一次”的策略；
- 队列满时保留高优先级告警，低优先级摘要可以丢弃并记录日志。

### 6.3 安全

- Webhook、Bot Token 和 Chat ID 不写入代码；
- 优先从环境变量或现有 credentials 配置读取；
- 日志中只打印渠道名称、请求类别、状态码和 request id；
- 不打印完整 URL、签名、Token 或完整账户凭证；
- 账户通知只输出必要信息，避免泄露私钥、API Key 和敏感原始 payload。

## 7. 成熟库选型

项目当前已经使用 `requests`，但运行时包含异步 Actor 和 MessageBus，因此建议统一采用支持同步和异步调用的轻量 HTTP 客户端。

### 7.1 HTTP：HTTPX

推荐使用 `httpx`：

- 同时提供同步和异步客户端；
- 支持连接池、超时、代理和标准 HTTP 错误处理；
- 适合当前异步运行时；
- 飞书、企业微信和 Telegram 都只需要调用 HTTP API，不需要引入大型厂商 SDK。

建议：通知模块内部统一注入一个 HTTP transport，适配器不直接创建全局客户端，方便测试时替换为 fake transport。

### 7.2 重试：Tenacity

推荐使用 `tenacity` 实现有限重试和指数退避：

- 成熟、职责单一；
- 支持同步和异步函数；
- 可以按异常类型和结果进行重试；
- 测试时可以禁用或缩短等待时间。

重试策略仍由通知模块控制，不能让厂商适配器各自定义一套不同策略。

### 7.3 配置：复用现有 TOML 和环境变量机制

不建议为了通知配置引入 Pydantic Settings 或新的配置框架。项目已经使用 Python 标准库 `tomllib` 和现有 workspace/config 机制，通知配置应沿用已有配置解析、校验和敏感值解析能力。

### 7.4 定时任务：复用现有 Actor 生命周期

不建议首期引入 APScheduler。账户摘要和交易汇总可以由 Monitor/Notification 的运行循环根据时间窗口触发，减少新的调度器、线程和生命周期边界。

如果将来需要跨进程、持久化和多任务日历调度，再单独评估 APScheduler 或外部调度系统。

### 7.5 Telegram SDK 的取舍

首期只发送 Telegram 消息时，直接通过 HTTPX 调用 Telegram Bot API 更简单，依赖更少，也更容易控制错误和重试。

只有在未来需要以下能力时，才考虑引入 `python-telegram-bot`：

- 接收 Telegram 命令；
- 交互式按钮和回调；
- 长轮询或 webhook 服务；
- 会话、命令路由和用户权限管理。

## 8. 配置建议

```toml
[notifications]
enabled = true
summary_interval = "5m"
queue_size = 256
max_attempts = 3
deduplicate = true
minimum_level = "info"
rate_limit_per_minute = 30
circuit_breaker_failures = 5
circuit_recovery_seconds = 30

[notifications.feishu]
enabled = true
webhook_url = "${FEISHU_WEBHOOK_URL}"
secret = "${FEISHU_WEBHOOK_SECRET}"
categories = ["system.*", "execution.fill", "system.error"]

[notifications.wecom]
enabled = true
webhook_url = "${WECOM_WEBHOOK_URL}"

[notifications.telegram]
enabled = true
bot_token = "${TELEGRAM_BOT_TOKEN}"
chat_id = "${TELEGRAM_CHAT_ID}"
parse_mode = "MarkdownV2"
```

配置校验要求：

- 渠道启用时必须具备对应凭证；
- `queue_size` 和 `max_attempts` 必须为正整数；
- `summary_interval` 必须能解析为有效时间间隔；
- Telegram 的消息转义必须由适配器负责，业务层不直接拼接 MarkdownV2 转义细节；
- 缺少可选渠道配置时，只禁用该渠道，不应导致整个交易系统无法启动；
- 明确区分“配置错误”和“远端发送失败”。

## 9. 测试方案

### 单元测试

- 通知级别和事件分类；
- 路由策略；
- 去重键；
- 摘要时间窗口；
- 重试条件和最大次数；
- 飞书、企业微信和 Telegram payload 转换；
- Token、Webhook 和账户敏感信息脱敏。

### 集成测试

- 使用 HTTPX fake transport，不访问真实外部服务；
- 验证 2xx、4xx、5xx、超时和非法响应；
- 验证一个渠道失败不会影响其他渠道；
- 验证通知失败不会阻塞交易事件处理；
- 验证配置只启用一个或多个渠道时的组合结果。

### 端到端测试

- 模拟成交事件，检查通知 application 收到业务请求；
- 模拟系统异常，检查 ERROR 通知；
- 模拟时间推进，检查账户摘要只在窗口边界发送一次；
- 检查通知链路的运行状态能被 Monitor 观察并输出。

## 10. 分阶段实施

### 阶段一：契约和模型

- 建立独立 notification 模块；
- 定义通知请求、结果、级别和分类；
- 定义 `NotificationSender` protocol；
- 加入配置模型和脱敏校验；
- 使用 fake sender 完成 application 测试。

状态：已实现。

### 阶段二：渠道适配器

- 引入 HTTPX；
- 实现统一 HTTP transport；
- 实现飞书、企业微信、Telegram 三个适配器；
- 使用 fake transport 完成渠道 payload 和错误处理测试。

状态：已实现。

### 阶段三：运行时接入

- 从 Monitor 接入系统生命周期、连接健康、订单和成交事件；
- 增加账户定时摘要；
- 增加异步队列、有限重试、退避和去重；
- 将渠道健康状态纳入 Monitor 输出。

状态：已实现基础版本；通知 Actor 的运行指标通过 Monitor 的只读 Actor metrics 观察。

### 阶段四：生产强化

- 增加持久化 outbox（确有不丢消息需求时）；
- 增加通知发送统计和告警；
- 增加渠道级限流和熔断；
- 增加通知回放和失败重放工具。

状态：渠道级限流和熔断已实现；持久化 outbox、回放和失败重放保留为需要跨进程不丢消息时的增强项。

## 11. 验收标准

完成首期后应满足：

- 飞书、企业微信和 Telegram 可以独立启用或禁用；
- 交易模块不依赖任何通知厂商代码；
- 任一通知渠道失败不会阻断交易处理；
- 系统启动、停止、异常和成交状态可以被推送；
- 账户摘要可以按配置周期推送；
- 敏感配置不会出现在日志和业务事件中；
- 通知模块可以使用 fake sender 和 fake HTTP transport 独立测试；
- 通过项目要求的 focused tests、`uv run pytest -q`、`git diff --check` 以及跨模块 services 导入搜索。

## 12. 当前决策摘要

最终采用“独立 Notification 模块 + Infrastructure 渠道适配器 + Composition 注入”的方案。

首期库选型：

- HTTP：`httpx`；
- 重试：`tenacity`；
- 配置：复用现有 `tomllib` 和 workspace 配置体系；
- 定时：复用现有 Actor/Monitor 生命周期；
- Telegram：首期直接调用 Bot API，不引入 Telegram SDK。

该方案能保持模块边界清晰，同时避免为了三个简单的出站通知渠道引入过多运行时组件。
