from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from kairospy.data.contracts import (
    DataProductContract,
    DataProductDefinition,
    DatasetKey,
    DatasetLayer,
    DatasetStorageKind,
    QualityLevel,
    SourceBinding,
)
from kairospy.integrations.extensions.external_process import (
    ExternalProcessDataProductBuilder,
    ExternalProcessProductBinding,
    command_tuple,
)


CONFIG_PATH_KEY = "__kairos_config_path__"


@dataclass(frozen=True, slots=True)
class ProviderExtensionContext:
    root: Path
    config_path: Path
    module_path: Path
    extension: Mapping[str, object]


def provider_extension_specs(config: str | Path | Mapping[str, Any]) -> tuple[DataProductContract, ...]:
    specs: list[DataProductContract] = []
    config_path = _config_base_path(config)
    for extension in _provider_extensions(config):
        if _extension_kind(extension) in {"external_process", "process"}:
            specs.extend(_external_process_product_specs(extension))
            continue
        module_path = _extension_module_path(config_path, extension)
        module = _load_extension_module(module_path)
        context = ProviderExtensionContext(Path(), Path(config_path), module_path, extension)
        specs.extend(_module_product_specs(module, context, extension))
    return tuple(specs)


def register_provider_extensions(root: str | Path, config: str | Path | Mapping[str, Any], registry: object) -> None:
    config_path = _config_base_path(config)
    for extension in _provider_extensions(config):
        if _extension_kind(extension) in {"external_process", "process"}:
            specs = _external_process_product_specs(extension)
            registry.register(
                ExternalProcessDataProductBuilder(
                    root,
                    tuple(_external_process_binding(config_path, extension, spec) for spec in specs),
                ),
                specs,
            )
            continue
        module_path = _extension_module_path(config_path, extension)
        module = _load_extension_module(module_path)
        context = ProviderExtensionContext(Path(root), Path(config_path), module_path, extension)
        specs = _module_product_specs(module, context, extension)
        register_name = str(extension.get("function") or "register")
        register = getattr(module, register_name, None)
        if callable(register):
            result = register(registry, context)
            _register_returned(registry, result, specs)
            continue
        connector_name = str(extension.get("connector_function") or "get_connector")
        factory = getattr(module, connector_name, None)
        if callable(factory):
            _register_returned(registry, factory(context), specs)
            continue
        if specs:
            continue
        raise ValueError(
            f"provider extension {module_path} must define {register_name}(registry, context), "
            f"{connector_name}(context), products(context), or PRODUCTS"
        )


def _module_product_specs(
    module: ModuleType,
    context: ProviderExtensionContext,
    extension: Mapping[str, object],
) -> tuple[DataProductContract, ...]:
    function_name = str(extension.get("products_function") or "products")
    products = getattr(module, function_name, None)
    if callable(products):
        return _as_product_specs(products(context))
    if hasattr(module, "PRODUCTS"):
        return _as_product_specs(getattr(module, "PRODUCTS"))
    return ()


def _register_returned(registry: object, value: object, default_specs: tuple[DataProductContract, ...]) -> None:
    if value is None:
        return
    if _looks_like_builder(value):
        registry.register(value, default_specs)
        return
    if isinstance(value, tuple) and len(value) == 2 and _looks_like_builder(value[0]):
        registry.register(value[0], _as_product_specs(value[1]) or default_specs)
        return
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        for item in value:
            _register_returned(registry, item, default_specs)
        return
    raise ValueError("provider extension register() returned an unsupported value")


def _looks_like_builder(value: object) -> bool:
    return hasattr(value, "provider") and callable(getattr(value, "supports", None)) and callable(getattr(value, "acquire", None))


def _as_product_specs(value: object) -> tuple[DataProductContract, ...]:
    if value is None:
        return ()
    if isinstance(value, DataProductContract):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        result = tuple(value)
        if not all(isinstance(item, DataProductContract) for item in result):
            raise ValueError("provider extension products must be DataProductContract objects")
        return result
    raise ValueError("provider extension products must be DataProductContract objects")


def _provider_extensions(config: str | Path | Mapping[str, Any]) -> tuple[Mapping[str, object], ...]:
    value = dict(config) if isinstance(config, Mapping) else json.loads(Path(config).read_text(encoding="utf-8"))
    extensions = value.get("provider_extensions") if isinstance(value, dict) else None
    if extensions is None:
        return ()
    if not isinstance(extensions, list):
        raise ValueError("provider config provider_extensions must be a list")
    result = []
    for index, item in enumerate(extensions):
        if not isinstance(item, dict):
            raise ValueError(f"provider config provider_extensions[{index}] must be an object")
        result.append(dict(item))
    return tuple(result)


def _config_base_path(config: str | Path | Mapping[str, Any]) -> Path:
    if isinstance(config, Mapping):
        config_path = config.get(CONFIG_PATH_KEY)
        if config_path:
            return Path(str(config_path)).expanduser().resolve()
        return Path.cwd()
    return Path(config)


def _extension_kind(extension: Mapping[str, object]) -> str:
    return str(extension.get("kind") or extension.get("type") or "python").strip()


def _external_process_product_specs(extension: Mapping[str, object]) -> tuple[DataProductContract, ...]:
    provider = str(extension.get("provider") or "").strip()
    if not provider:
        raise ValueError("external process provider extension requires provider")
    raw_products = extension.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise ValueError("external process provider extension requires products")
    return tuple(_external_process_product_spec(provider, extension, dict(item)) for item in raw_products)


def _external_process_product_spec(
    provider: str,
    extension: Mapping[str, object],
    raw: Mapping[str, object],
) -> DataProductContract:
    logical_key = str(raw.get("logical_key") or raw.get("key") or raw.get("dataset") or "").strip()
    if not logical_key:
        raise ValueError("external process product requires logical_key")
    venue = raw.get("venue", extension.get("venue"))
    dimensions = {
        "provider": provider,
        **({"venue": str(venue)} if venue else {}),
        **{str(key): str(value) for key, value in dict(raw.get("dimensions", {})).items()},
    }
    product = DataProductDefinition(
        DatasetKey(logical_key),
        str(raw.get("title") or logical_key),
        DatasetLayer(str(raw.get("layer", DatasetLayer.CANONICAL.value))),
        str(raw.get("description") or f"External process Data Product {logical_key}."),
        dimensions,
        str(raw.get("primary_time") or "available_time"),
        sources=(SourceBinding(
            provider,
            str(venue) if venue else None,
            int(raw.get("priority", extension.get("priority", 100))),
            QualityLevel(str(raw.get("source_quality_level", QualityLevel.WORKSPACE.value))),
            tuple(str(item) for item in raw.get("acquisition_modes", ("process",))),
        ),),
        owner=str(raw.get("owner", extension.get("owner", "user"))),
        source_policy_version=str(raw.get("source_policy_version", "priority-v1")),
    )
    return DataProductContract(
        product,
        str(raw.get("relative_path") or f"canonical/external/provider={provider}/product={logical_key}"),
        str(raw.get("schema_id") or f"{logical_key}.v1"),
        dict(raw.get("capabilities", {})),
        DatasetStorageKind(str(raw.get("storage_kind", DatasetStorageKind.TABULAR.value))),
        str(raw.get("layout_version", "1")),
        str(raw.get("quality_profile", "external_process")),
        QualityLevel(str(raw.get("minimum_publication_level", QualityLevel.WORKSPACE.value))),
    )


def _external_process_binding(
    config_path: str | Path,
    extension: Mapping[str, object],
    spec: DataProductContract,
) -> ExternalProcessProductBinding:
    raw = _external_process_product_config(extension, str(spec.key))
    fields = tuple(str(item.get("name") if isinstance(item, Mapping) else item) for item in raw.get("fields", ()))
    cwd_value = extension.get("cwd")
    cwd = Path(str(cwd_value)).expanduser() if cwd_value else Path(config_path).parent
    if not cwd.is_absolute():
        cwd = Path(config_path).parent / cwd
    env = {str(key): str(value) for key, value in dict(extension.get("env", {})).items()}
    return ExternalProcessProductBinding(
        spec,
        fields,
        str(extension["provider"]),
        spec.product.sources[0].venue if spec.product.sources else None,
        command_tuple(extension.get("command")),
        cwd.resolve(),
        env,
        int(extension.get("timeout_seconds", 300)),
        str(raw.get("transform_id", extension.get("transform_id", "external_process.dataset"))),
        str(raw.get("transform_version", extension.get("transform_version", "1"))),
        str(extension.get("cost_class", "external-process")),
        int(extension["estimate_requests"]) if extension.get("estimate_requests") is not None else None,
    )


def _external_process_product_config(extension: Mapping[str, object], logical_key: str) -> Mapping[str, object]:
    for item in extension.get("products", ()):
        if isinstance(item, Mapping) and str(item.get("logical_key") or item.get("key") or item.get("dataset")) == logical_key:
            return item
    raise ValueError(f"external process extension has no product config for {logical_key!r}")


def _extension_module_path(config_path: str | Path, extension: Mapping[str, object]) -> Path:
    source = extension.get("path") or extension.get("file") or extension.get("module")
    if not source:
        raise ValueError("provider extension requires path")
    path = Path(str(source)).expanduser()
    if not path.is_absolute():
        path = Path(config_path).parent / path
    return path.resolve()


def _load_extension_module(path: Path) -> ModuleType:
    if not path.exists():
        raise FileNotFoundError(path)
    digest = sha256(path.read_bytes()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(f"kairospy_provider_extension_{digest}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load provider extension module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
