from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterable

from .workspace import StudyWorkspace, StudyWorkspaceRepository


_LEGACY_SMA_TUTORIAL_HASH = "f9c8e187e9fcf1b4565f6dbb6903155ce2e0ae06a1dc1ef7960720d6052c174e"


class StudyData:
    def __init__(self, session: StudySession) -> None:
        self._session = session

    def arrow(self, *, columns: Iterable[str] | None = None):
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.ARROW)

    def pandas(self, *, columns: Iterable[str] | None = None):
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.PANDAS)

    def polars(self, *, columns: Iterable[str] | None = None):
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.POLARS)

    def rows(self, *, columns: Iterable[str] | None = None) -> list[dict[str, object]]:
        from kairos.data import OutputFormat
        return self._query(columns).collect(OutputFormat.ROWS)

    def _query(self, columns: Iterable[str] | None):
        workspace = self._session.workspace
        return self._session.client.get(
            workspace.input_release_id, start=workspace.start, end=workspace.end,
            fields=tuple(columns) if columns is not None else None,
        )


@dataclass(frozen=True, slots=True)
class StudyProfile:
    rows: int
    columns: int
    missing_values: int
    duplicate_primary_times: int
    chronological: bool
    valid_ohlc: bool | None
    point_in_time_safe: bool | None
    passed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "rows": self.rows, "columns": self.columns, "missing_values": self.missing_values,
            "duplicate_primary_times": self.duplicate_primary_times, "chronological": self.chronological,
            "valid_ohlc": self.valid_ohlc, "point_in_time_safe": self.point_in_time_safe,
            "passed": self.passed,
        }


class StudySession:
    def __init__(self, root: str | Path, workspace: StudyWorkspace) -> None:
        from kairos.data import DatasetClient, RunMode
        self.root = Path(root)
        self.workspace = workspace
        self.client = DatasetClient(self.root, run_mode=RunMode.STUDY)
        release = self.client.resolve(workspace.input_release_id)
        if release.content_hash != workspace.input_content_hash:
            raise ValueError(
                f"Study input hash does not match Dataset Release: {workspace.input_content_hash} != {release.content_hash}"
            )
        self.data = StudyData(self)

    def describe(self, format: str = "dict") -> dict[str, object] | str:
        description = self._describe()
        if format in {"dict", "json"}:
            return description
        if format in {"table", "pretty", "prettytable"}:
            return _describe_table(description)
        raise ValueError("describe format must be one of: dict, table")

    def describe_table(self) -> str:
        return _describe_table(self._describe())

    def _describe(self) -> dict[str, object]:
        from kairos.data import OutputFormat
        workspace = self.workspace
        query = self.client.get(workspace.input_release_id, start=workspace.start, end=workspace.end)
        table = query.collect(OutputFormat.ARROW)
        return {
            "study_id": workspace.study_id, "version": workspace.version, "status": workspace.status.value,
            "hypothesis": workspace.hypothesis, "dataset": workspace.input_release_id,
            "input_hash": workspace.input_content_hash, "primary_time": workspace.primary_time,
            "start": workspace.start, "end": workspace.end, "rows": table.num_rows,
            "columns": table.num_columns, "fields": tuple(table.column_names),
        }

    def profile(self) -> StudyProfile:
        rows = self.data.rows()
        fields = tuple(rows[0]) if rows else ()
        missing = sum(value is None for row in rows for value in row.values())
        primary = self.workspace.primary_time
        times = [row.get(primary) for row in rows]
        if "instrument_id" in fields:
            by_instrument: dict[object, list[object]] = {}
            for row in rows:
                by_instrument.setdefault(row.get("instrument_id"), []).append(row.get(primary))
            chronological = all(
                all(left <= right for left, right in zip(values, values[1:]))
                for values in by_instrument.values()
            )
        else:
            chronological = all(left <= right for left, right in zip(times, times[1:]))
        identities = [(row.get("instrument_id"), row.get(primary)) for row in rows]
        duplicates = len(identities) - len(set(identities))
        ohlc_fields = {"open", "high", "low", "close"}
        valid_ohlc = None
        if ohlc_fields.issubset(fields):
            valid_ohlc = all(
                row["low"] <= row["open"] <= row["high"]
                and row["low"] <= row["close"] <= row["high"]
                and row["low"] <= row["high"]
                for row in rows
            )
        point_in_time_safe = None
        if {"event_time", "available_time"}.issubset(fields):
            point_in_time_safe = all(row["event_time"] <= row["available_time"] for row in rows)
        passed = bool(rows) and missing == 0 and duplicates == 0 and chronological
        passed = passed and valid_ohlc is not False and point_in_time_safe is not False
        return StudyProfile(len(rows), len(fields), missing, duplicates, chronological, valid_ohlc, point_in_time_safe, passed)

    def scaffold(self) -> Path:
        directory = self.root/"study-workspaces"/self.workspace.study_id/self.workspace.version
        target = directory/"research.py"
        source = f'''"""Flexible exploration for {self.workspace.study_id}@{self.workspace.version}."""

from kairos.study_platform import open_study


study = open_study({self.workspace.study_id!r}, root={str(self.root)!r}, version={self.workspace.version!r})
df = study.data.pandas()

print(df.head().to_string(index=False))
print("\\nData quality")
print(study.profile().as_dict())
'''
        if target.exists() and target.read_text(encoding="utf-8") != source:
            raise ValueError(f"research scaffold already exists with user changes: {target}")
        if not target.exists():
            target.write_text(source, encoding="utf-8")
        return target


def open_study(study_id: str, *, root: str | Path = "data", version: str = "1.0.0") -> StudySession:
    repository = StudyWorkspaceRepository(root)
    workspace = repository.load(study_id, version)
    if workspace.input_release_id == "fixture:sma-bars-v1":
        from .tutorial_data import ensure_sma_tutorial_dataset, tutorial_sma_bars
        release = ensure_sma_tutorial_dataset(root)
        if workspace.input_content_hash == _LEGACY_SMA_TUTORIAL_HASH:
            bars = tutorial_sma_bars()
            workspace = repository.migrate_sandbox_input(
                study_id, version, expected_content_hash=_LEGACY_SMA_TUTORIAL_HASH,
                input_release_id=release.release_id, input_content_hash=str(release.content_hash),
                primary_time="available_time", start=bars[0].end.isoformat(),
                end=(bars[-1].end + timedelta(hours=1)).isoformat(),
                reason="bind legacy in-memory SMA fixture to governed Dataset Release",
            )
    return StudySession(root, workspace)


def _describe_table(description: dict[str, object]) -> str:
    rows = [
        ("Study ID", description.get("study_id")),
        ("Version", description.get("version")),
        ("Status", description.get("status")),
        ("Dataset release", description.get("dataset")),
        ("Content hash", _short_hash(description.get("input_hash"))),
        ("Primary time", description.get("primary_time")),
        ("Range", f"[{description.get('start')}, {description.get('end')})"),
        ("Rows", f"{int(description.get('rows', 0)):,}"),
        ("Columns", description.get("columns")),
        ("Fields", ", ".join(str(item) for item in description.get("fields", ()))),
        ("Hypothesis", description.get("hypothesis")),
    ]
    key_width = max(len(label) for label, _ in rows)
    value_width = min(120, max(len(str(value)) for _, value in rows))
    border = "+" + "-" * (key_width + 2) + "+" + "-" * (value_width + 2) + "+"
    lines = [border, f"| {'Field'.ljust(key_width)} | {'Value'.ljust(value_width)} |", border]
    for label, value in rows:
        rendered = str(value)
        if len(rendered) > value_width:
            rendered = rendered[: value_width - 3] + "..."
        lines.append(f"| {label.ljust(key_width)} | {rendered.ljust(value_width)} |")
    lines.append(border)
    return "\n".join(lines)


def _short_hash(value: object) -> str:
    text = str(value or "")
    return text if len(text) <= 20 else f"{text[:12]}...{text[-8:]}"
