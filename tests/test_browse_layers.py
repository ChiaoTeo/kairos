from kairospy.application.browsing import ListQuery, query_rows
from kairospy.application.browsing.engine import query_rows as engine_query_rows
from kairospy.application.browsing.models import ListQuery as ModelListQuery
from kairospy.surface.tui import KairosTuiApp, ResourceBrowserApp, ResourceBrowserScreen, ResourceList, ResourceListBrowser
from kairospy.surface.tui.screens import ReferenceAssetsScreen
from kairospy.surface.tui.widgets import JsonDetail, QueryBar, ResourceTable


def test_browsing_layers_expose_resources_and_tui_boundaries() -> None:
    assert ListQuery is ModelListQuery
    assert query_rows is engine_query_rows
    assert ResourceListBrowser is not None
    assert ResourceBrowserApp is not None
    assert ResourceBrowserScreen is not None
    assert KairosTuiApp is not None
    assert QueryBar is not None
    assert ResourceTable is not None
    assert JsonDetail is not None
    assert ReferenceAssetsScreen is not None


def test_resource_list_browser_builds_resource_list_from_rows() -> None:
    browser = ResourceListBrowser.from_rows(({"symbol": "BTC", "kind": "crypto"},), columns=("symbol",), title="Assets")

    assert isinstance(browser.resource, ResourceList)
    assert browser.resource.title == "Assets"
    assert browser.resource.columns == ("symbol",)
    assert browser.resource.rows == ({"symbol": "BTC", "kind": "crypto"},)


def test_resource_list_browser_can_start_from_resource_list() -> None:
    resource = ResourceList.from_rows(
        ({"symbol": "BTC", "kind": "crypto"},),
        columns=("symbol",),
        title="Assets",
        query=ListQuery(text="btc", page_size=7),
    )

    browser = ResourceListBrowser(resource)

    assert browser.resource.title == "Assets"
    assert browser.resource.columns == ("symbol",)
    assert browser.query.text == "btc"
    assert browser.query.page_size == 7


def test_resource_list_preserves_minimal_screen_input() -> None:
    resource = ResourceList.from_rows(
        ({"asset_id": "btc", "symbol": "BTC"},),
        columns=("asset_id", "symbol"),
        title="Reference Assets",
        query=ListQuery(page_size=5, expression="[?symbol == 'BTC']"),
    )

    assert resource.title == "Reference Assets"
    assert resource.columns == ("asset_id", "symbol")
    assert resource.query is not None
    assert resource.query.page_size == 5
    assert resource.query.expression == "[?symbol == 'BTC']"
