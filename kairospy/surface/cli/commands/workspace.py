from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kairospy.infrastructure.storage.codec import to_primitive


def workspace_command(args: argparse.Namespace) -> int:
    from kairospy.workspace import Workspace, WorkspaceRepository

    if args.action == "list":
        repository = WorkspaceRepository.discover(Path.cwd())
        workspaces = repository.list()
        payload = {
            "product": "workspace",
            "operation": "list",
            "workspace_count": len(workspaces),
            "workspaces": [item.manifest.to_dict() for item in workspaces],
        }
        emit_workspace_payload(args, payload)
        return 0
    if args.action == "create":
        workspace = Workspace.open_or_create(args.name, start=Path.cwd())
        payload = {
            "product": "workspace",
            "operation": "create",
            "workspace": workspace.name,
            "root": str(workspace.root),
        }
        emit_workspace_payload(args, payload)
        return 0
    if args.action in {"attach", "add"}:
        workspace = Workspace.open_or_create(args.workspace, start=Path.cwd())
        stream = getattr(args, "stream", None) or getattr(args, "stream_arg", None)
        dataset = getattr(args, "dataset", None)
        if stream is None and dataset is None:
            raise SystemExit("workspace attach requires a Data Stream or --dataset")
        name = args.name or str(stream or dataset)
        if stream is not None and dataset is None:
            if "{space}" in str(stream):
                dataset = str(stream)
            else:
                from kairospy.data import DataStreamResolver
                from kairospy.integrations.data_products.resolver import DataProductResolver

                stream_ref = DataStreamResolver(workspace.data_root).resolve(stream)
                plan = DataProductResolver().resolve(stream)
                dataset = str(plan.dataset_id if plan.source != "stream" else stream_ref.dataset_id)
        attachment = workspace.attach(
            name,
            dataset=dataset,
            stream=stream,
            view=args.view,
            instruments=tuple(args.instrument),
            fields=tuple(args.field),
            freshness_seconds=args.freshness_seconds,
        )
        payload = {
            "product": "workspace",
            "operation": "attach",
            "workspace": workspace.name,
            "attachment": attachment.to_dict(),
        }
        emit_workspace_payload(args, payload)
        return 0
    if args.action == "inspect":
        workspace = Workspace.open_or_create(args.name, start=Path.cwd())
        payload = {
            "product": "workspace",
            "operation": "inspect",
            "workspace": workspace.name,
            "attachments": {
                name: attachment.to_dict()
                for name, attachment in workspace.attachments.bindings.items()
            },
        }
        emit_workspace_payload(args, payload)
        return 0
    if args.action == "inspect-code":
        payload = workspace_inspect_code(
            args.entrypoint,
            tuple(args.param),
            mode=args.mode,
        )
        emit_workspace_payload(args, payload)
        return 0
    raise SystemExit(f"unsupported workspace action: {args.action}")


def workspace_inspect_code(entrypoint_ref: str, params_values: tuple[str, ...], *, mode: str = "inspect") -> dict[str, object]:
    from kairospy.infrastructure.configuration import KairosProjectConfig
    from kairospy.surface import product as product_surface
    from kairospy.workspace import WorkspaceBuildContext

    params = {}
    for value in params_values:
        if "=" not in value:
            raise SystemExit(f"workspace parameter must be key=value: {value}")
        key, raw = value.split("=", 1)
        params[key] = raw
    config = KairosProjectConfig.discover(Path.cwd())
    _module, _callable_name, entrypoint = product_surface._load_run_entrypoint(entrypoint_ref, config.root)
    context = WorkspaceBuildContext(
        project_root=config.root,
        data_root=config.relative_path("paths.lake_root", ".kairos/data"),
    )
    projection = entrypoint(context, params)
    if projection is None:
        projection = context.project()
    if not hasattr(projection, "to_dict"):
        raise ValueError(f"workspace entrypoint must return WorkspaceProjection: {entrypoint_ref}")
    return {
        "product": "workspace",
        "operation": "inspect-code",
        "entrypoint": entrypoint_ref,
        "params": params,
        "projection": projection.to_dict(),
        "nodes": [node.to_dict() for node in projection.nodes],
        "preflight": projection.preflight(mode),
    }




def emit_workspace_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    from kairospy.surface.cli.rendering.workspace import emit_workspace_payload as emit

    emit(args, payload)
