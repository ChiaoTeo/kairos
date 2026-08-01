from kairospy.application.support.system.browsing import ListQuery
from kairospy.surface.tui.screens import replace_query
from kairospy.surface.tui.widgets import filters_text, sort_text


def test_textual_browser_preserves_query_state_when_replacing_page() -> None:
    query = ListQuery(
        text="btc",
        filters=(("kind", "crypto"),),
        sort="symbol",
        descending=True,
        limit=100,
        page=2,
        page_size=25,
    )

    replaced = replace_query(query, page=3)

    assert replaced.page == 3
    assert replaced.page_size == 25
    assert replaced.text == "btc"
    assert replaced.filters == (("kind", "crypto"),)
    assert replaced.limit == 100
    assert filters_text(replaced) == "kind=crypto"
    assert sort_text(replaced) == "-symbol"
