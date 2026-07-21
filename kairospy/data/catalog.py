from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from difflib import get_close_matches
import json
from pathlib import Path
from uuid import uuid4

from kairospy.configuration import DEFAULT_LAKE_ROOT

from .contracts import (
    DataView, DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetRelease, DatasetStatus,
    DatasetStorageKind, QualityLevel,
    SourceBinding,
)


class DataCatalog:
    """Current dataset-product, immutable-release, and alias registry."""

    def __init__(self, root: str | Path = DEFAULT_LAKE_ROOT, registry_path: str | Path | None = None) -> None:
        self.root = Path(root)
        self.registry_path = Path(registry_path) if registry_path is not None else self.root / "catalog" / "datasets.json"
        self._products: dict[str, DataProductDefinition] = {}
        self._specs: dict[str, DataProductContract] = {}
        self._releases: dict[str, DatasetRelease] = {}
        self._aliases: dict[str, str] = {}
        if self.registry_path.exists():
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 4:
                raise ValueError("dataset registry must use current schema version 4")
            self._aliases = {str(alias): str(release_id) for alias, release_id in value.get("aliases", {}).items()}
            for raw in value.get("product_specs", []):
                self.register_product_spec(_spec_from_primitive(raw))
            for raw in value.get("products", []):
                self.register_product(_product_from_primitive(raw), enrich=True)
            for raw in value.get("releases", []):
                self.register_release(_release_from_primitive(raw))
            missing_alias_targets = sorted(set(self._aliases.values()) - set(self._releases))
            if missing_alias_targets:
                raise ValueError(f"dataset aliases target unknown releases: {', '.join(missing_alias_targets)}")

    def register_product(self, product: DataProductDefinition, *, enrich: bool = False) -> None:
        previous = self._products.get(str(product.key))
        if previous is not None and previous != product:
            if not enrich or previous.layer != product.layer or previous.primary_time != product.primary_time:
                raise ValueError(f"conflicting dataset product: {product.key}")
        if previous is not None and enrich:
            product = replace(
                product,
                title=previous.title if previous.title != str(previous.key) else product.title,
                description=previous.description or product.description,
                dimensions=previous.dimensions or product.dimensions,
                sources=product.sources or previous.sources,
                owner=previous.owner or product.owner,
                source_policy_version=product.source_policy_version or previous.source_policy_version,
            )
        self._products[str(product.key)] = product

    def register_product_spec(self, spec: DataProductContract, *, enrich: bool = False) -> None:
        key = str(spec.key)
        previous = self._specs.get(key)
        if previous is not None and previous != spec:
            contract_unchanged = replace(previous, product=spec.product) == spec
            if not enrich or not contract_unchanged:
                raise ValueError(f"conflicting data product contract: {spec.key}")
        self.register_product(spec.product, enrich=enrich)
        self._specs[key] = replace(spec, product=self._products[key])

    def product_spec(self, key: DataProductDefinition | DatasetKey | str) -> DataProductContract:
        product = self.product(key)
        try:
            return self._specs[str(product.key)]
        except KeyError as error:
            raise KeyError(f"dataset product has no complete data product contract: {product.key}") from error

    def product_specs(self) -> tuple[DataProductContract, ...]:
        return tuple(sorted(self._specs.values(), key=lambda item: str(item.key)))

    def update_product_spec(self, spec: DataProductContract, *, actor: str, reason: str) -> None:
        if not actor.strip() or not reason.strip():
            raise ValueError("data product contract update requires actor and reason")
        key = str(spec.key)
        previous = self._specs.get(key)
        if previous is None:
            self.register_product_spec(spec, enrich=True)
            return
        self.register_product(spec.product, enrich=True)
        updated = replace(spec, product=self._products[key])
        if updated == previous:
            return
        self._specs[key] = updated
        audit = self.registry_path.parent / "product-spec-updates.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "logical_key": key,
                "actor": actor,
                "reason": reason,
                "from": _spec_primitive(previous),
                "to": _spec_primitive(updated),
                "at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    def relocate_release(self, release_id: str, relative_path: str, *, actor: str, reason: str,
                         verified_content_hash: str) -> DatasetRelease:
        if not actor.strip() or not reason.strip() or not verified_content_hash.strip():
            raise ValueError("release relocation requires actor, reason, and verified content hash")
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("release relocation path must be lake-relative")
        current = self.release(release_id)
        if current.content_hash != verified_content_hash:
            raise ValueError("release relocation hash does not match immutable Release content")
        if not (self.root / path).exists():
            raise FileNotFoundError(self.root / path)
        relocated = replace(current, relative_path=path.as_posix())
        self._releases[release_id] = relocated
        audit = self.registry_path.parent / "release-relocations.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "release_id": release_id,
                "logical_key": str(current.product_key),
                "from": current.relative_path,
                "to": relocated.relative_path,
                "content_hash": verified_content_hash,
                "actor": actor,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return relocated

    def register_release(self, release: DatasetRelease) -> None:
        if str(release.product_key) not in self._products:
            raise KeyError(f"dataset product is not registered: {release.product_key}")
        previous = self._releases.get(release.release_id)
        if previous is not None and previous != release:
            raise ValueError(f"immutable dataset release conflicts with existing content: {release.release_id}")
        self._releases[release.release_id] = release
        self._validate_release_aliases()

    def resolve(self, name_or_alias: str, *, version: str | None = None) -> DatasetRelease:
        return self.release(name_or_alias, version=version)

    def product(self, key: DataProductDefinition | DatasetKey | str) -> DataProductDefinition:
        value = str(key.key) if isinstance(key, DataProductDefinition) else str(key)
        direct = self._products.get(value)
        if direct is not None:
            return direct
        return self._products[str(self.release(value).product_key)]

    def release(self, value: DataProductDefinition | DatasetKey | str, *, version: str | None = None,
                provider: str | None = None, venue: str | None = None) -> DatasetRelease:
        name = str(value.key) if isinstance(value, DataProductDefinition) else str(value)
        direct = None if isinstance(value, (DataProductDefinition, DatasetKey)) else self._releases.get(self._aliases.get(name, name))
        candidates = [item for item in self._releases.values()
                      if str(item.product_key) == name or name in item.aliases]
        if direct is not None:
            candidates = [direct]
        if version is not None:
            candidates = [item for item in candidates if item.release_version == version]
        if provider is not None:
            candidates = [item for item in candidates if item.provider == provider]
        if venue is not None:
            candidates = [item for item in candidates if item.venue == venue]
        if not candidates:
            raise KeyError(_unknown_message(name, self._products, self._releases))
        approved = [item for item in candidates if item.status in {
            DatasetStatus.APPROVED_FOR_WORKSPACE, DatasetStatus.APPROVED_FOR_BACKTEST,
            DatasetStatus.APPROVED_FOR_PRODUCTION,
        }]
        product = self._products.get(str((approved or candidates)[0].product_key))
        priorities = {(item.provider, item.venue): item.priority for item in product.sources} if product else {}
        return sorted(approved or candidates, key=lambda item: (
            priorities.get((item.provider, item.venue), 0), item.published_at or "", _version_key(item.release_version),
        ))[-1]

    def quarantine(self, release_id: str, *, actor: str, reason: str) -> DatasetRelease:
        if not actor.strip() or not reason.strip():
            raise ValueError("dataset quarantine requires actor and reason")
        current = self.release(release_id)
        quarantined = replace(current, status=DatasetStatus.QUARANTINED)
        self._releases[release_id] = quarantined
        self.save()
        audit = self.registry_path.parent / "quarantines.jsonl"; audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "release_id": release_id, "logical_key": str(current.product_key), "actor": actor,
                "reason": reason, "from": current.status.value, "to": DatasetStatus.QUARANTINED.value,
                "content_hash": current.content_hash, "at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return quarantined

    def add_release_alias(self, release_id: str, alias: str) -> DatasetRelease:
        name = alias.strip()
        if not name:
            raise ValueError("dataset alias cannot be empty")
        current = self.release(release_id)
        if name == str(current.product_key) or name in current.aliases:
            return current
        for item in self._releases.values():
            if item.release_id != current.release_id and name in item.aliases:
                raise ValueError(f"dataset alias {name!r} is already used by release {item.release_id!r}")
        updated = replace(current, aliases=tuple((*current.aliases, name)))
        self._releases[release_id] = updated
        self._validate_release_aliases()
        self.save()
        return updated

    def purge_quarantined_release(self, release_id: str, *, actor: str, reason: str) -> None:
        if not actor.strip() or not reason.strip():
            raise ValueError("quarantined Release purge requires actor and reason")
        release = self.release(release_id)
        if release.status is not DatasetStatus.QUARANTINED:
            raise ValueError("only quarantined Releases can be purged")
        aliases = [alias for alias, target in self._aliases.items() if target == release_id]
        if aliases:
            raise ValueError("cannot purge a quarantined Release targeted by aliases")
        path = self.root / release.relative_path
        if path.exists():
            import shutil
            shutil.rmtree(path)
        del self._releases[release_id]
        self.save()
        audit = self.registry_path.parent / "release-purges.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "release_id": release_id,
                "logical_key": str(release.product_key),
                "content_hash": release.content_hash,
                "relative_path": release.relative_path,
                "actor": actor,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    def products(self) -> tuple[DataProductDefinition, ...]:
        return tuple(sorted(self._products.values(), key=lambda item: str(item.key)))

    def releases(self, product: DataProductDefinition | DatasetKey | str | None = None) -> tuple[DatasetRelease, ...]:
        values = self._releases.values()
        if product is not None:
            key = str(product.key) if isinstance(product, DataProductDefinition) else str(product)
            values = (item for item in values if str(item.product_key) == key)
        return tuple(sorted(values, key=lambda item: (str(item.product_key), _version_key(item.release_version))))

    def aliases(self) -> dict[str, str]:
        return dict(sorted(self._aliases.items()))

    def promote_alias(self, alias: str, release_id: str, *, actor: str, reason: str,
                      quality_report_hash: str) -> DatasetRelease:
        if "@" not in alias or alias.startswith("@") or alias.endswith("@"):
            raise ValueError("dataset alias must use product@name form")
        if not actor.strip() or not reason.strip() or not quality_report_hash.strip():
            raise ValueError("alias promotion requires actor, reason and quality report hash")
        release = self.release(release_id)
        product_prefix = alias.split("@", 1)[0]
        if product_prefix != str(release.product_key):
            raise ValueError("dataset alias product prefix must match the target release product")
        if release.status not in {DatasetStatus.APPROVED_FOR_WORKSPACE, DatasetStatus.APPROVED_FOR_BACKTEST,
                                  DatasetStatus.APPROVED_FOR_PRODUCTION}:
            raise ValueError("dataset alias can only target an approved release")
        previous = self._aliases.get(alias)
        self._aliases[alias] = release.release_id
        self.save()
        audit = self.registry_path.parent / "alias-promotions.jsonl"; audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "alias": alias, "logical_key": str(release.product_key), "from_release": previous,
                "to_release": release.release_id, "actor": actor, "reason": reason,
                "quality_report_hash": quality_report_hash, "content_hash": release.content_hash,
                "at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return release

    def search(self, **dimensions: str) -> tuple[DataProductDefinition, ...]:
        return tuple(item for item in self.products()
                     if all(item.dimensions.get(key) == value for key, value in dimensions.items()))

    def promote(self, release_id: str, status: DatasetStatus | str, *, actor: str, reason: str) -> DatasetRelease:
        target = DatasetStatus(status)
        current = self.release(release_id)
        allowed = {
            DatasetStatus.VALIDATED: {DatasetStatus.APPROVED_FOR_WORKSPACE},
            DatasetStatus.APPROVED_FOR_WORKSPACE: {DatasetStatus.APPROVED_FOR_BACKTEST},
            DatasetStatus.APPROVED_FOR_BACKTEST: {DatasetStatus.APPROVED_FOR_PRODUCTION},
        }
        if target not in allowed.get(current.status, set()):
            raise ValueError(f"invalid dataset promotion: {current.status.value} -> {target.value}")
        minimum = {
            DatasetStatus.APPROVED_FOR_WORKSPACE: {QualityLevel.WORKSPACE, QualityLevel.BACKTEST, QualityLevel.PRODUCTION},
            DatasetStatus.APPROVED_FOR_BACKTEST: {QualityLevel.BACKTEST, QualityLevel.PRODUCTION},
            DatasetStatus.APPROVED_FOR_PRODUCTION: {QualityLevel.PRODUCTION},
        }[target]
        if current.quality_level not in minimum:
            raise ValueError(f"{target.value} requires a higher quality level than {current.quality_level.value}")
        if not actor.strip() or not reason.strip():
            raise ValueError("dataset promotion requires actor and reason")
        promoted = replace(current, status=target)
        self._releases[release_id] = promoted
        self.save()
        audit = self.registry_path.parent / "promotions.jsonl"; audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "release_id": release_id, "logical_key": str(promoted.product_key),
                "from": current.status.value, "to": target.value, "actor": actor, "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(), "content_hash": promoted.content_hash,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return promoted

    def record_quality_assessment(self, release_id: str, level: QualityLevel | str, *,
                                  report_hash: str, actor: str, reason: str) -> DatasetRelease:
        if not report_hash.strip() or not actor.strip() or not reason.strip():
            raise ValueError("quality assessment requires report hash, actor and reason")
        current = self.release(release_id)
        target_level = QualityLevel(level)
        target_status = DatasetStatus.QUARANTINED if target_level is QualityLevel.ARCHIVED else current.status
        assessed = replace(current, quality_level=target_level, status=target_status)
        self._releases[release_id] = assessed
        self.save()
        audit = self.registry_path.parent / "quality-assessments.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "release_id": release_id, "logical_key": str(assessed.product_key),
                "from": current.quality_level.value, "to": assessed.quality_level.value,
                "from_status": current.status.value, "to_status": assessed.status.value,
                "report_hash": report_hash, "actor": actor, "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return assessed

    def path(self, dataset_id_or_alias: str, *, version: str | None = None) -> Path:
        return self.root / self.resolve(dataset_id_or_alias, version=version).relative_path

    def save(self) -> Path:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 4, "aliases": self.aliases(),
                   "products": [_product_primitive(item) for item in self.products()],
                   "product_specs": [_spec_primitive(item) for item in self.product_specs()],
                   "releases": [_release_primitive(item) for item in self.releases()]}
        temporary = self.registry_path.with_name(f".{self.registry_path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(self.registry_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return self.registry_path

    def discover(self) -> tuple[DatasetRelease, ...]:
        """Register current governed datasets already present in the lake."""
        discovered: list[DatasetRelease] = []
        for pointer in sorted(self.root.glob("**/release.json")):
            directory = pointer.parent
            release = _release_from_metadata(self.root, directory)
            if release is None or release.release_id in self._releases:
                continue
            product = _product_from_metadata(directory, release)
            try:
                spec = _spec_from_metadata(self.root, directory, product, release)
                try:
                    self.product_spec(product.key)
                except KeyError:
                    self.register_product_spec(spec, enrich=True)
                else:
                    self.register_product(product, enrich=True)
            except ValueError:
                self.register_product(product, enrich=True)
            self.register_release(release)
            discovered.append(release)
        event_root = self.root / "canonical" / "market"
        for directory in sorted(event_root.glob("dataset=*")):
            dataset_id = directory.name.removeprefix("dataset=")
            if dataset_id in self._releases:
                continue
            schema = _read_json(directory / "schema.json")
            lineage = _read_json(directory / "lineage.json")
            manifest = _read_json(directory / "manifest.json")
            provider = str(lineage.get("source", {}).get("provider", "")) if isinstance(lineage.get("source"), dict) else ""
            logical = _event_logical_name(dataset_id, provider)
            product = _discovered_product(logical, DatasetLayer.CANONICAL, provider)
            self.register_product(product, enrich=True)
            release = DatasetRelease(
                dataset_id, product.key, str(manifest.get("generated_at") or dataset_id),
                str(schema.get("schema_id", "market.event_envelope.v1")),
                str(schema.get("schema_version", _schema_version(str(schema.get("schema_id", "market.event_envelope.v1"))))),
                str(lineage.get("producer", {}).get("name", "discovery")) if isinstance(lineage.get("producer"), dict) else "discovery",
                str(lineage.get("producer", {}).get("version", "1")) if isinstance(lineage.get("producer"), dict) else "1",
                str(directory.relative_to(self.root)), "parquet",
                str(manifest.get("dataset_sha256") or manifest.get("content_sha256") or "") or None,
                provider or None,
                str(lineage.get("source", {}).get("venue")) if isinstance(lineage.get("source"), dict) and lineage.get("source", {}).get("venue") else None,
                (), _status(_discovered_status(directory)), _quality_level(directory, _status(_discovered_status(directory))),
                str(manifest.get("generated_at") or dataset_id), DatasetStorageKind.MARKET_EVENTS, "1",
            )
            self.register_release(release)
            discovered.append(release)
        pointers = [*(self.root / "curated").glob("**/dataset.json")]
        for pointer in sorted(pointers):
            value = _read_json(pointer)
            manifest = value.get("manifest", {}) if isinstance(value.get("manifest"), dict) else {}
            dataset_id = str(manifest.get("dataset_id") or pointer.parent.name)
            if dataset_id in self._releases:
                continue
            market_type = str(manifest.get("market_data_type") or "market_snapshots")
            underlying = dataset_id.lower().split(".")[0]
            logical = f"curated.market_snapshots.options.us.{underlying}" if "massive" in dataset_id.lower() else f"curated.{market_type}"
            product = _discovered_product(logical, DatasetLayer.CURATED, "")
            self.register_product(product, enrich=True)
            status = _status(_discovered_status(pointer.parent))
            release = DatasetRelease(
                dataset_id, product.key, _manifest_version(manifest.get("end"), dataset_id),
                "market_replay_dataset.v2", "2", "market_replay_dataset", "2",
                str(pointer.parent.relative_to(self.root)), str(value.get("format", "json")),
                str(manifest.get("content_hash") or "") or None,
                str(manifest.get("source")) if manifest.get("source") else None, None, (), status,
                _quality_level(pointer.parent, status), str(manifest.get("created_at") or manifest.get("end") or dataset_id),
                DatasetStorageKind.MARKET_SNAPSHOTS, "1",
            )
            self.register_release(release)
            discovered.append(release)
        return tuple(discovered)

    def _validate_release_aliases(self) -> None:
        owners: dict[str, str] = {}
        for item in self._releases.values():
            for alias in item.aliases:
                product = str(item.product_key)
                previous = owners.get(alias)
                if previous is not None and previous != product:
                    raise ValueError(f"dataset alias {alias!r} is shared by {previous!r} and {product!r}")
                owners[alias] = product


def _product_primitive(item: DataProductDefinition) -> dict[str, object]:
    return {
        "logical_key": str(item.key), "title": item.title, "layer": item.layer.value,
        "description": item.description, "dimensions": dict(item.dimensions), "primary_time": item.primary_time,
        "default_view": item.default_view.value, "sources": [
            {"provider": source.provider, "venue": source.venue, "priority": source.priority,
             "quality_level": source.quality_level.value, "acquisition_modes": list(source.acquisition_modes)}
            for source in item.sources
        ], "owner": item.owner, "source_policy_version": item.source_policy_version,
    }


def _release_primitive(item: DatasetRelease) -> dict[str, object]:
    return {
        "release_id": item.release_id, "logical_key": str(item.product_key),
        "release_version": item.release_version, "schema_id": item.schema_id,
        "schema_version": item.schema_version, "transform_id": item.transform_id,
        "transform_version": item.transform_version, "relative_path": item.relative_path,
        "format": item.format, "content_hash": item.content_hash, "provider": item.provider,
        "venue": item.venue, "aliases": list(item.aliases), "status": item.status.value,
        "quality_level": item.quality_level.value, "published_at": item.published_at,
        "storage_kind": item.storage_kind.value, "layout_version": item.layout_version,
    }


def _spec_primitive(item: DataProductContract) -> dict[str, object]:
    return {
        "product": _product_primitive(item.product),
        "relative_path": item.relative_path,
        "schema_id": item.schema_id,
        "capabilities": dict(item.capabilities),
        "storage_kind": item.storage_kind.value,
        "layout_version": item.layout_version,
        "quality_profile": item.quality_profile,
        "minimum_publication_level": item.minimum_publication_level.value,
    }


def _product_from_primitive(raw: dict[str, object]) -> DataProductDefinition:
    sources = tuple(SourceBinding(
        str(item["provider"]), str(item["venue"]) if item.get("venue") is not None else None,
        int(item.get("priority", 0)), QualityLevel(str(item.get("quality_level", QualityLevel.WORKSPACE.value))),
        tuple(str(value) for value in item.get("acquisition_modes", [])),
    ) for item in raw.get("sources", []))
    return DataProductDefinition(
        DatasetKey(str(raw["logical_key"])), str(raw.get("title") or raw["logical_key"]),
        DatasetLayer(str(raw["layer"])), str(raw.get("description", "")),
        {str(key): str(value) for key, value in dict(raw.get("dimensions", {})).items()},
        str(raw.get("primary_time", "available_time")),
        DataView(str(raw.get("default_view", DataView.RAW_AS_RECEIVED.value))), sources,
        str(raw["owner"]) if raw.get("owner") is not None else None,
        str(raw.get("source_policy_version", "priority-v1")),
    )


def _spec_from_primitive(raw: dict[str, object]) -> DataProductContract:
    product_raw = raw.get("product")
    if not isinstance(product_raw, dict):
        raise ValueError("data product contract requires a product document")
    return DataProductContract(
        _product_from_primitive(product_raw),
        str(raw["relative_path"]),
        str(raw["schema_id"]),
        dict(raw.get("capabilities", {})),
        DatasetStorageKind(str(raw.get("storage_kind", DatasetStorageKind.TABULAR.value))),
        str(raw.get("layout_version", "1")),
        str(raw.get("quality_profile", "generic")),
        QualityLevel(str(raw.get("minimum_publication_level", QualityLevel.WORKSPACE.value))),
    )


def _release_from_primitive(raw: dict[str, object]) -> DatasetRelease:
    return DatasetRelease(
        str(raw["release_id"]), DatasetKey(str(raw["logical_key"])), str(raw["release_version"]),
        str(raw["schema_id"]), str(raw["schema_version"]), str(raw["transform_id"]),
        str(raw["transform_version"]), str(raw["relative_path"]), str(raw["format"]),
        str(raw["content_hash"]) if raw.get("content_hash") is not None else None,
        str(raw["provider"]) if raw.get("provider") is not None else None,
        str(raw["venue"]) if raw.get("venue") is not None else None,
        tuple(str(item) for item in raw.get("aliases", [])), DatasetStatus(str(raw["status"])),
        QualityLevel(str(raw.get("quality_level", QualityLevel.WORKSPACE.value))),
        str(raw["published_at"]) if raw.get("published_at") is not None else None,
        DatasetStorageKind(str(raw.get("storage_kind") or _storage_kind(
            str(raw["relative_path"]), str(raw["schema_id"]),
        ).value)),
        str(raw.get("layout_version", "1")),
    )


def _layer(value: str) -> DatasetLayer:
    aliases = {"feature": "features"}
    return DatasetLayer(aliases.get(value, value))


def _status(value: str) -> DatasetStatus:
    aliases = {"approved": DatasetStatus.APPROVED_FOR_WORKSPACE.value}
    return DatasetStatus(aliases.get(value, value))


def _schema_version(schema_id: str) -> str:
    tail = schema_id.rsplit(".", 1)[-1]
    return tail[1:] if tail.startswith("v") and tail[1:].isdigit() else "1"


def _primary_time(schema_id: str) -> str:
    return "available_time" if "event" in schema_id or "quote" in schema_id or "trade" in schema_id else "period_start"


def _storage_kind(relative_path: str, schema_id: str) -> DatasetStorageKind:
    normalized = relative_path.replace("\\", "/")
    schema = schema_id.lower()
    if "/canonical/market/dataset=" in f"/{normalized}" or "event_envelope" in schema:
        return DatasetStorageKind.MARKET_EVENTS
    if "market_snapshots" in normalized or "market_replay_dataset" in schema or "historical_dataset" in schema or normalized.endswith("/dataset.json"):
        return DatasetStorageKind.MARKET_SNAPSHOTS
    if normalized.startswith("reference/") or schema.startswith("reference."):
        return DatasetStorageKind.REFERENCE
    return DatasetStorageKind.TABULAR


def _quality_level(directory: Path, status: DatasetStatus) -> QualityLevel:
    capabilities = _read_json(directory / "capabilities.json")
    level = int(capabilities.get("maximum_validation_level", 0) or 0)
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return QualityLevel.PRODUCTION
    if status in {DatasetStatus.APPROVED_FOR_BACKTEST} or level >= 3:
        return QualityLevel.BACKTEST
    if status in {DatasetStatus.APPROVED_FOR_WORKSPACE, DatasetStatus.VALIDATED} or level >= 2:
        return QualityLevel.WORKSPACE
    return QualityLevel.INTEGRITY if level >= 1 else QualityLevel.ARCHIVED


def _unknown_message(name: str, products, releases) -> str:
    values = set(products) | set(releases)
    values.update(alias for item in releases.values() for alias in item.aliases)
    matches = get_close_matches(name, sorted(values), n=3, cutoff=0.45)
    suggestion = f"; did you mean: {', '.join(matches)}" if matches else ""
    return f"unknown dataset product, release or alias {name!r}{suggestion}"


def _version_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in value.replace("-", ".").split("."))


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _release_from_metadata(root: Path, directory: Path) -> DatasetRelease | None:
    raw = _read_json(directory / "release.json")
    release_id = raw.get("release_id")
    logical_key = raw.get("logical_key")
    if not release_id or not logical_key:
        return None
    relative_path = directory.relative_to(root).as_posix()
    schema_id = str(raw.get("schema_id") or _read_json(directory / "schema.json").get("schema_id") or "dataset.v1")
    status = _status(str(raw.get("status") or _discovered_status(directory)))
    quality = QualityLevel(str(raw.get("quality_level") or _quality_level(directory, status).value))
    content_hash = raw.get("content_hash") or _read_json(directory / "manifest.json").get("dataset_sha256")
    return DatasetRelease(
        str(release_id),
        DatasetKey(str(logical_key)),
        str(raw.get("release_version") or f"content.{str(content_hash or release_id)[:16]}"),
        schema_id,
        str(raw.get("schema_version") or _schema_version(schema_id)),
        str(raw.get("transform_id") or "discovery"),
        str(raw.get("transform_version") or "1"),
        relative_path,
        str(raw.get("format") or "parquet"),
        str(content_hash) if content_hash is not None else None,
        str(raw.get("provider")) if raw.get("provider") is not None else None,
        str(raw.get("venue")) if raw.get("venue") is not None else None,
        tuple(str(item) for item in raw.get("aliases", ())),
        status,
        quality,
        str(raw.get("published_at")) if raw.get("published_at") is not None else None,
        DatasetStorageKind(str(raw.get("storage_kind") or _storage_kind(relative_path, schema_id).value)),
        str(raw.get("layout_version") or "1"),
    )


def _product_from_metadata(directory: Path, release: DatasetRelease) -> DataProductDefinition:
    usage = _read_json(directory / "usage.json")
    dimensions = {str(key): str(value) for key, value in dict(usage.get("dimensions", {})).items()}
    sources = (
        SourceBinding(release.provider, release.venue, 100, release.quality_level),
    ) if release.provider else ()
    return DataProductDefinition(
        release.product_key,
        str(usage.get("title") or release.product_key),
        _layer(release.relative_path.split("/", 1)[0]),
        str(usage.get("description") or ""),
        dimensions,
        str(usage.get("primary_time") or _primary_time(release.schema_id)),
        DataView(str(usage.get("default_view") or DataView.RAW_AS_RECEIVED.value)),
        sources,
        str(usage.get("owner")) if usage.get("owner") is not None else None,
        str(usage.get("source_policy_version") or "priority-v1"),
    )


def _spec_from_metadata(root: Path, directory: Path, product: DataProductDefinition,
                        release: DatasetRelease) -> DataProductContract:
    base = directory.parent if directory.name.startswith("release=") else directory
    capabilities = _read_json(directory / "capabilities.json")
    return DataProductContract(
        product,
        base.relative_to(root).as_posix(),
        release.schema_id,
        capabilities,
        release.storage_kind,
        release.layout_version,
        str(_read_json(directory / "quality.json").get("profile") or "generic"),
        release.quality_level if release.quality_level is not QualityLevel.ARCHIVED else QualityLevel.WORKSPACE,
    )


def _event_logical_name(dataset_id: str, provider: str) -> str:
    parts = dataset_id.lower().split(".")
    if provider == "massive" and len(parts) > 3 and parts[:3] == ["options", "us", "massive"]:
        return f"market.events.options.us.{parts[3]}"
    return "market.events"


def _manifest_version(value: object, fallback: str) -> str:
    if isinstance(value, dict) and "$datetime" in value:
        return str(value["$datetime"])
    return str(value or fallback)


def _discovered_status(directory: Path) -> str:
    quality = _read_json(directory / "quality.json")
    return "approved_for_workspace" if quality.get("passed") is True else "registered"


def _discovered_product(logical: str, layer: DatasetLayer, provider: str) -> DataProductDefinition:
    parts = logical.split(".")
    dimensions = {}
    if len(parts) >= 5 and parts[:4] in (["market", "events", "options", "us"],
                                        ["curated", "market_snapshots", "options", "us"],
                                        ["curated", "market_slices", "options", "us"]):
        underlying = "SPX" if parts[4].lower() == "spxw" else parts[4].upper()
        dimensions = {"asset_class": "option", "region": "us", "underlying": underlying,
                      "frequency": "event" if layer is DatasetLayer.CANONICAL else "slice"}
        if parts[4].lower() == "spxw":
            dimensions["contract_family"] = "SPXW"
        if layer is DatasetLayer.CANONICAL:
            dimensions["venue"] = "opra"
    elif logical == "curated.synthetic":
        dimensions = {"synthetic": "true"}
    elif logical.startswith("curated."):
        dimensions = {"market_data_type": logical.removeprefix("curated.")}
    sources = (SourceBinding(provider, "opra", 100, QualityLevel.WORKSPACE),) if provider else ()
    return DataProductDefinition(DatasetKey(logical), logical, layer, dimensions=dimensions,
                          primary_time="available_time" if layer is DatasetLayer.CANONICAL else "timestamp",
                          sources=sources)
