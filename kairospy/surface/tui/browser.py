from __future__ import annotations

import json
import sys
from typing import Mapping, Sequence, TextIO

from kairospy.application.browsing import ListQuery, query_rows
from kairospy.surface.tui.app import ResourceBrowserApp
from kairospy.surface.tui.models import DetailReader, ResourceList, SaveEditor


class ResourceListBrowser:
    """Pipe-safe list browser that uses Textual when a real TTY is available."""

    def __init__(
        self,
        resource: ResourceList,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.resource = resource
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.query = resource.query or ListQuery()

    @classmethod
    def from_rows(
        cls,
        rows: Sequence[Mapping[str, object]],
        *,
        columns: Sequence[str] | None = None,
        detail: DetailReader | None = None,
        save: SaveEditor | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        page_size: int = 20,
        query: str | None = None,
        title: str = "Browse",
    ) -> ResourceListBrowser:
        return cls(
            ResourceList.from_rows(
                rows,
                columns=tuple(columns or ()),
                detail=detail,
                save=save,
                page_size=page_size,
                query=ListQuery(page_size=page_size, expression=query),
                title=title,
            ),
            stdin=stdin,
            stdout=stdout,
        )

    def run(self) -> None:
        if self._supports_fullscreen():
            self.resource.query = self.query
            ResourceBrowserApp(self.resource).run()
            return
        self._run_line_mode()

    def _supports_fullscreen(self) -> bool:
        try:
            return bool(self.stdin.isatty() and self.stdout.isatty())
        except (AttributeError, OSError):
            return False

    def _run_line_mode(self) -> None:
        self._print_page()
        while True:
            self.stdout.write("browse> ")
            self.stdout.flush()
            line = self.stdin.readline()
            if line == "":
                return
            command = line.strip()
            if not command:
                continue
            if command in {"q", "quit", "exit"}:
                return
            try:
                if not self._handle(command):
                    return
            except ValueError as error:
                self._write(f"error: {error}")

    def _handle(self, command: str) -> bool:
        if command in {"n", "next"}:
            self.query = self._replace_page(self.query.page + 1)
        elif command in {"p", "prev", "previous"}:
            self.query = self._replace_page(max(1, self.query.page - 1))
        elif command.startswith("/"):
            self.query = self._replace(text=command[1:].strip() or None, page=1)
        elif command.startswith("query "):
            self.query = self._replace(expression=command[6:].strip() or None, page=1)
        elif command.startswith("filter "):
            key, separator, value = command[7:].partition("=")
            if not separator:
                raise ValueError("filter must use key=value")
            filters = tuple(item for item in self.query.filters if item[0] != key.strip())
            self.query = self._replace(filters=(*filters, (key.strip(), value.strip())), page=1)
        elif command in {"clear", "clear-filter"}:
            self.query = self._replace(text=None, filters=(), expression=None, page=1)
        elif command.startswith("sort "):
            value = command[5:].strip()
            self.query = self._replace(sort=value.removeprefix("-") or None, descending=value.startswith("-"), page=1)
        elif command.startswith("size "):
            self.query = self._replace(page_size=int(command[5:].strip()), page=1)
        elif command.startswith("page "):
            self.query = self._replace(page=max(1, int(command[5:].strip())))
        elif command.startswith("open "):
            self._open(command[5:].strip())
            return True
        elif command == "json":
            result = query_rows(self.resource.rows, self.query, columns=self.resource.columns)
            self._write(json.dumps(result.to_dict(), ensure_ascii=False, default=str, indent=2))
            return True
        elif command in {"help", "?"}:
            self._write("n/p page  /text search  query JMESPATH  filter key=value  sort field[-]  size N  open N  json  clear  q")
            return True
        else:
            raise ValueError("unknown command; use help")
        self._print_page()
        return True

    def _open(self, target: str) -> None:
        result = query_rows(self.resource.rows, self.query, columns=self.resource.columns)
        try:
            index = int(target) - 1
            row = result.rows[index]
        except (ValueError, IndexError) as error:
            raise ValueError("open expects a row number on the current page") from error
        payload = self.resource.detail(row) if self.resource.detail else row
        self._write(json.dumps(payload, ensure_ascii=False, default=str, indent=2))

    def _print_page(self) -> None:
        result = query_rows(self.resource.rows, self.query, columns=self.resource.columns)
        self._write(f"page {result.page}/{result.total_pages}  ({result.total_rows} rows)")
        if not result.rows:
            self._write("No rows.")
            return
        self._write("  ".join(("#", *result.columns)))
        for index, row in enumerate(result.rows, start=1):
            cells = ("-" if row.get(column) is None else str(row.get(column)) for column in result.columns)
            self._write("  ".join((str(index), *cells)))

    def _replace_page(self, page: int) -> ListQuery:
        return self._replace(page=page)

    def _replace(self, **changes: object) -> ListQuery:
        values = {
            "text": self.query.text,
            "filters": self.query.filters,
            "expression": self.query.expression,
            "sort": self.query.sort,
            "descending": self.query.descending,
            "limit": self.query.limit,
            "page": self.query.page,
            "page_size": self.query.page_size,
        }
        values.update(changes)
        return ListQuery(**values)

    def _write(self, value: str) -> None:
        self.stdout.write(value + "\n")
        self.stdout.flush()


__all__ = ["ResourceListBrowser"]
