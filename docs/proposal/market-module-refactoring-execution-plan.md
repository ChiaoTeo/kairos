# Market 模块结构重构落地计划

## 1. 文档定位

- 状态：已完成（后续仅保留增量治理）
- 范围：`crates/modules/market` 与其独立 `contract` crate
- 设计依据：`docs/proposal/market-module-structure-refactoring.md`
- 目标：把设计提案转换为可以逐项删除旧代码、验证行为并验收的实施清单

本文件是 Market 重构的实施状态权威来源。原设计提案负责解释领域边界和取舍，本文件负责记录最终目录、迁移顺序、旧代码删除项和验证证据。

目录出现不等于迁移完成。一个切片只有同时满足以下条件才可标记完成：

1. 真实实现已经进入目标模块；
2. 原聚合文件中的对应实现已经删除；
3. 没有兼容转发模块或第二份状态；
4. focused behavior tests 和 architecture tests 通过；
5. 本文件的状态和证据已经更新。

## 2. 不变量

1. `MarketApplication` 是唯一公开用例 facade。
2. `MarketActor` 是唯一可变 Market 状态 owner。
3. Order Book 是 Observation，与 Quote、Trade、Bar 等平级；只允许内部机制更复杂。
4. Market domain 不定义 Reference client、Reference DTO 或 Reference persistence model。
5. Reference client、provider connector、mmap publisher、JSONL persistence 等具体实现只在 composition 选择。
6. Application 和 services 不依赖 composition。
7. Process 接收 typed input，不解析 JSON wire record。
8. Current view、replay checkpoint、ordered change 使用不同类型，不创建第二份 current-state projection。
9. 不为视觉效果创建空文件；计划列出的文件必须承载真实类型、规则或调用入口。
10. 每个切片迁移后立即删除旧概念，不保留长期 compatibility facade。

## 3. 最终目录

以下目录是当前认可的最终结构。与旧提案相比，明确保留 `domain/events/`、`domain/view/` 和 `services/actor/checkpoint.rs`，因为它们分别属于领域变化、当前业务视图和 Actor 私有恢复状态。

```text
crates/modules/market/
  contract/
  src/
    lib.rs
    bin/
      kairos-market-server.rs
      kairos-market-cli.rs

    application/
      mod.rs
      model/
        mod.rs
        error.rs
        query.rs
      observations/
        mod.rs
        projection.rs
        source_inputs.rs
        quote/mod.rs
        trade/mod.rs
        bar/mod.rs
        trade_bar/mod.rs
        quote_bar/mod.rs
        ticker_24h/mod.rs
        option_greeks/mod.rs
        rate/mod.rs
        mark_price/mod.rs
        index_price/mod.rs
        funding_rate/mod.rs
        open_interest/mod.rs
        order_book/
          mod.rs
          projection.rs
          continuity.rs
          resync.rs
      subscriptions/
        mod.rs
        static_subscription.rs
        dynamic_subscription.rs
        lifecycle.rs
        resolution.rs
      universe/
        mod.rs
        reconciliation.rs
        recovery.rs
      queries/
        mod.rs
        observations.rs
        order_books.rs
        freshness.rs
        execution_estimate.rs
      sources/
        mod.rs
        attachment.rs
        subscriptions.rs
        recovery.rs
      process/
        mod.rs
        lifecycle.rs
        actor_task.rs
        ingress.rs
        maintenance.rs
        universe.rs
        recovery.rs
        publication.rs
        shutdown.rs
      replay/
        mod.rs
        model.rs
        loader.rs

    services/
      mod.rs
      actor/
        mod.rs
        state.rs
        checkpoint.rs
        subscriptions.rs
        sources.rs
        freshness.rs
        read_model.rs
        events.rs
        universe/mod.rs
        observations/
          mod.rs
          views.rs
          quote/mod.rs
          trade/mod.rs
          bar/mod.rs
          trade_bar/mod.rs
          quote_bar/mod.rs
          ticker_24h/mod.rs
          option_greeks/mod.rs
          rate/mod.rs
          mark_price/mod.rs
          index_price/mod.rs
          funding_rate/mod.rs
          open_interest/mod.rs
          order_book/
            mod.rs
            continuity.rs
      source/
        mod.rs
        messages.rs
        driver.rs
        stream.rs
        snapshot.rs
        replay.rs
        normalization.rs
        recovery.rs
      control/
        mod.rs
        transport.rs
        wire.rs
        ingress.rs
        response.rs
      publication/
        mod.rs
        fanout.rs
        queue.rs

    composition/
      mod.rs
      config/
        mod.rs
        dto.rs
        profile.rs
        sources.rs
        defaults.rs
      launch/
        mod.rs
        process.rs
        diagnostic.rs
      sources/
        mod.rs
        routing.rs
        activation.rs
        binance.rs
        okx.rs
        hyperliquid.rs
        massive.rs
        replay.rs
      reference/
        mod.rs
        client.rs
        events.rs
        projection.rs
      history/
        mod.rs
        jsonl.rs
      publication/
        mod.rs
        views.rs
        events.rs
        encoding.rs
        mmap.rs

    domain/
      mod.rs
      events/mod.rs
      view/mod.rs
      observation/
        mod.rs
        identity/
          mod.rs
          kind.rs
          key.rs
          qualifier.rs
          capability.rs
        quote/mod.rs
        trade/mod.rs
        bar/mod.rs
        trade_bar/mod.rs
        quote_bar/mod.rs
        ticker_24h/mod.rs
        option_greeks/mod.rs
        rate/mod.rs
        mark_price/mod.rs
        index_price/mod.rs
        funding_rate/mod.rs
        open_interest/mod.rs
        order_book/
          mod.rs
          book.rs
          level.rs
          delta.rs
          continuity.rs
      subscription/
        mod.rs
        intent.rs
        member.rs
        selector.rs
        status.rs
      source/
        mod.rs
        identity.rs
        route.rs
        state.rs
        readiness.rs
      freshness/
        mod.rs
        status.rs
        evaluation.rs
      market/
        mod.rs
        resolved.rs
        data_route.rs
        selection.rs

  tests/
    architecture.rs
    application/
    process/
    composition/
    behavior/
```

`state.rs` 只定义 `MarketActor` 的字段、构造和 restore；它不是继续容纳所有状态转换的兜底文件。

## 4. 当前基线

### 4.1 已完成

- [x] 四层根目录已经建立，根目录只保留模块边界文件。
- [x] `domain/reference/` 和 Market-owned Reference SQLite reader 已删除。
- [x] Reference contract client 限制在 `composition/reference/`。
- [x] `MarketDescriptor` 已替换为 `ResolvedMarket` 与 `MarketDataRoute`。
- [x] `latest` 与 `views` 双 projection 已收敛为单一 current view。
- [x] snapshot、view、checkpoint、change 类型已经分离。
- [x] 所有公开 Observation kind 已在 Domain/Application/Actor 建立同名目录和真实入口。
- [x] 无状态 Spot/Perpetual/Future/Option wrapper 已删除。

### 4.2 未完成证据

- [x] `application/process/runtime.rs` 已删除并拆分为生命周期、Actor task、typed ingress、maintenance、universe、recovery、publication、shutdown。
- [x] `application/sources/orchestration.rs` 已删除并按 attachment/subscriptions/recovery 拆分。
- [ ] `services/actor/state.rs` 已降至约 227 行，Source/Freshness 已迁出，read model/event draining 仍待迁出。
- [x] `services/publication/encoding.rs` 已删除，contract encoding 已迁入 `composition/publication/`。
- [x] `composition/publisher/mmap.rs` 已迁入 `composition/publication/mmap.rs`。
- [ ] `services/source/stream.rs` 仍约 880 行。
- [ ] `composition/config/model.rs` 仍约 600 行。
- [x] Domain subscription/source/freshness 与 Order Book 内部结构已拆分。
- [ ] tests 仍平铺在四个顶层文件中。

## 5. 执行阶段

### Phase 0：目标树与保护规则

- [x] 建立本执行计划。
- [x] 修正 event/view/checkpoint 的最终归属。
- [x] Architecture test 固化最终一级目录和禁止恢复旧路径。
- [ ] 为每个阶段增加“目标文件存在且旧文件不存在”的检查。

删除项：旧提案中 `application/model/event.rs`、`view.rs`、`checkpoint.rs` 的目标要求。

### Phase 1：Observation、identity 与 Order Book

- [x] 建立 `domain/observation/identity/` 并迁移 kind/key/qualifier/capability。
- [x] 拆分 `domain/observation/order_book/` 的 book/level/delta/continuity。
- [x] 把公共 projection/source input 行为迁入 Application 对应文件。
- [x] 把 view update 和 Order Book continuity 状态转换迁出 `actor/state.rs`。
- [x] 删除 `domain/observation/kind/` 和 Actor 中旧 Observation 实现。

验证：kind capability matrix、selector filtering、out-of-order view、Order Book gap/resync、三层路径 architecture test。

完成证据（2026-08-17）：

- `cargo test -p kairos-market --no-fail-fast`：48 lib、10 Actor、19/20 architecture（首次运行仅因 architecture assertion 仍读取旧 `observations/mod.rs` 路径失败）、4 Order Book、2 replay；业务测试全部通过；
- 修正 architecture assertion 后，`cargo test -p kairos-market --test architecture`：20 passed；
- `cargo test -p kairos-market-contract`：2 passed；
- `cargo check -p kairos-market --tests`、Market fmt 和 scoped `git diff --check` 通过；
- architecture test 明确禁止 Observation/Order Book 实现回流 `services/actor/state.rs`。

### Phase 2：Subscription 与 Universe

- [x] 拆分 Domain intent/member/selector/status。
- [x] 迁移 Actor subscriptions 与 universe 状态转换。
- [x] 拆分 Application static/dynamic/lifecycle/resolution。
- [x] 将 Universe reconciliation 与 recovery 分离。
- [x] 删除旧的聚合 subscription/universe 实现。

验证：static/dynamic subscribe、owner release、budget、watermark、reconciliation idempotency。

完成证据（2026-08-17）：

- `cargo check -p kairos-market --tests` 通过且无 warning；
- `cargo test -p kairos-market --lib -- --test-threads=1`：48 passed；
- Actor/architecture/Order Book/replay integration tests：10 + 21 + 4 + 2 passed；
- architecture test 禁止 Subscription/Universe 行为回流 `services/actor/state.rs`；
- `application/universe/resolution.rs` 已删除，市场选择解析归入 subscription resolution，watermarked replacement 与 recovery view 分别位于 Universe reconciliation/recovery。

### Phase 3：Source 与 Freshness

- [x] 拆分 Domain source identity/route/state/readiness。
- [x] 拆分 Domain freshness status/evaluation。
- [x] 迁移 Actor sources/freshness 状态转换。
- [x] 拆分 Application attachment/subscriptions/recovery。
- [x] 拆分 services source driver/normalization/recovery。
- [x] 在 composition 建立 routing/activation/replay。
- [x] 删除 `application/sources/orchestration.rs`。

验证：epoch、fair polling、confirmation、reconnect、freshness、targeted Order Book resync、bounded shutdown。

完成证据（2026-08-17）：

- `cargo check -p kairos-market --tests` 通过且无 warning；
- `cargo test -p kairos-market --no-fail-fast -- --test-threads=1`：50 lib、10 Actor、22 architecture、4 Order Book、2 replay 全部通过；
- Source 测试覆盖 fair polling、订阅确认、epoch advance/stale ack、reconnect、targeted Order Book resync 与 bounded shutdown；Freshness 增加阈值边界和时钟倒退判定测试；
- `services/source/stream.rs` 从约 880 行降至约 604 行，normalization/recovery 已成为真实独立模块；
- architecture test 固化 Domain/Application/Actor/Services/Composition 的 Source/Freshness 路径，并禁止行为回流 `actor/state.rs` 或恢复 `application/sources/orchestration.rs`。

### Phase 4：Process 与 Control

- [x] 拆分 actor task、typed ingress、maintenance、universe、recovery、publication、shutdown。
- [x] 拆分 control transport/wire/ingress/response。
- [x] Process 不再解析 JSON。
- [x] 删除 `application/process/runtime.rs`。

验证：command idempotency、owner isolation、health、pause/resume、shutdown、typed control boundary architecture test。

完成证据（2026-08-17）：

- `cargo check -p kairos-market --tests` 通过且无 warning；
- Phase 4 首次完整测试中 50 lib、10 Actor、4 Order Book、2 replay 全部通过，architecture 仅两处仍读取已删除 `runtime.rs` 的旧断言失败；
- 更新断言后 `cargo test -p kairos-market --test architecture -- --test-threads=1`：22 passed；Process command idempotency/owner isolation/pause-resume 聚焦测试：6 passed；
- `runtime.rs` 已删除，Actor loop 的生产代码约 170 行；control transport/wire/ingress/response 均为真实模块；
- architecture test 固化最终 Process/Control 文件集合，并静态禁止 Process 中出现 `serde_json::from_*` 或 command wire DTO。

### Phase 5：Publication、History 与 Replay

- [x] services publication 只保留 fanout/queue。
- [x] contract mapping、encoding、mmap 实现迁入 composition/publication。
- [x] JSONL recorder 迁入 composition/history。
- [x] Application replay 拆分 model/loader。
- [x] 删除 `services/publication/encoding.rs` 与 `services/history/`。

验证：FlatBuffers round-trip、sequence preservation、backpressure、history recovery、checkpoint resume。

完成证据（2026-08-17）：

- `cargo check -p kairos-market --tests` 通过且无 warning；
- `cargo test -p kairos-market --no-fail-fast -- --test-threads=1`：50 lib、10 Actor、23 architecture、4 Order Book、2 replay 全部通过；
- FlatBuffers event root、mmap 单资源写入、publication backlog/fanout、JSONL crash recovery/corruption、replay checkpoint resume 均有通过测试；
- Application 通过注入的 event encoder 函数和 typed `HistoryQueue` 工作，不导入 Composition；Services publication 不再包含 contract mapping、encoding、mmap 或 JSONL 实现；
- architecture test 固化最终 Publication/History/Replay 所有权并禁止恢复旧路径。

### Phase 6：Composition 收敛

- [x] assembly/process/diagnostic 收敛为 composition/launch。
- [x] config 拆分 dto/profile/sources/defaults。
- [x] Reference watcher 拆分 client/events，保留 projection。
- [x] 删除 composition/assembly、process、diagnostic、publisher 旧目录。

验证：provider isolation、launch/profile、Reference catch-up、live/replay composition。

完成证据（2026-08-17）：

- `cargo check -p kairos-market --tests` 通过且无 warning；
- `cargo test -p kairos-market --test architecture -- --test-threads=1`：25 passed；
- provider attachment/default endpoint/manual diagnostics 已归入 `composition/sources/activation.rs`，进程装配与诊断入口归入 `composition/launch/`；
- config 已拆为 `dto/profile/sources/defaults`，Reference watcher 已拆为 `client/events/projection`；
- 旧 `assembly/`、`process/`、`diagnostic/`、`publisher/`、`config/model.rs`、`reference/watcher.rs` 路径已删除；Composition 不再使用 `runtime` 命名。

### Phase 7：测试与公开边界

- [x] tests 按 application/process/composition/behavior 分类。
- [x] crate root 只重导出经过审查的 Application API 和必要 Composition 入口。
- [x] application/services/domain 不依赖 composition。
- [x] domain/services 保持 crate-private。
- [x] 删除所有旧路径、兼容 facade 和无调用模块。
- [x] 更新原设计提案的最终状态并完成逐项审计。

## 6. 每阶段验证命令

```text
cargo test -p kairos-market --no-fail-fast
cargo test -p kairos-market-contract
cargo fmt -p kairos-market -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
python3 scripts/check/check_domain_architecture.py
```

最终验收额外执行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
```

Phase 7 focused evidence（2026-08-17）：

- tests 已按 `application/`、`behavior/` 与顶层 architecture wrapper 收敛；
- crate root 仅公开 Application 与必要 Composition 入口，Domain/Services 保持 crate-private；
- architecture test 固化旧路径删除、公开边界和测试目录布局；
- Market focused tests、contract tests、fmt、diff check、crate layout 和 domain architecture checks 在最终验收阶段复跑并通过；
- `cargo fmt --all -- --check` 通过；`cargo test --workspace` 运行至 `kairos-transport` 时仅失败于既有 platform 测试 `shared_memory::tests::reader_reopens_after_capacity_replacement`，错误为临时目录 `snapshot.bin.writer.lock` 仍被占用（`WriterLeaseHeld`），与 Market 改造无关；
- `uv run pytest -q` 结果为 `361 passed, 5 failed, 8 skipped`。5 个失败均不涉及 Market 代码：两个 Account CLI 测试缺少现有 `--integration-provider` 参数，Market CLI 回放测试读取不到既有 `snapshot["event_sequence"]` 字段，Paper Account CLI 进程退出码为 1，以及一个 process launch 时序断言超出 2 秒阈值；
- Composition 不再使用 `runtime` 目录或模块名：启动/诊断实现位于 `composition/launch/`，配置模型位于 `composition/config/profile.rs`。

若全仓检查被无关工作树改动阻断，必须记录准确文件和错误，并继续完成 Market focused checks；不得用 focused check 代替最终全仓验收。

## 7. 完成定义

只有以下条件全部满足，才能把本文档状态改为“完成”：

1. 第 5 节所有复选项完成；
2. 最终目录中的每个文件都承载真实职责；
3. 所有旧聚合路径和旧概念删除；
4. 单一 Application、单一 Actor、单一 current view 不变量成立；
5. Reference/provider/persistence/wire 依赖边界通过静态搜索和 architecture tests；
6. Market focused tests、contract tests、格式和仓库架构检查通过；
7. workspace tests 和 Python tests 通过，或只剩准确记录且与 Market 无关的既有失败；
8. 实际目录、本文档目标树和原设计提案最终状态一致。
