"""The four server-rendered pages must actually render, and the vendored
assets (no CDN) must be served. See docs/10-phase-6-finops-ui.md §6.4-6.5."""

from unittest.mock import patch


def test_index_page_renders_with_no_projects(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "DeployMint" in r.text


def test_index_page_lists_a_registered_project(client, registered_project):
    r = client.get("/")
    assert registered_project["name"] in r.text


def test_register_via_form_creates_a_project(client, sample_repo):
    r = client.post("/projects/register", data={
        "name": "form-registered", "repo_path": str(sample_repo)},
        follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/projects/")


def test_project_page_renders(client, registered_project):
    r = client.get(f"/projects/{registered_project['id']}")
    assert r.status_code == 200
    assert registered_project["name"] in r.text


def test_project_page_shows_critical_files_after_analysis(client, registered_project):
    pid = registered_project["id"]
    client.post(f"/api/projects/{pid}/analyze")
    r = client.get(f"/projects/{pid}")
    assert "Most critical files" in r.text
    assert "db.py" in r.text
    assert 'data-critical=' in r.text


def test_project_page_404s_for_unknown_project(client):
    r = client.get("/projects/999999")
    assert r.status_code == 404


def test_deploy_button_calls_start_run_and_redirects(client, registered_project):
    """Mocked — the real build+deploy path (what start_run() actually does
    when not skipped) is already verified end to end by test_execution.py
    against the live cluster; this only needs to prove the button is wired
    to start_run() and redirects to the new run's page."""
    with patch("deploymint.web.routes.start_run", return_value="run_mocked456") as mock_start:
        r = client.post(f"/projects/{registered_project['id']}/deploy", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/runs/run_mocked456"
    mock_start.assert_called_once()


def test_run_page_renders(client, registered_project):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    r = client.get(f"/runs/{run_id}")
    assert r.status_code == 200
    assert run_id in r.text


def test_run_page_has_artifact_tabs_not_bare_links(client, registered_project):
    import time

    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if client.get(f"/api/runs/{run_id}").json()["status"] != "running":
            break
        time.sleep(0.1)
    r = client.get(f"/runs/{run_id}")
    assert 'id="artifact-tabs"' in r.text
    assert 'data-file="Dockerfile"' in r.text
    assert 'id="artifact-preview"' in r.text
    assert "target=\"_blank\"" not in r.text


def test_run_page_passes_its_actual_status_to_connect_run(client, registered_project):
    """Regression test: connectRun() must know whether the run was ALREADY
    finished when the page loaded, so a replay of an already-finished run's
    persisted run.end event doesn't re-trigger the reload-on-completion
    logic — that reload would reconnect, replay run.end again, reload again,
    forever. Found by watching a real deploy finish in the browser and
    seeing the page reload in an infinite loop."""
    import time

    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    deadline = time.monotonic() + 30
    status = "running"
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()["status"]
        if status != "running":
            break
        time.sleep(0.1)
    assert status == "success"

    r = client.get(f"/runs/{run_id}")
    assert f'connectRun("{run_id}", 0, "{status}")' in r.text


def test_run_page_html_lists_each_finding_only_once(client, registered_project):
    """Guards the server-rendered half of a real duplication bug: run.html
    renders run.security.findings from the DB once. The other half — app.js
    appending the SAME findings again from replayed warden.finding/
    redteam.probe WS events — can only be caught with a real browser JS
    engine (no headless JS execution in this test suite), so that half was
    verified manually. See the guard added in app.js's WS message handler:
    it now skips appending findings via JS when the run was already
    finished on page load. Found in the browser only after fixing the
    reload-loop bug (below) stopped hiding it."""
    import time

    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    deadline = time.monotonic() + 30
    run = None
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            break
        time.sleep(0.1)
    assert run["status"] == "success"
    findings = run["security"]["findings"]
    assert findings  # the clean template output still has non-blocking findings

    r = client.get(f"/runs/{run_id}")
    first = findings[0]
    assert r.text.count(first["id"]) == 1


def test_run_page_404s_for_unknown_run(client):
    r = client.get("/runs/run_doesnotexist")
    assert r.status_code == 404


def test_costs_page_renders_with_breakdown(client):
    r = client.get("/costs")
    assert r.status_code == 200
    assert "487.12" in r.text


def test_vendored_assets_are_served_with_no_cdn_reference(client):
    r = client.get("/static/vendor/htmx.min.js")
    assert r.status_code == 200
    r2 = client.get("/static/vendor/cytoscape.min.js")
    assert r2.status_code == 200
    home = client.get("/")
    assert "unpkg.com" not in home.text
    assert "cdn." not in home.text
