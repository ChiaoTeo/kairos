from io import StringIO

from kairospy.application.browsing import ListQuery, parse_filters, query_rows
from kairospy.surface.tui import ResourceListBrowser


def test_query_rows_filters_sorts_and_pages() -> None:
    rows = (
        {"id": "b", "kind": "crypto"},
        {"id": "a", "kind": "equity"},
        {"id": "c", "kind": "crypto"},
    )
    result = query_rows(rows, ListQuery(filters=parse_filters(["kind=crypto"]), sort="id", page_size=1), columns=("id", "kind"))

    assert result.total_rows == 2
    assert result.total_pages == 2
    assert result.rows == ({"id": "b", "kind": "crypto"},)
    assert result.to_dict()["page"] == {"page": 1, "page_size": 1, "total_rows": 2, "total_pages": 2}


def test_query_rows_supports_jmespath_filter_and_projection() -> None:
    rows = (
        {"id": "b", "kind": "crypto", "symbol": "BTC"},
        {"id": "a", "kind": "equity", "symbol": "AAPL"},
    )
    result = query_rows(
        rows,
        ListQuery(expression="[?kind == 'crypto'].{asset: id, ticker: symbol}"),
    )

    assert result.rows == ({"asset": "b", "ticker": "BTC"},)


def test_query_limit_is_applied_after_expression() -> None:
    rows = tuple({"id": str(index)} for index in range(3))

    result = query_rows(rows, ListQuery(expression="[?id == '2']", limit=1))

    assert result.total_rows == 1
    assert result.rows == ({"id": "2"},)


def test_interactive_browser_supports_search_page_and_open() -> None:
    stdin = StringIO("/btc\nopen 1\nq\n")
    stdout = StringIO()
    ResourceListBrowser.from_rows(
        ({"id": "btc", "kind": "crypto"}, {"id": "eth", "kind": "crypto"}),
        columns=("id", "kind"),
        stdin=stdin,
        stdout=stdout,
        page_size=1,
    ).run()

    output = stdout.getvalue()
    assert "page 1/2" in output
    assert '"id": "btc"' in output
    assert "browse>" in output


def test_fullscreen_query_commands_update_shared_query_state() -> None:
    browser = ResourceListBrowser.from_rows(({"id": "a"},), stdin=StringIO(), stdout=StringIO())

    browser._handle("/btc")
    browser._handle("filter kind=crypto")
    browser._handle("sort -symbol")
    browser._handle("size 10")
    browser._handle("page 3")

    assert browser.query.text == "btc"
    assert browser.query.filters == (("kind", "crypto"),)
    assert browser.query.sort == "symbol"
    assert browser.query.descending is True
    assert browser.query.page == 3
    assert browser.query.page_size == 10
