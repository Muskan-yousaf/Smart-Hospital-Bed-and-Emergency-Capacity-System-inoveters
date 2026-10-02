"""Business logic: matching, classification, reservation, forecasting, audit, notifications."""
import json
import re
import secrets
from collections import defaultdict
from datetime import timedelta
from math import radians, sin, cos, asin, sqrt
from sqlalchemy.orm import Session
from sqlalchemy import select, update, and_
from .models import (Hospital, Capacity, CapacitySnapshot, ReferralRequest, AuditLog,
                     Notification, User, utcnow)

RESERVATION_MINUTES = 15
STALE_MINUTES = 60
AVG_AMBULANCE_KMH = 40
ROAD_FACTOR = 1.3


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def travel_minutes(dist_km: float) -> int:
    return max(1, round(dist_km * ROAD_FACTOR / AVG_AMBULANCE_KMH * 60))


def is_outdated(cap: Capacity) -> bool:
    return utcnow() - cap.last_updated > timedelta(minutes=STALE_MINUTES)


def audit(db: Session, user_id, hospital_id, action, details: dict | str = "", suspicious=False):
    if isinstance(details, dict):
        details = json.dumps(details)
    db.add(AuditLog(user_id=user_id, hospital_id=hospital_id, action=action,
                    details=details, suspicious=suspicious))


def snapshot(db: Session, cap: Capacity, when=None):
    db.add(CapacitySnapshot(hospital_id=cap.hospital_id, resource_type=cap.resource_type,
                            total_capacity=cap.total_capacity, occupied_capacity=cap.occupied_capacity,
                            reserved_capacity=cap.reserved_capacity,
                            available_capacity=cap.available_capacity, timestamp=when or utcnow()))


# ---------------- Notifications ----------------
def notify(db: Session, user_id: int, message: str, request_id: int | None = None):
    db.add(Notification(user_id=user_id, message=message, request_id=request_id))


def notify_hospital_staff(db: Session, hospital_id: int, message: str, request_id: int | None = None):
    for uid in db.scalars(select(User.id).where(User.hospital_id == hospital_id,
                                                User.role == "hospital_staff", User.is_active == True)):  # noqa: E712
        notify(db, uid, message, request_id)


# ---------------- Smart hospital matching ----------------
def score_hospital(h: Hospital, required_resources, service, lat, lon, urgency, max_distance_km,
                   emergency_only=False) -> dict:
    caps = {c.resource_type: c for c in h.capacities}
    dist = haversine_km(lat, lon, h.latitude, h.longitude)
    reasons, suitable = [], True

    if dist > max_distance_km:
        suitable = False
        reasons.append(f"Too far ({dist:.1f} km)")
    if (emergency_only or urgency in ("high", "critical")) and not h.emergency_available:
        suitable = False
        reasons.append("Emergency department not available")
    if service and service.strip().lower() not in h.service_list:
        suitable = False
        reasons.append(f"Service '{service}' not offered")

    avail_scores, resource_status, resource_detail = [], {}, {}
    for r in required_resources:
        c = caps.get(r)
        if not c or c.total_capacity == 0:
            suitable = False
            reasons.append(f"No {r.replace('_', ' ')} facility")
            resource_status[r] = "Not Offered"
            resource_detail[r] = {"available": 0, "total": 0}
            continue
        resource_detail[r] = {"available": c.available_capacity, "total": c.total_capacity}
        if c.available_capacity <= 0:
            suitable = False
            reasons.append(f"{r.replace('_', ' ')} full")
            resource_status[r] = "Not Available"
        else:
            resource_status[r] = "Available"
            # 30% free counts as "comfortable"; fewer free units score lower
            avail_scores.append(min(1.0, c.available_capacity / max(1, c.total_capacity * 0.3)))

    relevant = [caps[r] for r in required_resources if r in caps]
    outdated = any(is_outdated(c) for c in relevant)
    total = sum(c.total_capacity for c in h.capacities if c.resource_type != "ambulance")
    used = sum(c.occupied_capacity + c.reserved_capacity for c in h.capacities if c.resource_type != "ambulance")
    occupancy = round(100 * used / total, 1) if total else 0

    match_pct = None
    breakdown = {}
    if suitable:
        dist_score = max(0.0, 1 - dist / max_distance_km)
        avail_score = sum(avail_scores) / len(avail_scores)
        load_score = 1 - occupancy / 100
        fresh_score = 0.4 if outdated else 1.0
        # critical cases weight distance/time more than spare capacity
        wd = {"critical": 0.55, "high": 0.45}.get(urgency, 0.35)
        wf, wl = 0.1, 0.1
        wa = 1 - wd - wf - wl
        match_pct = round(100 * (wd * dist_score + wa * avail_score + wl * load_score + wf * fresh_score))
        breakdown = {"distance": round(100 * dist_score), "availability": round(100 * avail_score),
                     "hospital_load": round(100 * load_score), "data_freshness": round(100 * fresh_score)}

    last = max((c.last_updated for c in relevant), default=None)
    return {
        "hospital_id": h.id, "hospital_name": h.name, "location": h.location, "city": h.city,
        "contact": h.contact, "latitude": h.latitude, "longitude": h.longitude,
        "services": h.service_list, "emergency_available": h.emergency_available,
        "distance_km": round(dist, 1), "estimated_travel_min": travel_minutes(dist),
        "resources": resource_status, "resource_detail": resource_detail,
        "occupancy_percent": occupancy,
        "match_percent": match_pct, "score_breakdown": breakdown,
        "suitable": suitable, "reasons_not_suitable": reasons,
        "last_updated": last,
        "warning": "Capacity information may be outdated" if outdated else None,
    }


def match_hospitals(db: Session, required_resources, service, lat, lon, urgency, max_distance_km,
                    emergency_only=False):
    hospitals = db.scalars(select(Hospital).where(
        Hospital.verification_status == "verified", Hospital.account_status == "active")).all()
    results = [score_hospital(h, required_resources, service, lat, lon, urgency, max_distance_km, emergency_only)
               for h in hospitals]
    results.sort(key=lambda x: (not x["suitable"], -(x["match_percent"] or 0), x["distance_km"]))
    rank = 0
    for r in results:
        r["rank"] = (rank := rank + 1) if r["suitable"] else None
    return results


# ---------------- Patient request classification & summarisation (rule-based NLP) ----------------
RULES = [
    (["ventilator", "breathing difficulty", "not breathing", "respiratory failure", "intubat", "low oxygen",
      "spo2", "oxygen saturation", "shortness of breath"], "ventilator", "pulmonology"),
    (["newborn", "neonat", "premature", "preterm", "infant"], "nicu", "neonatology"),
    (["accident", "trauma", "fracture", "bleeding", "gunshot", "head injury", "fall from", "crash", "stab"],
     "trauma", "orthopedics"),
    (["heart attack", "chest pain", "cardiac", "myocardial", "arrhythmia"], "icu", "cardiology"),
    (["stroke", "paralysis", "seizure", "brain"], "icu", "neurology"),
    (["unconscious", "critical", "icu", "sepsis", "shock", "coma"], "icu", None),
    (["dialysis", "kidney failure", "renal failure"], "dialysis", "nephrology"),
    (["surgery", "operation", "appendix", "appendicitis", "hernia"], "operation_theatre", "surgery"),
    (["covid", "infectious", "isolation", "contagious", "tuberculosis", "measles", "dengue"], "isolation", None),
    (["pregnan", "delivery", "labour", "labor", "c-section"], "emergency", "gynecology"),
    (["burn"], "emergency", "surgery"),
]
CRITICAL_WORDS = ["unconscious", "not breathing", "heart attack", "severe bleeding", "critical", "stroke",
                  "cardiac arrest", "coma", "shock", "gunshot"]
HIGH_WORDS = ["accident", "chest pain", "fracture", "emergency", "labour", "labor", "ventilator", "seizure",
              "bleeding", "breathing difficulty", "head injury", "burn", "shortness of breath"]
LOW_WORDS = ["routine", "check-up", "checkup", "follow up", "follow-up", "mild", "stable"]


def _extract_vitals(t: str) -> dict:
    v = {}
    if m := re.search(r"(?:bp|blood pressure)\s*[:=]?\s*(\d{2,3})\s*/\s*(\d{2,3})", t):
        v["blood_pressure"] = f"{m[1]}/{m[2]}"
    if m := re.search(r"(?:spo2|oxygen(?: saturation)?|o2 sat)\s*[:=]?\s*(?:is|of|at)?\s*(\d{2,3})\s*%?", t):
        v["spo2_percent"] = int(m[1])
    if m := re.search(r"(?:pulse|heart rate|hr)\s*[:=]?\s*(?:is|of|at)?\s*(\d{2,3})", t):
        v["pulse_bpm"] = int(m[1])
    if m := re.search(r"(?:temp|temperature|fever)\s*[:=]?\s*(?:is|of|at)?\s*(\d{2,3}(?:\.\d)?)", t):
        v["temperature"] = float(m[1])
    return v


def classify_description(text: str) -> dict:
    """Identify required facility, urgency and a structured summary from a free-text case description."""
    raw = text.strip()
    t = raw.lower()
    resources, services, matched = [], [], []
    for words, res, svc in RULES:
        hits = [w for w in words if w in t]
        if hits:
            matched.extend(hits)
            if res not in resources:
                resources.append(res)
            if svc and svc not in services:
                services.append(svc)
    vitals = _extract_vitals(t)
    # vitals can escalate the need even if no keyword matched
    if vitals.get("spo2_percent") and vitals["spo2_percent"] < 90 and "ventilator" not in resources:
        resources.append("ventilator")
        matched.append(f"SpO2 {vitals['spo2_percent']}%")
    if "ventilator" in resources and "icu" not in resources:
        resources.insert(0, "icu")  # ventilated patients need an ICU bed
    if not resources:
        resources = ["general"]

    if any(w in t for w in CRITICAL_WORDS) or (vitals.get("spo2_percent", 100) < 85):
        urgency = "critical"
    elif any(w in t for w in HIGH_WORDS) or (vitals.get("spo2_percent", 100) < 92):
        urgency = "high"
    elif any(w in t for w in LOW_WORDS):
        urgency = "low"
    else:
        urgency = "medium"

    age = None
    if m := re.search(r"(\d{1,3})\s*(?:-|\s)?\s*(?:years?|yrs?|y/o|yo|year-old|years-old)", t):
        age = int(m[1])
    elif m := re.search(r"\b(?:age|aged)\s*[:=]?\s*(\d{1,3})", t):
        age = int(m[1])
    gender = ("female" if re.search(r"\b(female|woman|girl|lady|mother|she|her)\b", t)
              else "male" if re.search(r"\b(male|man|boy|father|he|him|his)\b", t) else None)
    if age is not None and age < 1 and "nicu" not in resources:
        resources.append("nicu")

    first_sentence = re.split(r"(?<=[.!?])\s+", raw)[0] if raw else ""
    condition = first_sentence[:120] + ("..." if len(first_sentence) > 120 else "")
    parts = []
    if age is not None or gender:
        parts.append(" ".join(x for x in [f"{age}y" if age is not None else None, gender] if x))
    if condition:
        parts.append(condition)
    if vitals:
        parts.append(", ".join(f"{k.replace('_', ' ')} {val}" for k, val in vitals.items()))
    parts.append(f"needs {', '.join(r.replace('_', ' ') for r in resources)}")
    confidence = min(0.95, 0.4 + 0.12 * len(set(matched))) if matched else 0.3

    return {
        "required_resources": resources,
        "required_service": services[0] if services else None,
        "suggested_services": services,
        "urgency": urgency,
        "summary": " | ".join(parts),
        "structured": {"age": age, "gender": gender, "condition": condition, "vitals": vitals,
                       "keywords": sorted(set(matched)), "urgency": urgency,
                       "required_resources": resources},
        "confidence": round(confidence, 2),
    }


# ---------------- Reservation handling ----------------
def get_capacity(db: Session, hospital_id: int, resource: str) -> Capacity | None:
    return db.scalar(select(Capacity).where(Capacity.hospital_id == hospital_id,
                                            Capacity.resource_type == resource))


def _atomic_reserve_one(db: Session, hospital_id: int, resource: str) -> bool:
    """Single conditional UPDATE: only succeeds if a free unit exists at the moment of writing.
    Two staff accepting at the same time can never both get the last bed."""
    free = (Capacity.total_capacity - Capacity.occupied_capacity
            - Capacity.reserved_capacity - Capacity.unavailable_capacity)
    res = db.execute(
        update(Capacity)
        .where(and_(Capacity.hospital_id == hospital_id, Capacity.resource_type == resource, free > 0))
        .values(reserved_capacity=Capacity.reserved_capacity + 1, last_updated=utcnow())
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


def reserve(db: Session, req: ReferralRequest) -> list[str]:
    """Hold one unit of every required resource for RESERVATION_MINUTES.
    All-or-nothing: returns [] on success, or the list of resources that had no free unit."""
    failed, held = [], []
    for r in req.resource_list:
        if _atomic_reserve_one(db, req.selected_hospital_id, r):
            held.append(r)
        else:
            failed.append(r)
    if failed:
        for r in held:  # roll back partial holds
            db.execute(update(Capacity).where(Capacity.hospital_id == req.selected_hospital_id,
                                              Capacity.resource_type == r)
                       .values(reserved_capacity=Capacity.reserved_capacity - 1)
                       .execution_options(synchronize_session=False))
        return failed
    req.reserved = True
    req.accepted_at = utcnow()
    req.expires_at = req.accepted_at + timedelta(minutes=RESERVATION_MINUTES)
    req.confirmation_code = secrets.token_hex(3).upper()
    db.flush()
    _refresh_and_snapshot(db, req)
    return []


def _refresh_and_snapshot(db: Session, req: ReferralRequest):
    for r in req.resource_list:
        cap = get_capacity(db, req.selected_hospital_id, r)
        if cap:
            db.refresh(cap)
            snapshot(db, cap)


def release(db: Session, req: ReferralRequest):
    if req.reserved:
        for r in req.resource_list:
            cap = get_capacity(db, req.selected_hospital_id, r)
            if cap and cap.reserved_capacity > 0:
                cap.reserved_capacity -= 1
                cap.last_updated = utcnow()
                snapshot(db, cap)
        req.reserved = False


def confirm_admission(db: Session, req: ReferralRequest):
    """Reserved units -> occupied units."""
    for r in req.resource_list:
        cap = get_capacity(db, req.selected_hospital_id, r)
        if not cap:
            continue
        if req.reserved and cap.reserved_capacity > 0:
            cap.reserved_capacity -= 1
        cap.occupied_capacity += 1
        cap.last_updated = utcnow()
        snapshot(db, cap)
    req.reserved = False
    req.expires_at = None
    req.admitted_at = utcnow()


def discharge(db: Session, req: ReferralRequest):
    """Occupied units -> free again."""
    for r in req.resource_list:
        cap = get_capacity(db, req.selected_hospital_id, r)
        if cap and cap.occupied_capacity > 0:
            cap.occupied_capacity -= 1
            cap.last_updated = utcnow()
            snapshot(db, cap)
    req.discharged_at = utcnow()


def expire_old_reservations(db: Session) -> int:
    now = utcnow()
    stale = db.scalars(select(ReferralRequest).where(
        ReferralRequest.request_status == "accepted",
        ReferralRequest.expires_at != None, ReferralRequest.expires_at < now)).all()  # noqa: E711
    for r in stale:
        release(db, r)
        r.request_status = "expired"
        r.note = (r.note + " | " if r.note else "") + "Reservation expired after 15 minutes"
        audit(db, None, r.selected_hospital_id, "reservation_expired", {"request_id": r.id})
        notify(db, r.created_by, f"Request #{r.id}: the bed hold at {r.hospital_name} expired. "
                                 "Please search again.", r.id)
    if stale:
        db.commit()
    return len(stale)


# ---------------- Forecasting ----------------
def _linear_fit(points: list[tuple[float, float]]):
    n = len(points)
    if n < 2:
        return 0.0, points[0][1] if points else 0.0
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / den if den else 0.0
    return slope, my - slope * mx


def capacity_forecast(db: Session, hospital_id: int, hours_back: int = 48) -> list[dict]:
    """Per resource: fit available units vs time and estimate when capacity runs out."""
    now = utcnow()
    since = now - timedelta(hours=hours_back)
    snaps = db.scalars(select(CapacitySnapshot).where(
        CapacitySnapshot.hospital_id == hospital_id, CapacitySnapshot.timestamp >= since)
        .order_by(CapacitySnapshot.timestamp)).all()
    by_res = defaultdict(list)
    for s in snaps:
        by_res[s.resource_type].append(((s.timestamp - now).total_seconds() / 3600, s.available_capacity))
    caps = {c.resource_type: c for c in db.scalars(select(Capacity).where(Capacity.hospital_id == hospital_id))}
    out = []
    for r, cap in caps.items():
        if cap.total_capacity == 0:
            continue
        pts = by_res.get(r, []) + [(0.0, cap.available_capacity)]
        slope, _ = _linear_fit(pts)  # units per hour
        avail = cap.available_capacity
        hours_to_full = None
        if slope < -0.01 and avail > 0:
            hours_to_full = round(avail / -slope, 1)
        risk = ("critical" if avail == 0 or (hours_to_full is not None and hours_to_full <= 6)
                else "high" if (hours_to_full is not None and hours_to_full <= 24) or avail / cap.total_capacity < 0.1
                else "medium" if slope < -0.01 or avail / cap.total_capacity < 0.25 else "low")
        out.append({"resource_type": r, "available_now": avail, "total": cap.total_capacity,
                    "trend_per_hour": round(slope, 2),
                    "predicted_available_in_6h": max(0, min(cap.total_capacity, round(avail + slope * 6))),
                    "hours_until_full": hours_to_full, "risk": risk, "samples": len(pts)})
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    out.sort(key=lambda x: (order[x["risk"]], x["available_now"]))
    return out


def demand_forecast(requests: list[ReferralRequest], days: int = 7) -> dict:
    """Average requests per hour-of-day over the last `days`, plus the next 6 hours' expectation."""
    now = utcnow()
    since = now - timedelta(days=days)
    recent = [r for r in requests if r.created_at >= since]
    by_hour = [0] * 24
    by_res = defaultdict(int)
    for r in recent:
        by_hour[r.created_at.hour] += 1
        for res in r.resource_list:
            by_res[res] += 1
    avg = [round(c / days, 2) for c in by_hour]
    nxt = [{"hour": (now.hour + i) % 24, "expected_requests": avg[(now.hour + i) % 24]} for i in range(1, 7)]
    peak = sorted(range(24), key=lambda h: -by_hour[h])[:3]
    return {"avg_requests_by_hour": avg, "next_6_hours": nxt,
            "peak_hours": [h for h in peak if by_hour[h] > 0],
            "demand_by_resource": dict(sorted(by_res.items(), key=lambda x: -x[1]))}
