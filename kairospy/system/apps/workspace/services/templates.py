"""Versioned, user-owned project starters at the Workspace boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any, Mapping, Sequence

from kairospy.system.domain.workspace import Workspace


BACKTEST_TEMPLATE = "backtest-basic"
_TEMPLATE_ALIASES = {"backtest": BACKTEST_TEMPLATE}
_TOKEN = re.compile(r"{{([a-z][a-z0-9_]*)}}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PYTHON_PACKAGE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)
_ASSET_ROOT = Path(__file__).resolve().parents[1] / "template_assets"


@dataclass(frozen=True, slots=True)
class TemplateParameter:
    name: str
    description: str
    kind: str
    required: bool
    default: str | None


@dataclass(frozen=True, slots=True)
class TemplateFile:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class TemplateRequirement:
    kind: str
    parameter: str
    capability: str | None
    products: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectTemplate:
    template_id: str
    version: str
    title: str
    description: str
    parameters: tuple[TemplateParameter, ...]
    files: tuple[TemplateFile, ...]
    requirements: tuple[TemplateRequirement, ...]

    def public(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "parameters": [
                {
                    "name": value.name,
                    "description": value.description,
                    "kind": value.kind,
                    "required": value.required,
                    "default": value.default,
                }
                for value in self.parameters
            ],
            "requirements": [
                {
                    "kind": value.kind,
                    "parameter": value.parameter,
                    "capability": value.capability,
                    "products": list(value.products),
                }
                for value in self.requirements
            ],
        }


def _canonical_template_id(template: str) -> str:
    value = template.strip().lower()
    return _TEMPLATE_ALIASES.get(value, value)


def _manifest_paths() -> tuple[Path, ...]:
    return tuple(sorted(_ASSET_ROOT.glob("*/template.toml")))


def _load_template(path: Path) -> ProjectTemplate:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    header = _mapping(raw.get("template"), "template")
    parameters = tuple(
        TemplateParameter(
            name=_text(value.get("name"), "parameter.name"),
            description=_text(value.get("description"), "parameter.description"),
            kind=_text(value.get("kind"), "parameter.kind"),
            required=bool(value.get("required", False)),
            default=(
                None if value.get("default") is None else str(value.get("default"))
            ),
        )
        for value in _tables(raw.get("parameters"), "parameters")
    )
    files = tuple(
        TemplateFile(
            source=_text(value.get("source"), "file.source"),
            target=_text(value.get("target"), "file.target"),
        )
        for value in _tables(raw.get("files"), "files")
    )
    requirements = tuple(
        TemplateRequirement(
            kind=_text(value.get("kind"), "requirement.kind"),
            parameter=_text(value.get("parameter"), "requirement.parameter"),
            capability=(
                None
                if value.get("capability") is None
                else _text(value.get("capability"), "requirement.capability")
            ),
            products=tuple(str(item) for item in value.get("products", ())),
        )
        for value in _tables(raw.get("requirements", ()), "requirements")
    )
    template = ProjectTemplate(
        template_id=_text(header.get("id"), "template.id"),
        version=_text(header.get("version"), "template.version"),
        title=_text(header.get("title"), "template.title"),
        description=_text(header.get("description"), "template.description"),
        parameters=parameters,
        files=files,
        requirements=requirements,
    )
    if template.template_id != path.parent.name:
        raise ValueError(f"template id does not match directory: {path}")
    names = [value.name for value in template.parameters]
    if len(names) != len(set(names)):
        raise ValueError(f"template has duplicate parameters: {template.template_id}")
    if not template.files:
        raise ValueError(f"template has no files: {template.template_id}")
    if any(value.parameter not in names for value in template.requirements):
        raise ValueError(f"template requirement references an unknown parameter: {path}")
    return template


def _catalog() -> dict[str, ProjectTemplate]:
    templates = [_load_template(path) for path in _manifest_paths()]
    result = {value.template_id: value for value in templates}
    if len(result) != len(templates):
        raise ValueError("template catalog contains duplicate ids")
    return result


def validate_project_template(template: str | None) -> str | None:
    if template is None:
        return None
    value = _canonical_template_id(template)
    if value not in _catalog():
        supported = ", ".join(sorted(_catalog()))
        raise ValueError(
            f"unknown project template {template!r}; choose one of: {supported}"
        )
    return value


def list_project_templates() -> list[dict[str, object]]:
    return [value.public() for value in _catalog().values()]


def show_project_template(template: str) -> dict[str, object]:
    return _template(template).public()


def project_template_paths(project: Path, template: str | None) -> tuple[Path, ...]:
    template_id = validate_project_template(template)
    if template_id is None:
        return ()
    definition = _catalog()[template_id]
    values = _resolve_parameters(definition, {})
    return tuple(
        path
        for path in _targets(project, definition, values)
        if ".kairos" not in path.relative_to(project.resolve()).parts
    )


def install_project_template(
    workspace: Workspace,
    template: str | None,
    *,
    installation_id: str | None = None,
    parameters: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    template_id = validate_project_template(template)
    if template_id is None:
        return ()
    definition = _catalog()[template_id]
    values = _resolve_parameters(definition, parameters or {})
    name = _safe_id(installation_id or values.get("launch_id") or template_id)
    _verify_requirements(workspace, definition, values)
    targets = _targets(workspace.paths.project_root, definition, values)
    record = _installation_path(workspace, name)
    conflicts = [path for path in (*targets, record) if path.exists()]
    if conflicts:
        joined = ", ".join(str(path) for path in conflicts)
        raise FileExistsError(f"project template would overwrite existing files: {joined}")

    rendered = tuple(
        (
            target,
            _render(
                (_ASSET_ROOT / definition.template_id / item.source).read_text(
                    encoding="utf-8"
                ),
                values,
            ),
        )
        for item, target in zip(definition.files, targets, strict=True)
    )
    created: list[Path] = []
    try:
        for target, content in rendered:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(target)
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(
            _installation_document(
                workspace.paths.project_root,
                name,
                definition,
                {
                    parameter.name: values[parameter.name]
                    for parameter in definition.parameters
                    if parameter.name in values
                },
                rendered,
            ),
            encoding="utf-8",
        )
        created.append(record)
        if definition.template_id == BACKTEST_TEMPLATE:
            _install_replay_profile(workspace)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return tuple(target for target, _content in rendered)


def project_template_status(
    workspace: Workspace, installation_id: str | None = None
) -> list[dict[str, object]]:
    root = workspace.paths.child("config", "templates")
    paths = (
        (root / f"{_safe_id(installation_id)}.toml",)
        if installation_id is not None
        else tuple(sorted(root.glob("*.toml"))) if root.is_dir() else ()
    )
    result: list[dict[str, object]] = []
    for path in paths:
        if not path.is_file():
            raise KeyError(f"template installation does not exist: {path.stem}")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        installation = _mapping(raw.get("installation"), "installation")
        files: list[dict[str, str]] = []
        for value in _tables(raw.get("files"), "files"):
            relative = _text(value.get("path"), "files.path")
            target = _project_child(workspace.paths.project_root, relative)
            expected = _text(value.get("sha256"), "files.sha256")
            status = (
                "missing"
                if not target.is_file()
                else "current"
                if _sha256(target.read_bytes()) == expected
                else "customized"
            )
            files.append({"path": relative, "status": status})
        available = _catalog().get(str(installation.get("template_id")))
        result.append(
            {
                "installation_id": path.stem,
                "template_id": installation.get("template_id"),
                "installed_version": installation.get("template_version"),
                "available_version": available.version if available else None,
                "update_available": bool(
                    available
                    and available.version != installation.get("template_version")
                ),
                "files": files,
                "status": (
                    "missing"
                    if any(value["status"] == "missing" for value in files)
                    else "customized"
                    if any(value["status"] == "customized" for value in files)
                    else "current"
                ),
            }
        )
    return result


def _template(template: str) -> ProjectTemplate:
    template_id = validate_project_template(template)
    assert template_id is not None
    return _catalog()[template_id]


def _resolve_parameters(
    template: ProjectTemplate, supplied: Mapping[str, str]
) -> dict[str, str]:
    definitions = {value.name: value for value in template.parameters}
    unknown = sorted(set(supplied) - set(definitions))
    if unknown:
        raise ValueError(f"unknown template parameters: {', '.join(unknown)}")
    result: dict[str, str] = {}
    for name, definition in definitions.items():
        raw = supplied.get(name, definition.default)
        if raw is None or not str(raw).strip():
            if definition.required:
                raise ValueError(f"template parameter is required: {name}")
            continue
        value = str(raw).strip()
        if definition.kind == "id" and _ID.fullmatch(value) is None:
            raise ValueError(f"template parameter must be a resource id: {name}")
        if definition.kind == "python-package" and _PYTHON_PACKAGE.fullmatch(value) is None:
            raise ValueError(f"template parameter must be a Python package name: {name}")
        if definition.kind not in {"id", "python-package", "text"}:
            raise ValueError(f"unsupported template parameter kind: {definition.kind}")
        result[name] = value
    for definition in template.parameters:
        if definition.kind == "python-package" and definition.name in result:
            result[f"{definition.name}_path"] = result[definition.name].replace(
                ".", "/"
            )
    return result


def _verify_requirements(
    workspace: Workspace,
    template: ProjectTemplate,
    parameters: Mapping[str, str],
) -> None:
    # Imported lazily because Integration's process composition also resolves
    # Workspace paths during module initialization.
    from kairospy.system.apps.integration.application import (
        ProviderConnectionConfigurationApplication,
    )

    providers = ProviderConnectionConfigurationApplication(workspace)
    for requirement in template.requirements:
        if requirement.kind != "provider-connection":
            raise ValueError(f"unsupported template requirement: {requirement.kind}")
        connection_id = parameters[requirement.parameter]
        try:
            connection = providers.show(connection_id)
        except KeyError as error:
            raise ValueError(
                f"template requires an existing provider connection: {connection_id}"
            ) from error
        if connection.get("verification_status") != "verified":
            raise ValueError(
                f"template requires a verified provider connection: {connection_id}"
            )
        capabilities = set(_string_values(connection.get("capabilities_verified")))
        if requirement.capability and requirement.capability not in capabilities:
            raise ValueError(
                f"provider connection has not verified {requirement.capability}: {connection_id}"
            )
        products = set(_string_values(connection.get("products")))
        missing = sorted(set(requirement.products) - products)
        if missing:
            raise ValueError(
                f"provider connection does not provide {', '.join(missing)}: {connection_id}"
            )


def _targets(
    project: Path, template: ProjectTemplate, parameters: Mapping[str, str]
) -> tuple[Path, ...]:
    return tuple(
        _project_child(project, _render(value.target, parameters))
        for value in template.files
    )


def _project_child(project: Path, relative: str) -> Path:
    path = (project / relative).resolve()
    try:
        path.relative_to(project.resolve())
    except ValueError as error:
        raise ValueError(f"template path escapes project root: {relative}") from error
    return path


def _render(content: str, parameters: Mapping[str, str]) -> str:
    names = set(_TOKEN.findall(content))
    missing = sorted(names - set(parameters))
    if missing:
        raise ValueError(f"template values are missing: {', '.join(missing)}")
    return _TOKEN.sub(lambda match: parameters[match.group(1)], content)


def _installation_path(workspace: Workspace, installation_id: str) -> Path:
    return workspace.paths.child("config", "templates", f"{installation_id}.toml")


def _installation_document(
    project_root: Path,
    installation_id: str,
    template: ProjectTemplate,
    parameters: Mapping[str, str],
    rendered: Sequence[tuple[Path, str]],
) -> str:
    lines = [
        "[installation]",
        f"id = {_quote(installation_id)}",
        f"template_id = {_quote(template.template_id)}",
        f"template_version = {_quote(template.version)}",
        f"installed_at = {_quote(datetime.now(timezone.utc).isoformat())}",
        "",
        "[parameters]",
    ]
    lines.extend(f"{key} = {_quote(value)}" for key, value in sorted(parameters.items()))
    for path, content in rendered:
        lines.extend(
            (
                "",
                "[[files]]",
                f"path = {_quote(path.relative_to(project_root).as_posix())}",
                f"sha256 = {_quote(_sha256(content.encode()))}",
            )
        )
    return "\n".join(lines) + "\n"


def _install_replay_profile(workspace: Workspace) -> None:
    path = workspace.paths.manifest
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    market = raw.get("market")
    if isinstance(market, Mapping):
        profiles = market.get("profiles")
        if isinstance(profiles, Mapping) and "replay" in profiles:
            replay = profiles["replay"]
            if (
                isinstance(replay, Mapping)
                and replay.get("scope") == "replay"
                and isinstance(replay.get("replay"), Mapping)
            ):
                return
            raise ValueError("existing replay market profile is incompatible")
    content = path.read_text(encoding="utf-8") + (
        "\n[market.profiles.replay]\n"
        'scope = "replay"\n\n'
        "[market.profiles.replay.replay]\n"
        'clock = "maximum"\n'
        "speed_multiplier = 1\n"
        "start_paused = true\n"
    )
    _replace_manifest(path, content)


def _replace_manifest(path: Path, content: str) -> None:
    mode = path.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _safe_id(value: str) -> str:
    if _ID.fullmatch(value) is None:
        raise ValueError("installation id must be a single resource id")
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"template {name} must be a table")
    return value


def _tables(value: object, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"template {name} must be an array of tables")
    result: list[Mapping[str, Any]] = []
    for item in value:
        result.append(_mapping(item, name))
    return tuple(result)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"template {name} is required")
    return value.strip()


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


# Build the public catalog only after all manifest validation helpers exist.
SUPPORTED_PROJECT_TEMPLATES = frozenset({*_catalog(), *_TEMPLATE_ALIASES})
