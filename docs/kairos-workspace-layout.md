# Kairos workspace resource layout

Status: accepted migration target

Kairos treats the project workspace and every launch instance as resource
scopes. A scope is identified by its root directory and uses the same resource
categories:

```text
<scope>/
  config/       resolved or user-authored configuration
  data/         scope-owned immutable or materialized inputs
  state/        durable owner state and recovery journals
  snapshots/    derived, externally readable current views
  run/          ephemeral process resources
  logs/         diagnostic output
```

Directories are created on demand. A scope does not create empty categories
merely to look complete.

## Runtime resources

Runtime resources are component-first in every scope:

```text
run/<component>/
  control.sock
  process.lock
  health.json
```

The process lock file remains on disk after exit; the operating-system lock is
the ownership authority. Unix sockets may resolve to a stable short path under
`/tmp` when the logical path exceeds the platform limit.

## Workspace scope

The workspace scope is `<project>/.kairos`. `kairos.toml` is the only regular
file owned directly by that root. Workspace configuration lives below
`config/`; reusable datasets live below `data/`; business modules own their
durable state and snapshots below their named category directories.

Reference databases therefore live below `state/reference/`, execution order
journals below `state/execution/orders/`, and managed historical archives below
`state/workspace/archives/`. These are ownership paths within the shared scope
model, not additional top-level resource categories.

During migration, existing account and credential profiles remain readable
from their legacy top-level directories. New project initialization and all new
writes use:

```text
config/accounts/
config/credentials/
config/market/connections/
```

## Launch instance scope

The canonical instance root remains, during this migration:

```text
launches/<mode>/<launch-id>/instances/<instance-id>/
```

Whether `mode` can be removed from identity is a separate business decision;
the directory migration does not silently change launch identity semantics.
The instance root contains only `manifest.json` plus the common resource
categories. Important instance resources are:

```text
config/normalized.json
state/launch/status.json
state/launch/command.json
state/launch/lifecycle.jsonl
state/launch/run.sqlite
state/<component>/checkpoints/
snapshots/<component>/...
run/<component>/{control.sock,process.lock,health.json}
logs/<component>/...
```

Checkpoints are durable component state, not a separate top-level resource
category. Shared Workspace data is referenced by identity; data is copied into
an instance only when the instance owns a materialized input such as a replay.

## Ownership and compatibility

- Workspace/System owns scope identity and path construction.
- Business modules own the contents below their component directories.
- Path consumers must use the scope path API instead of joining private path
  literals to a workspace or instance root.
- New code writes only the canonical layout.
- Legacy paths may be read during a bounded migration, but compatibility reads
  must be removed after existing workspaces have been migrated.
- Backups and retention are maintenance operations, not additional resource
  categories. They belong outside the live scope or under an explicitly
  managed archive root.
