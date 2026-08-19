"""The four server-rendered pages must actually render, and the vendored
assets (no CDN) must be served. See docs/10-phase-6-finops-ui.md §6.4-6.5."""

import time
from unittest.mock import patch


def _wait_for_status(client, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()["status"]
        if status != "running":
            return status
        time.sleep(0.1)
    raise TimeoutError(f"run {run_id} did not reach a terminal status in {timeout}s")


def test_index_page_renders_as_pure_marketing(client):
    """/ is a marketing page now — no DB query, no register form, no project
    grid. That functionality moved to /dashboard. See docs/24-landing-and-docs.md."""
    r = client.get("/")
    assert r.status_code == 200
    assert "DeployMint" in r.text
    assert "Ship secure infrastructure" in r.text
    assert 'href="/dashboard"' in r.text
    assert 'name="repo_path"' not in r.text  # the register form lives elsewhere now


def test_dashboard_page_renders_with_no_projects(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Register a project" in r.text


def test_dashboard_page_lists_a_registered_project(client, registered_project):
    r = client.get("/dashboard")
    assert registered_project["name"] in r.text


def test_dashboard_shows_a_folder_picker_for_real_workspace_dirs(client, sample_repo):
    """A browser <input type=file webkitdirectory> can't hand JS an absolute
    path at all, so the picker is server-driven: a <select> listing what's
    actually mounted under ./projects. See docs/25-folder-picker.md."""
    r = client.get("/dashboard")
    assert 'id="folder-select"' in r.text
    assert f'value="/workspace/{sample_repo.name}"' in r.text
    assert 'name="repo_path"' in r.text


def test_dashboard_falls_back_to_a_text_input_with_no_workspace_dirs(client):
    r = client.get("/dashboard")
    assert 'id="folder-select"' not in r.text
    assert 'placeholder="/workspace/my-app"' in r.text


def test_register_via_form_creates_a_project(client, sample_repo):
    r = client.post("/projects/register", data={
        "name": "form-registered", "repo_path": str(sample_repo)},
        follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/projects/")


def test_register_via_form_sanitizes_the_name_like_the_json_api_does(client, sample_repo):
    """Regression test for a real bug (docs/22-naming.md): this form built a
    Project straight from the raw field, skipping the sanitization
    ProjectCreate's pydantic validator applies on the JSON API path. A name
    with a space would reach the DB unchanged and only fail much later, at
    the Execution stage, as an invalid Docker image tag."""
    r = client.post("/projects/register", data={
        "name": "Bew Proj", "repo_path": str(sample_repo)},
        follow_redirects=False)
    assert r.status_code == 303
    project_id = r.headers["location"].rsplit("/", 1)[-1]
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["name"] == "bew-proj"
    assert " " not in project["name"]
    assert project["name"] == project["name"].lower()


def test_register_via_form_rejects_an_all_punctuation_name(client, sample_repo):
    r = client.post("/projects/register", data={
        "name": "---", "repo_path": str(sample_repo)})
    assert r.status_code == 400


def _ajax_register(client, **data):
    return client.post(
        "/projects/register", data=data,
        headers={"X-Requested-With": "fetch"}, follow_redirects=False)


def test_register_via_fetch_returns_json_on_success(client, sample_repo):
    """app.js's wireRegisterForm() submits with this header specifically so
    a duplicate-name 409 can show a rename modal instead of a dead-end page
    reload. See docs/27-rename-modal.md."""
    r = _ajax_register(client, name="fetch-registered", repo_path=str(sample_repo))
    assert r.status_code == 201
    assert r.json()["redirect"].startswith("/projects/")


def test_register_via_fetch_returns_json_400_on_bad_name(client, sample_repo):
    r = _ajax_register(client, name="---", repo_path=str(sample_repo))
    assert r.status_code == 400
    assert "error" in r.json()


def test_register_via_fetch_returns_json_409_on_duplicate_name(client, sample_repo):
    first = _ajax_register(client, name="dup-app", repo_path=str(sample_repo))
    assert first.status_code == 201

    second = _ajax_register(client, name="dup-app", repo_path=str(sample_repo))
    assert second.status_code == 409
    assert "already exists" in second.json()["error"]


def test_register_via_plain_form_post_is_unaffected_by_the_fetch_path(client, sample_repo):
    """No X-Requested-With header at all — the original non-JS form
    submission still redirects exactly like before."""
    r = client.post("/projects/register", data={
        "name": "plain-form-app", "repo_path": str(sample_repo)},
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


def _set_run_fields(run_id, **fields):
    """Directly patches a Run row's JSONB columns — used to exercise
    run.html's rendering of deployment/security/errors data without needing
    a real docker/kubectl execution (that path is covered separately by the
    slow, infra-gated tests in test_execution.py)."""
    from deploymint.db.database import get_session_factory
    from deploymint.db.models import Run

    Session = get_session_factory()
    with Session() as db:
        run = db.get(Run, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        db.commit()


def test_run_page_renders_errors_section_for_a_failed_run(client, registered_project):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_status(client, run_id)
    _set_run_fields(run_id, status="failed",
                    errors=["execution: invalid tag 'x y': invalid reference format"])

    r = client.get(f"/runs/{run_id}")
    assert 'id="run-errors"' in r.text
    assert "invalid reference format" in r.text
    assert "<div class=\"section-label\">Errors</div>" in r.text


def test_run_page_shows_notes_not_errors_for_a_successful_run_with_a_resilience_note(
    client, registered_project
):
    """Regression test: run.errors also carries benign resilience-path notes
    (e.g. Smith falling back to the deterministic template because no LLM
    key is configured) on a run that still succeeded — labeling that as a
    bright-red 'Error' next to a green success badge would be alarming and
    misleading. See docs/23-ui-evidence.md."""
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    status = _wait_for_status(client, run_id)
    assert status == "success"

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["errors"], "expected the template-fallback resilience note in errors"

    r = client.get(f"/runs/{run_id}")
    assert 'id="run-errors"' in r.text
    assert "<div class=\"section-label\">Notes</div>" in r.text
    assert "<div class=\"section-label\">Errors</div>" not in r.text


def test_run_page_omits_errors_section_when_empty(client, registered_project):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_status(client, run_id)
    _set_run_fields(run_id, errors=[])

    r = client.get(f"/runs/{run_id}")
    assert 'id="run-errors"' not in r.text


def test_run_page_renders_deployment_evidence_card(client, registered_project):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_status(client, run_id)
    _set_run_fields(run_id, deployment={
        "image_tag": "deploymint/sample:run123", "mode": "docker",
        "container_id": "abc123def456", "local_url": "http://localhost:32100",
        "status": "running", "build_log": "Step 1/5 : FROM python:3.11-slim\n...",
        "kubectl_output": "",
    })

    r = client.get(f"/runs/{run_id}")
    assert "Deployment &amp; Evidence" in r.text
    assert "deploymint/sample:run123" in r.text
    assert "http://localhost:32100" in r.text
    assert "Step 1/5" in r.text  # build log present in a <details> block
    assert "Checkov" in r.text and "OPA" in r.text and "Red Team" in r.text


def test_run_page_deployment_card_shows_no_deployment_message_when_skipped(
    client, registered_project
):
    run_id = client.post(
        f"/api/projects/{registered_project['id']}/runs", json={"skip_deploy": True}
    ).json()["run_id"]
    _wait_for_status(client, run_id)

    r = client.get(f"/runs/{run_id}")
    assert "No deployment for this run" in r.text


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
