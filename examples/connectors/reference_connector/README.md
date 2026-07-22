# Connector Contract Reference

这个目录展示 connector 的语言边界，而不是复制 Python 领域模型。

输入是 `contract_vectors.json` 中的 Venue Raw Frame，输出是符合 `canonical_event.schema.json` 的 JSONL Canonical Event。Python 和未来 Rust gateway 必须通过同一 verifier。

验证 Python reference：

```bash
uv run python examples/connectors/reference_connector/verify_contract.py
```

验证未来 Rust binary：

```bash
uv run python examples/connectors/reference_connector/verify_contract.py \
  --command './target/release/kairospy-binance-gateway --contract-vectors examples/connectors/reference_connector/contract_vectors.json'
```

接入约束：

- Raw payload 不得进入 Strategy；
- message ID 对相同事实必须稳定；
- 时间必须带 UTC offset；
- source/receive/canonical sequence 不得混用；
- gap、reconnect 和 ignored frame 必须有结构化证据；
- stdout 只输出 Canonical JSONL，诊断写 stderr；
- Rust gateway 必须能被 Python reference connector 替换，上层 Strategy/Projection 不改代码。
