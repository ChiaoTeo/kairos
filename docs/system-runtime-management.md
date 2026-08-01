# System Runtime Management Design

This document describes the intended management model for the built-in KairosPy system runtime. It focuses on the near-term design needed to diagnose heartbeat timeouts, identify unmanaged system processes, and restart the system safely without introducing an over-broad recovery mechanism.

## Problem

The built-in system runtime is long-lived and owns operational responsibilities such as account views, command handling, and trade-lock coordination. When it stops heartbeating or fails, the CLI must be able to answer:

- Which system instance is the current managed instance?
- Is the recorded process still alive?
- Is the heartbeat fresh?
- Are there extra system processes outside the registry?
- Can the current instance be restarted without accidentally killing unrelated processes?

The management surface must not route diagnostic commands into the strategy/runtime command channel. Commands such as `status`, `health`, and `inspect` are control-plane operations and must remain read-only.

## Design Goals

- Keep runtime management separate from business commands.
- Persist enough process identity to diagnose stale and unmanaged instances.
- Provide a read-only inspection path before adding repair behavior.
- Make restart behavior conservative and auditable.
- Avoid broad process scans that can kill unrelated KairosPy commands.
- Defer a full `recover` command until the project needs multi-instance repair semantics.

## Non-Goals

- No automatic global cleanup of every `kairospy` process.
- No default `SIGKILL` behavior.
- No repair plan engine in the first iteration.
- No historical state rewriting beyond marking the current stale instance when explicitly requested.
- No replacement for an external process supervisor such as launchd, systemd, or a container orchestrator.

## Management Model

The system runtime should be treated as three layers:

- **Runtime**: the actual long-lived process that runs `kairospy system up --foreground`.
- **Registry**: launch artifacts under `.kairos/launches`, especially `current.json`, `state.json`, `summary.json`, logs, and events.
- **Management CLI**: commands such as `system status`, `system inspect`, `system restart`, and `system attach`.

The runtime reports lifecycle state through artifacts. The CLI reads those artifacts and may submit explicit runtime commands, but diagnostic commands should never be forwarded as arbitrary runtime commands.

## Process Identity

The runtime should persist process identity in `state.json`. PID is useful but must not be treated as a complete identity because operating systems can reuse PIDs.

Recommended identity fields:

```json
{
  "identity": {
    "launch_id": "kairos-system",
    "launch_instance_id": "ef8ddead-4aa2-4b3b-99cd-89f13289ded4",
    "pid": 68843,
    "ppid": 1,
    "host": "MacBook-Pro-2.local",
    "cwd": "/Users/zhaoqian/Code/Github/trader",
    "root": "/Users/zhaoqian/Code/Github/trader/.kairos/launches",
    "argv": [
      "python",
      "-m",
      "kairospy",
      "launch",
      "system",
      "up",
      "--foreground",
      "--root",
      "/Users/zhaoqian/Code/Github/trader/.kairos/launches",
      "--launch-id",
      "kairos-system"
    ],
    "started_at": "2026-08-01T09:15:06.141935+00:00"
  }
}
```

Near-term minimum:

- Continue persisting `identity.pid`.
- Expose PID in `system status --format json`.
- Add `argv`, `cwd`, and `root` when practical so process checks can validate more than PID.

## State Model

Keep lifecycle phase separate from externally diagnosed health.

Lifecycle phase is reported by the runtime:

- `starting`
- `running`
- `stopping`
- `stopped`
- `failed`
- `abandoned`

Health is diagnosed by the management layer:

- `healthy`: phase is active, heartbeat is fresh, and PID matches a live process.
- `stale`: phase is active but heartbeat is old or missing.
- `dead`: registry says active but PID is not alive.
- `orphaned`: a matching system process exists but is not the registry current instance.
- `conflicted`: more than one matching system process exists for the same launch root and launch id.
- `unknown`: insufficient data to classify.

`phase` answers what the runtime last reported. `health` answers what the supervisor can verify now.

## Command Surface

Recommended near-term command surface:

```bash
kairospy system status
kairospy system inspect
kairospy system restart
kairospy system restart --clean-stale
kairospy system attach
```

### `system status`

Fast human-readable status backed by the registry. JSON output should include process identity fields where available.

Expected behavior:

- Read registry records.
- Show current phase, status, heartbeat age, launch instance id, PID, and log path.
- No process cleanup.
- No runtime command submission.

### `system inspect`

Read-only deep diagnosis. This is the most important next step because it makes failures explainable before adding repair behavior.

Expected checks:

- Read `current.json`.
- Read current instance `state.json`.
- Validate heartbeat freshness.
- Check whether `identity.pid` is alive.
- Scan process table for matching system processes.
- Classify current health.
- Report unmanaged or duplicate matching processes.

Process matching must be narrow. A process should be considered a KairosPy system process only if its command line matches the current workspace/root and launch id, for example:

```text
contains: -m kairospy system up --foreground
contains: --root <current launch root>
contains: --launch-id kairos-system
pid != current CLI process
```

Avoid broad matching such as killing every process containing `kairospy`.

### `system restart`

Registry-trusting restart.

Expected behavior:

- If the registry current instance is active and healthy enough to accept commands, submit `runtime.stop`.
- Wait for it to become inactive within the timeout.
- Start a new system instance.
- Return the stopped command file and new instance identity.

This command should not scan the whole OS process table or kill unrelated processes.

### `system restart --clean-stale`

Conservative repair for the current registry instance only.

Expected behavior:

- Inspect the current registry instance.
- If it is active with a fresh heartbeat, use normal graceful restart.
- If it is stale and `identity.pid` is alive, verify that the PID command line matches this exact workspace/root and `kairos-system`.
- Send `SIGTERM` to that verified current PID.
- Wait for exit.
- Start a new system instance.

This option should not clean arbitrary orphaned processes. It only handles the current registry-owned stale process.

## Deferred Full Recover

A full `system recover` command is intentionally deferred. It is useful later, but it is broader than the current need.

A future full recovery command could:

- Build a repair plan from registry state and process table state.
- Stop the current managed instance.
- Terminate verified orphaned system processes.
- Mark dead active records as abandoned.
- Start a single new system instance.
- Emit a structured repair report.

For now, this is over-designed. The project should first ship `inspect` and `restart --clean-stale`.

## Attach Behavior

`system attach` is an interactive control-plane shell attached to the current runtime's logs and command channel. It should keep management commands local:

```text
status      -> read registry/state locally
inspect     -> read registry/state/process table locally
help        -> local help
logs        -> local log tail or pointer
command ... -> explicitly submit runtime command
trade-*     -> explicitly submit account command
stop        -> submit runtime.stop
```

Unknown attach commands should return an attach-shell error. They should not be forwarded as arbitrary runtime commands by default.

## Failure Examples

### Stale heartbeat, PID dead

Registry says:

```text
phase=running
heartbeat_age=120s
pid=12345
```

Process table says PID 12345 is not alive.

Diagnosis:

```text
health=dead
action=restart
```

### Stale heartbeat, PID alive and verified

Registry says active, heartbeat is stale, and PID still exists. The PID command line matches this workspace and launch id.

Diagnosis:

```text
health=stale
action=restart --clean-stale
```

### Extra system process

Process table contains another `kairospy system up --foreground --root <same root> --launch-id kairos-system` that is not the registry current PID.

Diagnosis:

```text
health=conflicted
action=manual review for now
```

Near-term behavior should report this in `system inspect`, not kill it automatically.

## Implementation Order

1. Expose PID and identity fields in launch record JSON output.
2. Add `system inspect` as a read-only command.
3. Add narrow process-table matching for system runtime processes.
4. Add `system restart --clean-stale`.
5. Keep system runtime commands only under the top-level `system` product surface.
6. Keep full `system recover` deferred until inspect output shows recurring orphan/conflict cases.

## Acceptance Criteria

- `system status --format json` includes `pid` when available.
- `system inspect --format json` can classify `healthy`, `stale`, `dead`, `orphaned`, or `conflicted`.
- `system inspect` has no side effects.
- `system restart` continues to perform graceful registry-based restart.
- `system restart --clean-stale` only terminates the registry current PID after validating argv/root/launch id.
- Attach `status` and `inspect` do not enter the runtime command channel.
- Unknown attach commands do not fail the system runtime.
