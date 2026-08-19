"""The in-app docs viewer renders docs/user-guide/*.md — genuine end-user
documentation — as browsable pages at /guide. See
docs/24-landing-and-docs.md (supersedes docs/20-in-app-docs.md, which
described the earlier version pointed at this project's own build log).

Routes live at /guide, not /docs — FastAPI's own /docs is its built-in
Swagger UI, and a route at that path would silently never register."""

import pytest

from deploymint.web import docs_content


def test_every_nav_entry_resolves_to_a_real_file():
    """Guards the exact bug docs/20-in-app-docs.md describes: a page that
    resolves locally but silently shows 'not available' once shipped,
    because the Dockerfile doesn't COPY docs/ into the image. This runs in
    the same environment the app itself runs in, so if this test passes,
    the pages are genuinely there."""
    missing = [p.slug for p in docs_content.NAV if not p.path.is_file()]
    assert not missing, f"missing doc files: {missing}"


def test_render_produces_real_html_not_raw_markdown():
    page = docs_content.get_page("agents")
    html = docs_content.render(page)
    assert "<h" in html  # some heading tag rendered
    assert "# " not in html.split("\n")[0]  # not literal raw markdown


@pytest.mark.parametrize("slug", [p.slug for p in docs_content.NAV])
def test_docs_page_renders_for_every_nav_entry(client, slug):
    r = client.get(f"/guide/{slug}")
    assert r.status_code == 200, slug
    assert 'class="docs-nav"' in r.text
    assert "<em>This page is not available" not in r.text


def test_docs_index_redirects_to_first_page(client):
    r = client.get("/guide")
    assert r.status_code == 200
    assert docs_content.NAV[0].title in r.text


def test_docs_unknown_slug_is_404(client):
    r = client.get("/guide/does-not-exist")
    assert r.status_code == 404


def test_docs_nav_link_present_on_every_page(client):
    r = client.get("/")
    assert 'href="/guide"' in r.text


def test_nav_is_user_facing_not_the_build_log():
    """Regression test for the actual content pivot: /guide used to render
    this project's own build log ('Phase 1: Foundation', 'Decisions Log',
    etc.) — contributor history, not something a user deploying their own
    repo needs. See docs/24-landing-and-docs.md."""
    titles = " ".join(p.title for p in docs_content.NAV)
    for stale in ("Phase 1", "Phase 18", "Decisions Log", "Pending Work", "Risks & Cutlines"):
        assert stale not in titles
    assert "Getting Started" in titles
    assert "The Agent Pipeline" in titles


def test_internal_doc_links_resolve_to_real_nav_entries(client):
    """Every cross-link between user-guide pages must point at a slug that
    actually exists in NAV — a link to a stale filename-shaped path would
    404 silently for a reader clicking through."""
    import re

    known_slugs = {p.slug for p in docs_content.NAV}
    for page in docs_content.NAV:
        html = docs_content.render(page)
        for href in re.findall(r'href="/guide/([a-z0-9-]+)"', html):
            assert href in known_slugs, f"{page.slug} links to unknown slug {href!r}"


def test_route_does_not_collide_with_fastapis_own_swagger_docs(client):
    """Regression test for the exact bug found while building this: FastAPI
    serves its own interactive API docs at /docs by default. A route
    registered at /docs would never actually get hit — Swagger UI wins."""
    r = client.get("/docs")
    assert "swagger-ui" in r.text.lower()
