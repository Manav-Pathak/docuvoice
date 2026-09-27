from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_synthetic_offline_flow_to_pdf():
    session = client.post("/api/sessions", json={"mode": "offline"}).json()
    session_id = session["id"]

    response = client.post(f"/api/sessions/{session_id}/demo")
    assert response.status_code == 200
    assert response.json()["documents"][0]["extraction_method"] == "synthetic_demo"

    response = client.post(
        f"/api/sessions/{session_id}/form",
        json={"schema_id": "personal_accident_claim"},
    )
    assert response.status_code == 200
    assert response.json()["form"]["fields"]["full_name"]["value"] == "Aarav Sharma"

    response = client.post(
        f"/api/sessions/{session_id}/voice/command",
        json={"transcript": "Change my pincode to four zero zero zero five six"},
    )
    assert response.status_code == 200
    assert response.json()["session"]["form"]["fields"]["pincode"]["value"] == "400056"

    response = client.get(f"/api/sessions/{session_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_online_mode_failure_preserves_offline_state():
    session = client.post("/api/sessions", json={"mode": "offline"}).json()
    response = client.patch(
        f"/api/sessions/{session['id']}/mode", json={"mode": "online"}
    )
    if response.status_code == 409:
        preserved = client.get(f"/api/sessions/{session['id']}").json()
        assert preserved["mode"] == "offline"
