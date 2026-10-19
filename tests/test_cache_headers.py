"""The HTML must never be cached; the hashed assets always can be.

Reported from real use: a phone showed the current build fingerprint in the
header while running a bundle two versions old, so a feature that had shipped
was simply missing with nothing to explain it. The fingerprint is read from the
API -- it reports what the SERVER holds -- while the browser was replaying an
index.html it had cached, which names the old bundle. The deploy was fine. The
HTML was stale and said nothing about it.

Asset filenames carry a content hash, so they are immutable by construction.
index.html is the one file that has to be re-fetched, because it is what names
them.
"""

from __future__ import annotations


def test_index_html_is_never_cached(client):
    response = client.get("/")
    assert response.status_code == 200
    cache = response.headers.get("cache-control", "")
    assert "no-store" in cache or "no-cache" in cache, cache


def test_a_client_route_is_never_cached(client):
    # Deep links serve the same HTML and must not be cached either.
    cache = client.get("/waivers").headers.get("cache-control", "")
    assert "no-store" in cache or "no-cache" in cache, cache


def test_hashed_assets_are_cached_hard(client):
    from pathlib import Path

    import app.main as main

    assets = Path(main.STATIC_DIR) / "assets"
    bundle = next((p for p in assets.glob("index-*.js")), None)
    if bundle is None:
        return      # no built frontend in this checkout; nothing to assert

    response = client.get(f"/assets/{bundle.name}")
    assert response.status_code == 200
    cache = response.headers.get("cache-control", "")
    assert "max-age=31536000" in cache and "immutable" in cache, cache


def test_the_api_is_left_alone(client):
    # The API has its own caching story; this middleware must not rewrite it.
    cache = client.get("/api/health").headers.get("cache-control", "")
    assert "no-store" not in cache, cache
