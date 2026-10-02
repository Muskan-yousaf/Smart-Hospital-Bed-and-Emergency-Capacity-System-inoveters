"""Demo data. Runs automatically on first start (when the DB is empty).

Creates hospitals, users, 7 days of capacity history and past referrals so that
analytics, trends and forecasting have something to show from the first login.
"""
import json
import random
import secrets
from datetime import timedelta
from sqlalchemy import select
from .models import (User, Hospital, Capacity, CapacitySnapshot, ReferralRequest, AuditLog, Notification,
                     RESOURCE_TYPES, utcnow)
from .security import hash_password

# Fictional hospitals, approximate coordinates around Hyderabad / Jamshoro, Sindh.
# caps: resource -> (total, occupied)
HOSPITALS = [
    ("City Care Hospital", "Saddar, Hyderabad", "Hyderabad", 25.3960, 68.3578, "022-2780001",
     "emergency,cardiology,neurology,surgery,orthopedics,pulmonology",
     {"general": (100, 70), "emergency": (20, 12), "icu": (20, 17), "nicu": (10, 6), "ventilator": (12, 9),
      "operation_theatre": (6, 3), "isolation": (8, 2), "dialysis": (10, 5), "trauma": (8, 4), "ambulance": (6, 2)}),
    ("Latifabad Medical Centre", "Unit 7, Latifabad, Hyderabad", "Hyderabad", 25.3780, 68.3800, "022-2780002",
     "emergency,gynecology,surgery,pediatrics",
     {"general": (60, 48), "emergency": (10, 8), "icu": (10, 10), "nicu": (4, 2), "ventilator": (5, 5),
      "operation_theatre": (3, 1), "isolation": (4, 1), "dialysis": (4, 1), "trauma": (3, 1), "ambulance": (3, 1)}),
    ("Jamshoro University Hospital", "Jamshoro", "Jamshoro", 25.4300, 68.2800, "022-2780003",
     "emergency,cardiology,nephrology,neonatology,surgery,neurology,pulmonology,orthopedics",
     {"general": (150, 100), "emergency": (30, 20), "icu": (25, 15), "nicu": (12, 7), "ventilator": (15, 8),
      "operation_theatre": (8, 4), "isolation": (10, 3), "dialysis": (15, 9), "trauma": (10, 4), "ambulance": (8, 3)}),
    ("Kotri Community Hospital", "Main Road, Kotri", "Kotri", 25.3650, 68.3050, "022-2780004",
     "general,gynecology",
     {"general": (40, 30), "emergency": (6, 3), "icu": (4, 2), "nicu": (0, 0), "ventilator": (2, 0),
      "operation_theatre": (2, 1), "isolation": (2, 0), "dialysis": (0, 0), "trauma": (2, 1), "ambulance": (2, 1)}),
    ("Qasimabad Heart & Lung Institute", "Qasimabad, Hyderabad", "Hyderabad", 25.4050, 68.3300, "022-2780005",
     "emergency,cardiology,pulmonology",
     {"general": (50, 41), "emergency": (12, 9), "icu": (18, 16), "nicu": (0, 0), "ventilator": (14, 12),
      "operation_theatre": (4, 2), "isolation": (6, 4), "dialysis": (0, 0), "trauma": (0, 0), "ambulance": (4, 2)}),
    ("Tando Jam Rural Health Centre", "Tando Jam", "Tando Jam", 25.4270, 68.5300, "022-2780006",
     "general,pediatrics",
     {"general": (30, 27), "emergency": (4, 4), "icu": (2, 2), "nicu": (2, 1), "ventilator": (1, 1),
      "operation_theatre": (1, 0), "isolation": (2, 2), "dialysis": (0, 0), "trauma": (1, 1), "ambulance": (2, 2)}),
]
# Registered itself but not yet verified -> shows up in the admin's verification queue only.
PENDING = ("Hala Children Hospital", "Hala Road, Matiari", "Matiari", 25.5900, 68.4200, "022-2780007",
           "pediatrics,neonatology", {"general": (20, 5), "nicu": (6, 2), "emergency": (4, 1)})

PATIENTS = ["Ali Raza", "Sana Khan", "Bilal Ahmed", "Ayesha Noor", "Usman Tariq", "Fatima Shah", "Hamza Iqbal",
            "Zainab Malik", "Imran Qureshi", "Hira Baloch", "Kashif Memon", "Nida Soomro", "Asad Jamali", "Mehwish Ali"]
CASES = [
    ("Patient unconscious after heart attack, BP 80/50, needs ventilator", ["icu", "ventilator"], "cardiology", "critical"),
    ("Road accident, multiple fractures and bleeding", ["trauma"], "orthopedics", "high"),
    ("Premature newborn with breathing difficulty", ["nicu"], "neonatology", "high"),
    ("65 year old male with chest pain since morning", ["icu"], "cardiology", "high"),
    ("Kidney failure patient needs dialysis session", ["dialysis"], "nephrology", "medium"),
    ("Woman in labour, delivery expected tonight", ["emergency"], "gynecology", "high"),
    ("Suspected dengue with high fever, needs isolation", ["isolation"], None, "medium"),
    ("Acute appendicitis, surgery required", ["operation_theatre"], "surgery", "high"),
    ("Elderly patient, mild pneumonia, stable", ["general"], None, "low"),
    ("Stroke symptoms, left side paralysis", ["icu"], "neurology", "critical"),
]


def _history(rng, total, occ_now, points):
    """Random walk that ends exactly at the current value."""
    vals, v = [], occ_now
    for _ in range(points):
        vals.append(v)
        v = max(0, min(total, v + rng.choice([-2, -1, -1, 0, 0, 1, 1, 2]) * max(1, total // 20)))
    return list(reversed(vals))


def seed(db):
    if db.scalar(select(User)):
        return
    rng = random.Random(42)
    now = utcnow()

    hospitals = []
    for name, loc, city, lat, lon, phone, svc, caps in HOSPITALS:
        h = Hospital(name=name, location=loc, city=city, latitude=lat, longitude=lon, services=svc,
                     contact=phone, verification_status="verified", emergency_available=True,
                     created_at=now - timedelta(days=30))
        for r in RESOURCE_TYPES:
            total, occ = caps[r]
            h.capacities.append(Capacity(resource_type=r, total_capacity=total, occupied_capacity=occ,
                                         last_updated=now - timedelta(minutes=rng.randint(2, 40))))
        db.add(h)
        hospitals.append(h)
    name, loc, city, lat, lon, phone, svc, caps = PENDING
    pending = Hospital(name=name, location=loc, city=city, latitude=lat, longitude=lon, services=svc,
                       contact=phone, verification_status="pending", emergency_available=True)
    for r in RESOURCE_TYPES:
        total, occ = caps.get(r, (0, 0))
        pending.capacities.append(Capacity(resource_type=r, total_capacity=total, occupied_capacity=occ))
    db.add(pending)
    hospitals[5].emergency_available = False  # rural centre: no emergency department
    db.flush()

    # Kotri's data looks outdated in the demo
    for c in hospitals[3].capacities:
        c.last_updated = now - timedelta(hours=5)

    # 7 days of capacity history, every 3 hours
    points = 7 * 8
    for h in hospitals:
        for c in h.capacities:
            if not c.total_capacity:
                continue
            for i, occ in enumerate(_history(rng, c.total_capacity, c.occupied_capacity, points)):
                ts = now - timedelta(hours=3 * (points - 1 - i)) - timedelta(minutes=5)
                db.add(CapacitySnapshot(hospital_id=h.id, resource_type=c.resource_type,
                                        total_capacity=c.total_capacity, occupied_capacity=occ,
                                        available_capacity=c.total_capacity - occ, timestamp=ts))

    users = [
        User(username="admin", password_hash=hash_password("admin123"), full_name="System Administrator", role="admin"),
        User(username="staff1", password_hash=hash_password("staff123"), full_name="Dr. Sara (City Care)", role="hospital_staff", hospital_id=hospitals[0].id),
        User(username="staff2", password_hash=hash_password("staff123"), full_name="Dr. Kamran (Latifabad)", role="hospital_staff", hospital_id=hospitals[1].id),
        User(username="staff3", password_hash=hash_password("staff123"), full_name="Dr. Rabia (Jamshoro)", role="hospital_staff", hospital_id=hospitals[2].id),
        User(username="staff5", password_hash=hash_password("staff123"), full_name="Dr. Faisal (Qasimabad)", role="hospital_staff", hospital_id=hospitals[4].id),
        User(username="hala", password_hash=hash_password("staff123"), full_name="Hala Children Hospital", role="hospital_staff", hospital_id=pending.id),
        User(username="coord1", password_hash=hash_password("coord123"), full_name="Rescue 1122 Coordinator", role="coordinator", phone="1122"),
        User(username="patient1", password_hash=hash_password("patient123"), full_name="Ahmed (Attendant)", role="patient"),
    ]
    db.add_all(users)
    db.flush()
    coord, patient = users[6], users[7]

    # Past referrals: evening peak, mostly admitted/discharged, some rejected/expired
    for i in range(70):
        desc, res, svc, urg = rng.choice(CASES)
        h = rng.choice(hospitals[:5])
        hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 5, 6, 7, 8, 8, 6, 4, 3, 2])[0]
        created = (now - timedelta(days=rng.randint(0, 6))).replace(hour=hour, minute=rng.randint(0, 59))
        if created > now - timedelta(hours=2):
            created -= timedelta(days=1)
        status = rng.choices(["discharged", "admitted", "rejected", "expired", "cancelled"], weights=[45, 15, 18, 8, 6])[0]
        resp = rng.randint(60, 900) if status != "cancelled" else None
        r = ReferralRequest(
            patient_reference=rng.choice(PATIENTS), description=desc, required_resource=res[0],
            required_resources=",".join(res), required_service=svc, urgency=urg,
            latitude=25.39 + rng.uniform(-0.05, 0.05), longitude=68.35 + rng.uniform(-0.05, 0.05),
            selected_hospital_id=h.id, request_status=status, created_by=rng.choice([coord.id, patient.id]),
            created_at=created, response_seconds=resp, distance_km=round(rng.uniform(1, 15), 1),
            match_percent=rng.randint(55, 96) if status != "rejected" else None,
            confirmation_code=secrets.token_hex(3).upper() if status in ("admitted", "discharged") else "")
        if resp:
            r.accepted_at = created + timedelta(seconds=resp) if status not in ("rejected",) else None
        if status in ("admitted", "discharged"):
            r.transferred_at = created + timedelta(seconds=(resp or 0) + 300)
            r.admitted_at = created + timedelta(seconds=(resp or 0) + 1500)
        if status == "discharged":
            r.discharged_at = r.admitted_at + timedelta(days=rng.randint(1, 3))
            if r.discharged_at > now:
                r.request_status, r.discharged_at = "admitted", None
        if status == "rejected":
            r.note = rng.choice(["No specialist on duty", "ICU full", "Equipment under maintenance"])
        db.add(r)

    # Open requests waiting in City Care's inbox so the staff demo has work to do
    for ref, desc, res, svc, urg, mins in [
        ("Ali Raza", "45 year old male, unconscious after heart attack, SpO2 82%, BP 85/55", ["icu", "ventilator"], "cardiology", "critical", 3),
        ("Sana Khan", "Road accident on Indus Highway, fractured leg and bleeding", ["trauma"], "orthopedics", "high", 11),
    ]:
        r = ReferralRequest(patient_reference=ref, description=desc, required_resource=res[0],
                            required_resources=",".join(res), required_service=svc, urgency=urg,
                            latitude=25.392, longitude=68.362, distance_km=0.6, match_percent=88,
                            selected_hospital_id=hospitals[0].id, request_status="request_sent",
                            created_by=coord.id, created_at=now - timedelta(minutes=mins),
                            summary=json.dumps({"text": desc}))
        db.add(r); db.flush()
        for u in users:
            if u.hospital_id == hospitals[0].id:
                db.add(Notification(user_id=u.id, request_id=r.id, created_at=r.created_at,
                                    message=f"New {urg} referral #{r.id}: {', '.join(res)} for {ref}"))

    db.add_all([
        AuditLog(user_id=users[2].id, hospital_id=hospitals[1].id, action="capacity_update", suspicious=True,
                 details=json.dumps({"resource": "general", "old": {"total": 60, "occupied": 10, "unavailable": 0},
                                     "new": {"total": 60, "occupied": 48, "unavailable": 0}}),
                 timestamp=now - timedelta(hours=2)),
        AuditLog(user_id=None, hospital_id=pending.id, action="hospital_signup",
                 details=json.dumps({"name": pending.name}), timestamp=now - timedelta(hours=6)),
        Notification(user_id=users[0].id, message=f"New hospital awaiting verification: {pending.name}",
                     created_at=now - timedelta(hours=6)),
    ])
    db.commit()
