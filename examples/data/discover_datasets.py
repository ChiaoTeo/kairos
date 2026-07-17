from trading.data import DataCatalog


catalog = DataCatalog("data")
for product in catalog.search(asset_class="option"):
    releases = catalog.releases(product)
    print(product.key, product.title, product.dimensions)
    for release in releases:
        print(" ", release.release_id, release.provider, release.venue, release.quality_level.value)
