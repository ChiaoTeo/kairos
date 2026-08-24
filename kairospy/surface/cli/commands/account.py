"""Transparent shell for the canonical Rust Account CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence
import json

import typer

from kairospy.investment.apps.account.application.cli import AccountCliApplication
from kairospy.investment.apps.account.application import (
    AccountConfigurationApplication,
    CredentialApplication,
)
from kairospy.system.apps.configuration.application import ConfigurationReferenceApplication
from kairospy.system.apps.workspace.application import WorkspaceApplication


HELP = """Account standalone commands are owned by kairos-account-cli.

Canonical commands include:
  list, show, overview, assets, positions, earn-holdings, open-orders, fees
  connect, register, modify, simulate, schemas, schema, doctor
  setup (guided paper/live account configuration using SecretRefs)
  test ACCOUNT_ID (user-triggered connection and permission verification)
  credential-list, credential-create, credential-show, credential-delete

Top-level Account queries always use standalone/direct mode and never require
a launch instance. Connected Account commands exist only under a launch context.

"""

CONNECTED_COMMANDS = {
    "fill",
    "refresh",
    "reconcile",
}
ACCOUNT_QUERIES = {
    "assets",
    "overview",
    "summary",
    "snapshot",
    "current",
    "balances",
    "balance",
    "positions",
    "earn-holdings",
    "earn",
    "open-orders",
    "observed-orders",
    "fees",
}


def _leading_account_selector(arguments: list[str]) -> tuple[list[str], list[str]]:
    """Keep Account's invocation selector ahead of the Rust execution mode."""
    if not arguments:
        return [], arguments
    if arguments[0] == "--account-id":
        if len(arguments) < 2:
            raise typer.BadParameter("--account-id requires a value")
        return arguments[:2], arguments[2:]
    if arguments[0].startswith("--account-id="):
        return arguments[:1], arguments[1:]
    return [], arguments


def _query_account(
    selector: list[str], arguments: list[str]
) -> tuple[str | None, list[str]]:
    account_id = (
        selector[1]
        if len(selector) == 2
        else selector[0].split("=", 1)[1]
        if selector and selector[0].startswith("--account-id=")
        else None
    )
    if not arguments or arguments[0] not in ACCOUNT_QUERIES:
        return account_id, arguments
    values = list(arguments)
    if len(values) >= 3 and values[1] == "--account-id":
        account_id = values[2]
        del values[1:3]
    elif len(values) >= 2 and values[1].startswith("--account-id="):
        account_id = values[1].split("=", 1)[1]
        del values[1]
    elif len(values) >= 2 and not values[1].startswith("-"):
        account_id = values.pop(1)
    return account_id, values


def _workspace_and_arguments(argv: Sequence[str]) -> tuple[Path | None, list[str]]:
    values: list[str] = []
    result: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--workspace":
            if index + 1 >= len(argv):
                raise typer.BadParameter("--workspace requires a value")
            values.append(argv[index + 1])
            index += 2
            continue
        if item.startswith("--workspace="):
            values.append(item.split("=", 1)[1])
            index += 1
            continue
        result.append(item)
        index += 1
    if len(set(values)) > 1:
        raise typer.BadParameter("--workspace may be specified only once")
    return (Path(values[0]) if values else None), result


def account_passthrough(ctx: typer.Context) -> None:
    workspace, arguments = _workspace_and_arguments(ctx.args)
    if not arguments or arguments == ["--help"] or arguments == ["-h"]:
        typer.echo(HELP.rstrip(), nl=False)
        return
    account_selector, arguments = _leading_account_selector(arguments)
    if len(arguments) >= 2 and arguments[0] == "query":
        query = arguments[1]
        if query not in ACCOUNT_QUERIES:
            raise typer.BadParameter(f"unsupported Account query: {query}")
        arguments = [query, *arguments[2:]]
    if arguments and arguments[0] in {"standalone", "connected"}:
        explicit_mode = arguments.pop(0)
    else:
        explicit_mode = "standalone"
    owner = WorkspaceApplication().resolve(workspace)
    if explicit_mode == "connected":
        raise typer.BadParameter(
            "connected Account commands are available only under "
            "`kairos launch instance component account ...`"
        )
    if arguments and arguments[0] == "setup":
        from kairospy.surface.workbench import KairosWorkbenchApp, load_workbench_state

        state = load_workbench_state(Path(owner.paths.root))
        KairosWorkbenchApp(state, initial_section="resources").run()
        return
    if arguments and arguments[0] == "test":
        values = [
            value
            for value in arguments[1:]
            if value not in {"--format", "text", "json", "table"}
        ]
        if len(values) != 1 or values[0].startswith("-"):
            raise typer.BadParameter("account test requires exactly one account id")
        result = AccountConfigurationApplication(owner).test_connection(values[0])
        typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    if arguments and arguments[0] == "remove":
        account_id = _option_value([*account_selector, *arguments[1:]], "--account-id")
        if not account_id:
            raise typer.BadParameter("account remove requires --account-id")
        force = "--force" in arguments[1:]
        references = ConfigurationReferenceApplication(owner).account_references(
            account_id
        )
        if references and not force:
            locations = ", ".join(
                f"{item['source']}:{item['location']}" for item in references
            )
            raise typer.BadParameter(
                f"account is referenced by Launch configuration: {locations}; "
                "replace those references or use --force"
            )
        result = AccountConfigurationApplication(owner).delete(account_id, force=force)
        typer.echo(
            json.dumps(
                {**result, "references": references, "forced": force},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if arguments and arguments[0] == "credential-delete":
        credential_id = _option_value(arguments[1:], "--credential-id")
        if not credential_id:
            raise typer.BadParameter("credential-delete requires --credential-id")
        force = "--force" in arguments[1:]
        references = ConfigurationReferenceApplication(owner).credential_references(
            credential_id
        )
        if references and not force:
            locations = ", ".join(
                f"{item['source']}:{item['location']}" for item in references
            )
            raise typer.BadParameter(
                f"credential is referenced by configuration: {locations}; "
                "replace those references or use --force"
            )
        result = CredentialApplication(owner).delete(credential_id, force=force)
        owner.paths.child(
            "state", "configuration", "models", f"{credential_id}.json"
        ).unlink(missing_ok=True)
        typer.echo(
            json.dumps(
                {**result, "references": references, "forced": force},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    if arguments and arguments[0] in CONNECTED_COMMANDS:
        command = arguments[0]
        raise typer.BadParameter(
            f"`kairos account {command}` is a connected runtime command. "
            "Use `kairos launch instance component account ...` for a "
            "running launch-scoped Account component."
        )
    account_id, arguments = _query_account(account_selector, arguments)
    if arguments and arguments[0] in ACCOUNT_QUERIES:
        if not account_id:
            raise typer.BadParameter("account query requires an account id")
        if not account_selector:
            account_selector = ["--account-id", account_id]
    result = AccountCliApplication(owner).invoke(
        [*account_selector, explicit_mode, *(arguments or ["--help"])]
    )
    output = result.stdout if result.returncode == 0 else result.stderr or result.stdout
    if output:
        typer.echo(output.rstrip())
    if result.returncode:
        raise typer.Exit(result.returncode)


def _option_value(arguments: list[str], name: str) -> str | None:
    for index, item in enumerate(arguments):
        if item == name:
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if item.startswith(name + "="):
            return item.split("=", 1)[1]
    return None



__all__ = ["HELP", "account_passthrough"]
