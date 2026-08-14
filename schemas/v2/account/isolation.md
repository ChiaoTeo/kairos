# Account v2 isolation contract

Account has five different identities. They must not be collapsed into one
`scope` or inferred from `account_id` or `source_id`.

| Identity | Example | Owner | Meaning |
| --- | --- | --- | --- |
| `workspace_id` | `workspace-demo` | Workspace/System | process, socket, credential, and resource boundary |
| `account_runtime_id` | `account:shared` or `account:launch-1:instance-1` | Account composition | one Account Actor and its event/view publishers |
| `account_id` | `account:primary` | Account | canonical business owner identity |
| `segment_key` | `binance-spot` | Account composition | one provider/product account segment |
| `source_id` | `binance.spot` | Integration/composition | provider binding and provenance, not isolation |

## Runtime profiles

### Workspace-shared Account

The shared Account runtime owns one canonical account state:

```text
account_runtime_id = account:shared
scope              = workspace
launch_id          = absent on shared Account facts
instance_id        = absent on shared Account facts
```

All configured segments for the same canonical account are represented inside
the one Actor. A current view containing multiple account IDs is invalid.

### Instance Account

Replay, deterministic backtest, or an intentionally isolated run uses an
instance-scoped runtime:

```text
account_runtime_id = account:<launch_id>:<instance_id>
scope              = instance
launch_id          = required
instance_id        = required
```

Its event stream, resource manifest, and KSS1 files are resolved below the
launch-instance resource root. A reader or event from another runtime is
rejected.

## Transport identity rules

- the component manifest selects `account_runtime_id`; callers do not build
  resources from `account_id` or `segment_key`;
- Account event sequence is scoped to the Account runtime publisher;
- an event carries the matching workspace/launch/instance identity in
  `EventMetadata`;
- a current-view resource must match workspace identity, resource manifest,
  resource ID, resource epoch, view key, and runtime identity;
- `account_id` and `segment_key` are business identities, not process or
  permission boundaries;
- `source_id` distinguishes provider provenance and routing but must not grant
  access to another Account runtime;
- `ObservedOrder` identity handling must include `source_id` because remote
  order IDs are not globally unique across providers.
