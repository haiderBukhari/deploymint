def test_register_analyze_flow(client, sample_repo):
    r = client.post("/api/projects", json={"name": "flow", "repo_path": str(sample_repo)})
    assert r.status_code == 201
    pid = r.json()["id"]

    a = client.post(f"/api/projects/{pid}/analyze")
    assert a.status_code == 200
    data = a.json()
    assert data["language"] == "python"
    assert data["framework"] == "fastapi"
    assert data["exposed_port"] == 8000
    assert len(data["graph"]["nodes"]) >= 4


def test_duplicate_name_conflicts(client, sample_repo):
    body = {"name": "dup", "repo_path": str(sample_repo)}
    assert client.post("/api/projects", json=body).status_code == 201
    assert client.post("/api/projects", json=body).status_code == 409


def test_system_path_rejected(client):
    r = client.post("/api/projects", json={"name": "evil", "repo_path": "/"})
    assert r.status_code == 400


def test_outside_workspace_rejected(client):
    r = client.post("/api/projects", json={"name": "evil2", "repo_path": "/etc"})
    assert r.status_code == 400


def test_list_and_get_and_delete(client, sample_repo):
    pid = client.post(
        "/api/projects", json={"name": "crud", "repo_path": str(sample_repo)}
    ).json()["id"]

    assert len(client.get("/api/projects").json()) == 1
    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert client.get("/api/projects/9999").status_code == 404

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_graph_before_analyze_is_400(client, sample_repo):
    pid = client.post(
        "/api/projects", json={"name": "nograph", "repo_path": str(sample_repo)}
    ).json()["id"]
    assert client.get(f"/api/projects/{pid}/graph").status_code == 400


def test_health_and_doctor(client):
    assert client.get("/health").json()["status"] == "ok"
    doctor = client.get("/api/doctor").json()
    names = {c["name"] for c in doctor["checks"]}
    assert "database" in names
    assert "workspace" in names
