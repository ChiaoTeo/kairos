from __future__ import annotations

from typing import Mapping, Protocol

from kairospy.surface.state import SurfaceSnapshot, render_run_strip, render_surface_overview


class ProductView(Protocol):
    name: str
    label: str
    description: str
    maturity: object


def render_home_screen(snapshot: SurfaceSnapshot, products: tuple[ProductView, ...]) -> str:
    return "\n\n".join([
        render_surface_overview(snapshot),
        render_product_registry(products),
        render_home_commands(),
    ])


def render_run_workspace(snapshot: SurfaceSnapshot, run_menu: str) -> str:
    return "\n\n".join([
        render_surface_overview(snapshot),
        render_run_strip(snapshot),
        run_menu,
    ])


def render_raw_product_screen(snapshot: SurfaceSnapshot, product: ProductView) -> str:
    return "\n\n".join([
        render_surface_overview(snapshot),
        render_product_detail(product),
        render_raw_bridge_commands(product),
    ])


def render_reference_panel_screen(
    snapshot: SurfaceSnapshot,
    product: ProductView,
    summary: Mapping[str, object],
) -> str:
    return "\n\n".join([
        render_surface_overview(snapshot),
        render_product_detail(product),
        render_reference_summary(summary),
        render_reference_commands(),
    ])


def render_product_registry(products: tuple[ProductView, ...]) -> str:
    lines = [
        "Products",
        "  #  product       maturity   description",
        "  -  ------------  ---------  -----------",
    ]
    for index, product in enumerate(products, start=1):
        lines.append(
            f"  {index:<2} {product.name:<12}  {str(product.maturity):<9}  {product.description}"
        )
    return "\n".join(lines)


def render_product_detail(product: ProductView) -> str:
    return "\n".join([
        f"{product.label}",
        f"  maturity  {product.maturity}",
        f"  product   {product.name}",
        f"  scope     {product.description}",
    ])


def render_home_commands() -> str:
    return "\n".join([
        "Commands",
        "  <#>|<product>   open product",
        "  refresh         reload app state",
        "  quit            exit app",
    ])


def render_raw_bridge_commands(product: ProductView) -> str:
    return "\n".join([
        "Raw Command Bridge",
        f"  Type commands exactly as `kairospy {product.name} ...`, without the product prefix.",
        "  help       show product CLI help",
        "  back       return to products",
    ])


def render_reference_summary(summary: Mapping[str, object]) -> str:
    lines = [
        "Catalog",
        f"  root         {summary.get('root', '')}",
        f"  database     {summary.get('database', '')}",
    ]
    if summary.get("error"):
        lines.append(f"  error        {summary['error']}")
        return "\n".join(lines)
    for key in ("entities", "assets", "instruments", "listings", "markets", "events"):
        lines.append(f"  {key:<12} {summary.get(key, 0)}")
    return "\n".join(lines)


def render_reference_commands() -> str:
    return "\n".join([
        "Reference Panel",
        "  search <query>                       search instruments, listings, markets, assets",
        "  show <id>                            show one reference identifier",
        "  resolve <symbol> --venue <venue>      resolve tradable market identity",
        "  list [filters]                       list catalog markets",
        "  markets [filters]                    stream matching market rows",
        "  events [--limit n]                    stream lifecycle events",
        "  catalog status                       summarize catalog cache",
        "  refresh --provider massive [options] refresh provider catalog",
        "  refresh hyperliquid [options]         refresh Hyperliquid catalog",
        "  refresh-binance [options]             refresh Binance catalog",
        "  refresh-massive-equities [options]    refresh equity catalog",
        "  sync-massive-actions [options]        sync equity lifecycle events",
        "  back                                  return to products",
    ])


__all__ = [
    "render_home_commands",
    "render_home_screen",
    "render_product_detail",
    "render_product_registry",
    "render_raw_bridge_commands",
    "render_raw_product_screen",
    "render_reference_commands",
    "render_reference_panel_screen",
    "render_reference_summary",
    "render_run_workspace",
]
