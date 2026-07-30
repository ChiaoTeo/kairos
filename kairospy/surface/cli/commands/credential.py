from __future__ import annotations

import typer

from kairospy.application.system.facade.credential import CredentialFacade
from kairospy.surface.cli.options import OutputFormat
from kairospy.surface.cli.output import write_cli_result


credential_app = typer.Typer(no_args_is_help=True, help="Credential commands")
_CREDENTIALS = CredentialFacade()


@credential_app.command("list")
def list_credentials(
    ctx: typer.Context,
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    payload = _CREDENTIALS.list_credentials()
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@credential_app.command("create")
def create_credential(
    credential_id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    kind: str | None = typer.Option(None, "--kind"),
    api_key: str | None = typer.Option(None, "--api-key"),
    api_secret: str | None = typer.Option(None, "--api-secret"),
    passphrase: str | None = typer.Option(None, "--passphrase"),
    password: str | None = typer.Option(None, "--password"),
    wallet_address: str | None = typer.Option(None, "--wallet-address"),
    private_key: str | None = typer.Option(None, "--private-key"),
    vault_address: str | None = typer.Option(None, "--vault-address"),
    field_values: list[str] | None = typer.Option(None, "--field", help="Extra credential field as key=value"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    try:
        typer.echo(
            _CREDENTIALS.create(
                credential_id=credential_id,
                provider=provider,
                kind=kind,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                password=password,
                wallet_address=wallet_address,
                private_key=private_key,
                vault_address=vault_address,
                field_values=field_values,
                force=force,
            )
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@credential_app.command("show")
def show_credential(
    ctx: typer.Context,
    credential_id: str = typer.Argument(...),
    reveal_secrets: bool = typer.Option(False, "--reveal-secrets"),
    output_format: OutputFormat | None = typer.Option(None, "--format"),
) -> None:
    try:
        payload = _CREDENTIALS.show(credential_id, reveal_secrets=reveal_secrets)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@credential_app.command("delete")
def delete_credential(credential_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    try:
        typer.echo(_CREDENTIALS.delete(credential_id, force=force))
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@credential_app.command("remove")
def remove_credential(credential_id: str = typer.Argument(...), force: bool = typer.Option(False, "--force")) -> None:
    delete_credential(credential_id=credential_id, force=force)


__all__ = ["credential_app"]
