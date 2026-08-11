# 可观测性验收审计（2026-08-10）

本清单是 `strategy-observability.md` 的执行证据索引。`通过`仅表示已有对应的
本地代码与可重复验证；不把生产环境、CI 运行记录或提交状态用推断代替。

| 验收项 | 状态 | 证据 / 缺口 |
|---|---|---|
| Rust/Python endpoint 语义 | 通过 | 两端均测试 generic 和 signal endpoint 优先级。 |
| W3C Python → Rust 传播 | 通过 | `tests/test_rust_otel_integration.py`；release binary 运行结果为 4 passed。 |
| JSONL 与 trace 日志关联 | 通过 | `tests/test_unix_http.py`、Collector Loki smoke。 |
| 敏感字段脱敏 | 部分通过 | Python 覆盖字段、嵌套字段和异常文本；仍需对全部 Rust error-format 调用进行同等级系统性验证。 |
| 最小指标集 | 实现待全面查询验证 | readiness、operation、queue、lag/gap/retry、outbox oldest age、checkpoint age 已有埋点；尚未为每项建立真实 backend query 断言。 |
| Collector 与三后端 | 通过（本地） | `tests/test_collector_smoke.py`，trace/metric/log 各一项及 health 均已运行。 |
| Collector 故障隔离 | 部分通过 | 拒绝连接、5xx、慢响应和重启已验证；exporter queue 满、异常退出的最终日志仍无自动化证据。 |
| 生产配置 | 配置通过，环境待验收 | `otel-collector.production.yaml` 已由 0.121.0 validate；真实证书、认证后端、权限和保留策略尚未部署验证。 |
| CI 强制执行 | 已编排，待远端证据 | workflow 已安装 observability extra、运行跨语言和 smoke；尚无实际 GitHub Actions URL/artifact。 |
| 性能 | 未通过 | release 500 请求：throughput -0.92%、p95 未恶化、RSS +22.64%；RSS 超出 ≤10% 门槛。 |
| 可审查交付 | 未通过 | 工作区存在大量既有改动，相关变更尚未形成独立 commit/PR。 |

## 结论

当前状态符合“开发环境闭环”的大部分技术条件，但因为 RSS 性能门槛、部分故障注入、
生产环境实际验证、CI 运行证据与独立交付均未完成，不能标记为“生产可用”或“全部落地”。
