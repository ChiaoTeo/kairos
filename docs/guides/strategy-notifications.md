# Strategy notifications

Kairos Strategy 可以通过 `ctx.notifications` 发布逻辑通知。策略只选择 route；Workspace 决定 route 最终对应哪个飞书机器人或 Telegram chat。

长期架构约束和失败语义见
[`../decisions/0002-strategy-notification-delivery.md`](../decisions/0002-strategy-notification-delivery.md)。

## 交互式配置

推荐运行 `kairos i`，进入“管理运行资源 → 通知提醒”。该页面会：

1. 显示现有 Destination 和 Credential 可用状态；
2. 分渠道引导配置飞书自定义机器人或 Telegram Bot；
3. 使用 Telegram `getMe` 验证 Bot，并通过 `getUpdates` 发现可选 chat；
4. 显示 Destination 被哪些 Launch 引用；
5. 在显式确认后发送真实测试消息。

Destination 与 Launch route 的关联在创建或编辑 Launch 时配置。进入“运行列表与控制”，
新建或编辑 Launch，并在通知步骤中选择 Destination。通知目标页面不直接修改
Launch-owned route。

也可以直接运行：

```bash
kairos notifications setup --provider feishu --workspace /path/to/project
kairos notifications setup --provider telegram --workspace /path/to/project
kairos notifications list --workspace /path/to/project
```

Destination 和 Credential 是 Workspace 系统资源；route、`default_routes` 和
`lifecycle_routes` 属于 Launch。创建 Launch Instance 时，Kairos 会把 route 映射和
Workspace 通知配置 hash 写入脱敏的 `normalized.json`。运行中的 Instance 不会自动采用
后来修改的 Destination；修改系统通知配置后应创建新的 Instance。

## 1. 配置 Workspace destinations

编辑 Workspace 内的：

```text
.kairos/config/notifications/notifications.toml
```

```toml
version = 1

[destinations.feishu-options]
sender = "feishu"
credential_id = "feishu-options"

[destinations.telegram-personal]
sender = "telegram"
credential_id = "telegram-options"
chat_id = "-1001234567890"
```

Webhook 和 Bot Token 不能写在该文件中。

## 2. 声明 credential values

Credential 文件同时保存 identity、provider 和私有认证值。Webhook 或 Bot Token
不会写入通知配置、日志或 Instance 配置；Credential 文件和目录分别使用 `0600`
和 `0700` 权限：

```text
.kairos/config/credentials/feishu-options.toml
```

```toml
[credential]
id = "feishu-options"
provider = "feishu"
role = "notification-send"

[credential.values]
webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

```text
.kairos/config/credentials/telegram-options.toml
```

```toml
[credential]
id = "telegram-options"
provider = "telegram"
role = "notification-send"

[credential.values]
bot_token = "123456:..."
```

Credential 以整文件方式校验和原子替换，多字段认证不会出现部分更新。运行时只接受
`[credential.values]`，不从环境变量、外部文件或旧版字段回退读取。

## 3. 在 Launch 中选择 routes

```toml
[launch]
id = "spy-option-signals"
mode = "paper"
strategy = "private_strategies.spy_option:SpyOptionStrategy"

[execution]
enabled = false

[notifications]
enabled = true
required = true
default_routes = ["signals"]
# Enables the built-in intent-lifecycle-standard notification policy. Machine-readable
# Execution events and the Strategy decision journal remain enabled regardless.
lifecycle_routes = ["urgent"]
queue_capacity = 256
shutdown_grace_seconds = 5

[notifications.routes]
signals = ["feishu-options", "telegram-personal"]
urgent = ["feishu-options", "telegram-personal"]
```

运行校验：

```bash
kairos notifications validate --mode paper --workspace /path/to/project
kairos launch diagnose validate spy-option-signals --workspace /path/to/project
```

只有显式执行以下命令才会向真实 destination 发送测试消息：

```bash
kairos notifications test feishu-options --workspace /path/to/project
```

管理和绑定命令：

```bash
kairos notifications uses feishu-options --workspace /path/to/project
kairos notifications attach feishu-options --launch spy-option-signals \
  --route signals --default-route --workspace /path/to/project
kairos notifications disable feishu-options --workspace /path/to/project
kairos notifications delete feishu-options --workspace /path/to/project
```

仍被 Launch route 引用的 Destination 默认不能删除；应先 `detach`，或者在明确理解影响时
使用 `--force`。禁用 Destination 后，新 Launch 的校验会明确失败；已运行 Instance 保持其
启动时的装配结果。

## 4. 在 Strategy 中发布

对于会提交 Execution Intent 的策略，先记录 Strategy 决策，再把稳定关联传给提交 API。决策记录、
Intent 终态和效果评估会自动投影到 `lifecycle_routes`：

```python
from kairospy.strategy import DecisionHorizon, EffectEvidence

decision = ctx.decisions.record(
    reason="SPY put skew exceeds entry threshold",
    expected_outcome="positive markout after one minute",
    evidence=(
        EffectEvidence(
            owner="market",
            reference_id="quote:spy:2026-08-18T09:31:00Z",
            event_sequence=event.metadata.sequence,
        ),
    ),
    horizons=(
        DecisionHorizon("execution-final"),
        DecisionHorizon.after("markout-1m", "1m"),
    ),
)
receipt = ctx.execution.option_spread(
    OptionSpreadRequest(
        # ... package fields ...
        strategy_decision_id=decision.strategy_decision_id,
    )
)
```

当 horizon 到期后，策略使用 Account、Market、Risk、Execution 的公开事实调用
`ctx.decisions.evaluate(...)`。结论必须附带可复核的 `EffectEvidence`；迟到成交会把已评价 horizon
重新标记为 pending，后续评价写入更高 revision，不覆盖历史。

策略自己的非生命周期消息仍可直接调用 `ctx.notifications`：

```python
from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class SpyOptionStrategy(Strategy):
    strategy_id = "spy-option-signals"

    def on_quote(self, ctx: StrategyContext, event: QuoteEvent) -> None:
        # 组合筛选和业务 cooldown 由策略拥有。
        if not self._is_opportunity(event):
            return
        receipt = ctx.notifications.publish(
            title="SPY Put Credit Spread",
            body="Sell 590P / Buy 585P; net credit 1.25; max loss 375",
            routes=("signals",),
            severity="info",
            dedupe_key="spy:2026-08-21:590p:585p",
            attributes={"underlying": "SPY", "expiry": "2026-08-21"},
        )
        if receipt.status == "rejected":
            ctx.logger.warning(
                "signal notification was not accepted",
                notification_reason=receipt.reason,
            )
```

`publish` 只做本地验证和有界入队，不等待外部 HTTP。外部失败进入 Strategy health、instance notification health 和脱敏 delivery journal。

## 5. Instance 输出

```text
<instance>/config/normalized.json
<instance>/run/notification/health.json
<instance>/logs/notification/delivery.jsonl
<instance>/artifacts/notifications.jsonl       # backtest only
<instance>/artifacts/strategy-decisions.jsonl  # decision/effect append-only journal
```

Backtest 永远使用本地 Recording sender，不解析真实 notification credential，也不访问飞书或 Telegram。
`GET /v1/health` 同时返回 decision traces、Execution durable/processing cursor、lag/gap
counters、待评价数量与最老超期 horizon。单条诊断可直接查询当前 Strategy instance：

```bash
kairos launch strategy decision <launch-id> <strategy-decision-id> \
  --instance <instance-id> --workspace /path/to/project --format json
```

该命令调用 `GET /v1/decisions/<strategy_decision_id>`，把 Strategy decision journal 与
Execution 当前视图中的 Intent、Plan、Order、Fill、生命周期历史，以及脱敏的通知 destination
投递结果聚合起来。Execution 当前视图会显式标记历史是否截断；诊断结果不是新的业务事实来源。

## 6. Provider 组件与约束

- 真实渠道统一交给 Apprise 1.12+；Kairos 不实现飞书签名、Telegram 分片、HTTP 响应解析或 provider retry。
- 飞书 v1 使用 Apprise 的 `feishu://` custom-bot plugin。该 plugin 当前不支持飞书签名密钥，因此机器人需要关闭“签名校验”，并使用飞书的关键词或 IP 白名单保护。
- Telegram 使用 Apprise 的 `tgram://` plugin。消息长度处理、HTTP 和有限重试由 Apprise 负责。
- 不支持策略提供任意 endpoint、`@all`、任意 mention、按钮、交互卡片或聊天入站命令。
