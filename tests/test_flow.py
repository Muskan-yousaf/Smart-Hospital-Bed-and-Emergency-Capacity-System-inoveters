"""End-to-end test of the core flow:
Need -> Capacity -> Matching -> Referral -> Admission -> Updated Capacity.

Run from the smart-hospital folder:  python -m pytest -q
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

CITY_CARE = 1


def login(c, user, pw):
    r = c.post("/auth/login", data={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def cap(c, h, hid, res):
    return next(x for x in c.get(f"/hospitals/{hid}", headers=h).json()["capacities"] if x["resource_type"] == res)


def test_full_referral_flow():
    with TestClient(app) as c:
        coord = login(c, "coord1", "coord123")
        staff = login(c, "staff1", "staff123")
        admin = login(c, "admin", "admin123")

        ai = c.post("/classify", headers=coord, json={"description": "50 year old man unconscious, SpO2 80%"}).json()
        assert "icu" in ai["required_resources"] and "ventilator" in ai["required_resources"]
        assert ai["urgency"] == "critical"

        ranked = c.post("/search/match", headers=coord, json={
            "required_resources": ["icu", "ventilator"], "latitude": 25.396, "longitude": 68.358,
            "urgency": "critical", "max_distance_km": 50}).json()
        assert ranked[0]["suitable"] and ranked[0]["match_percent"] > 0
        assert any(not h["suitable"] for h in ranked)  # full / far hospitals are flagged, not hidden

        icu0, vent0 = cap(c, staff, CITY_CARE, "icu"), cap(c, staff, CITY_CARE, "ventilator")
        r = c.post("/requests", headers=coord, json={
            "patient_reference": "Test Patient", "description": "unconscious", "urgency": "critical",
            "required_resources": ["icu", "ventilator"], "hospital_id": CITY_CARE,
            "latitude": 25.396, "longitude": 68.358})
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        assert c.post("/requests", headers=coord, json={
            "patient_reference": "Test Patient", "required_resources": ["icu"], "hospital_id": CITY_CARE
        }).status_code == 409  # duplicate open request blocked

        assert c.post(f"/requests/{rid}/review", headers=staff).json()["request_status"] == "hospital_reviewing"
        acc = c.post(f"/requests/{rid}/respond", headers=staff, json={"accept": True}).json()
        assert acc["request_status"] == "accepted" and acc["expires_at"]
        assert acc["confirmation_code"] == ""  # hidden from staff
        assert cap(c, staff, CITY_CARE, "icu")["available_capacity"] == icu0["available_capacity"] - 1
        assert cap(c, staff, CITY_CARE, "ventilator")["reserved_capacity"] == vent0["reserved_capacity"] + 1

        code = c.get(f"/requests/{rid}", headers=coord).json()["confirmation_code"]
        assert len(code) == 6
        assert c.post(f"/requests/{rid}/transfer", headers=coord).json()["request_status"] == "patient_transferred"
        assert c.get(f"/requests/verify/{code}", headers=staff).json()["id"] == rid
        assert c.post(f"/requests/{rid}/admit", headers=staff, json={"confirmation_code": "WRONG1"}).status_code == 400
        adm = c.post(f"/requests/{rid}/admit", headers=staff, json={"confirmation_code": code}).json()
        assert adm["request_status"] == "admitted"
        icu1 = cap(c, staff, CITY_CARE, "icu")
        assert icu1["occupied_capacity"] == icu0["occupied_capacity"] + 1
        assert icu1["reserved_capacity"] == icu0["reserved_capacity"]

        assert c.post(f"/requests/{rid}/discharge", headers=staff).json()["request_status"] == "discharged"
        assert cap(c, staff, CITY_CARE, "icu")["occupied_capacity"] == icu0["occupied_capacity"]

        assert any("admitted" in n["message"] for n in c.get("/notifications", headers=coord).json())
        assert c.get(f"/analytics/hospital/{CITY_CARE}", headers=staff).status_code == 200
        assert c.get(f"/analytics/forecast/{CITY_CARE}", headers=staff).json()["capacity"]
        sysa = c.get("/analytics/system", headers=admin).json()
        assert sysa["total_hospitals"] >= 6 and sysa["pending_hospitals"] >= 1


def test_last_bed_cannot_be_double_booked():
    with TestClient(app) as c:
        coord = login(c, "coord1", "coord123")
        patient = login(c, "patient1", "patient123")
        staff = login(c, "staff1", "staff123")
        icu = cap(c, staff, CITY_CARE, "icu")
        # leave exactly one free ICU bed
        c.put(f"/hospitals/{CITY_CARE}/capacity/icu", headers=staff,
              json={"occupied_capacity": icu["total_capacity"] - icu["reserved_capacity"] - icu["unavailable_capacity"] - 1})
        ids = [c.post("/requests", headers=h, json={"patient_reference": f"P{i}", "required_resources": ["icu"],
                                                    "hospital_id": CITY_CARE}).json()["id"]
               for i, h in enumerate((coord, patient))]
        results = [c.post(f"/requests/{i}/respond", headers=staff, json={"accept": True}).json()["request_status"]
                   for i in ids]
        assert sorted(results) == ["accepted", "no_capacity"]
        assert cap(c, staff, CITY_CARE, "icu")["available_capacity"] == 0


def test_permissions_and_validation():
    with TestClient(app) as c:
        staff = login(c, "staff1", "staff123")
        patient = login(c, "patient1", "patient123")
        assert c.put("/hospitals/2/capacity/icu", headers=staff, json={"occupied_capacity": 1}).status_code == 403
        assert c.get("/analytics/system", headers=patient).status_code == 403
        assert c.put(f"/hospitals/{CITY_CARE}/capacity/icu", headers=staff,
                     json={"occupied_capacity": 9999}).status_code == 400
        assert c.post("/auth/login", data={"username": "admin", "password": "nope"}).status_code == 401
        # a self-registered hospital is invisible to search until verified
        r = c.post("/hospitals/signup", json={"name": "New Test Hospital", "latitude": 25.4, "longitude": 68.3,
                                              "staff_username": "newstaff", "staff_password": "secret12"})
        assert r.status_code == 201 and r.json()["verification_status"] == "pending"
        assert all(h["name"] != "New Test Hospital" for h in c.get("/hospitals").json())
