# Market v2 isolation contract

Market has four different identities. They must not be collapsed into one
`scope` or inferred from `market_id`.

| Identity | Example | Owner | Meaning |
| --- | --- | --- | --- |
| `workspace_id` | `workspace-demo` | Workspace/System | Hard process, socket, credential, and resource boundary |
| `market_runtime_id` | `market:shared` or `market:launch-1:instance-1` | Market composition | One concrete Market Actor and its event/view publisher |
| `owner_id` | `strategy-1:instance-1` | Caller/Strategy | Subscription demand owner used for release and authorization |
| `source_id` | `binance.spot` | Market composition/Integration | Provenance and routing identity, not an isolation boundary |

## Runtime profiles

### Workspace-shared Market

Live and paper Market normally use one workspace-scoped runtime:

```text
market_runtime_id = market:shared
scope             = workspace
launch_id         = absent on shared Market facts
instance_id       = absent on shared Market facts
```

The shared Actor owns one canonical observation state. Each subscription still
contains the caller's `owner_id`, and releasing one owner must not remove any
other owner's demand.

### Instance Market

Replay, deterministic backtest, and an intentionally isolated paper run use an
instance-scoped runtime:

```text
market_runtime_id = market:<launch_id>:<instance_id>
scope             = instance
launch_id         = required
instance_id       = required
```

Its UDS socket, event stream, typed resource metadata, and mmap files are resolved
under that launch-instance resource root. A command or reader with a
different launch/instance identity is rejected.

## Transport identity rules

- The socket or typed component metadata selects the target `market_runtime_id`;
  callers do not construct snapshot paths from `market_id`.
- A Market event stream is named `market.events/<market_runtime_id>` and its
  sequence is scoped to that runtime publisher.
- A shared Market event has workspace identity but no launch/instance identity;
  owner identity remains in subscription lifecycle facts and control requests.
- An instance Market event carries the matching workspace, launch, and
  instance identity in `EventMetadata`.
- A current-view resource must match `workspace_id`, runtime metadata,
  `resource_id`, `resource_epoch`, and `view_key` before decoding rows.
- `market_id` is a canonical business identity; it is not a process or
  permission boundary.
- `source_id` distinguishes provenance and routing. It must not be used to
  grant a caller access to another Market runtime.

The Python entry point should receive a resolved runtime endpoint from
composition. It should not expose socket paths, mmap paths, or source routing
as user-level identity concepts.
