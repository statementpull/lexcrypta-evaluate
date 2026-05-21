def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_license_status_not_activated(client):
    r = client.get("/license-status")
    assert r.status_code == 200
    assert r.json()["activated"] is False


def test_activate_license_demo_key(client):
    r = client.post("/activate-license", json={"key": "LEXV-DEMO-2026-BARTILOTTA"})
    assert r.status_code == 200
    assert r.json()["activated"] is True


def test_create_matter(client):
    client.post("/activate-license", json={"key": "LEXV-DEMO-2026-BARTILOTTA"})
    r = client.post("/matters", data={
        "subject": "Test Subject",
        "ref": "TEST-001",
        "type": "bankruptcy",
        "matter_date": "01 Jan 2025",
        "assigned_to": "Tester",
        "notes": "",
    })
    assert r.status_code == 200
    assert r.json()["subject"] == "Test Subject"
    assert r.json()["id"] is not None


def test_list_matters(client):
    client.post("/activate-license", json={"key": "LEXV-DEMO-2026-BARTILOTTA"})
    client.post("/matters", data={"subject": "A", "ref": "A-001", "type": "civil",
                                   "matter_date": "", "assigned_to": "", "notes": ""})
    r = client.get("/matters")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_purge_matter(client):
    client.post("/activate-license", json={"key": "LEXV-DEMO-2026-BARTILOTTA"})
    create_r = client.post("/matters", data={"subject": "Purge Me", "ref": "P-001",
                                              "type": "civil", "matter_date": "",
                                              "assigned_to": "", "notes": ""})
    mid = create_r.json()["id"]
    r = client.delete(f"/matters/{mid}/purge")
    assert r.status_code == 200
    matters = client.get("/matters").json()
    assert not any(m["id"] == mid for m in matters)
