import json
from collections import Counter, defaultdict
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from . import schemas, services
from .database import get_db
from .models import (User, Hospital, Capacity, CapacitySnapshot, ReferralRequest, AuditLog, Notification,
                     RESOURCE_TYPES, BED_TYPES, OPEN_STATUSES, utcnow)
from .security import (hash_password, verify_password, create_token,
                       current_user, require_roles)

auth_r = APIRouter(prefix="/auth", tags=["Auth"])
hosp_r = APIRouter(tags=["Hospitals & Capacity"])
search_r = APIRouter(tags=["Smart Matching & AI"])
req_r = APIRouter(prefix="/requests", tags=["Referral Requests"])
notif_r = APIRouter(prefix="/notifications", tags=["Notifications"])
ana_r = APIRouter(prefix="/analytics", tags=["Analytics"])
admin_r = APIRouter(prefix="/admin", tags=["Admin"])


def _check_resources(resources: list[str]):
    bad = [r for r in resources if r not in RESOURCE_TYPES]
    if bad:
        raise HTTPException(400, f"Unknown resource types: {bad}. Valid: {RESOURCE_TYPES}")


# ====================== AUTH ======================
@auth_r.post("/register", response_model=schemas.UserOut)
def register(data: schemas.RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "Username already taken")
    u = User(username=data.username, password_hash=hash_password(data.password),
             full_name=data.full_name or data.username, phone=data.phone, role=data.role)
    db.add(u); db.commit(); db.refresh(u)
    return u


@auth_r.post("/login", response_model=schemas.TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == form.username))
    if not u or not verify_password(form.password, u.password_hash):
        raise HTTPException(401, "Wrong username or password")
    if not u.is_active:
        raise HTTPException(403, "This account is disabled. Contact the administrator.")
    return {"access_token": create_token(u), "token_type": "bearer", "role": u.role,
            "hospital_id": u.hospital_id, "full_name": u.full_name or u.username}


@auth_r.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(current_user)):
    return user


# ====================== HOSPITALS / CAPACITY ======================
def hospital_out(h: Hospital) -> dict:
    out = schemas.HospitalOut.model_validate(h).model_dump()
    for c_out, c in zip(out["capacities"], h.capacities):
        c_out["outdated"] = c.total_capacity > 0 and services.is_outdated(c)
    return out


@hosp_r.get("/meta", tags=["Health"])
def meta():
    """Static lists the frontend needs (resource types, statuses, reservation window)."""
    return {"resource_types": RESOURCE_TYPES, "bed_types": BED_TYPES,
            "reservation_minutes": services.RESERVATION_MINUTES, "stale_minutes": services.STALE_MINUTES}


@hosp_r.get("/hospitals", response_model=list[schemas.HospitalOut])
def list_hospitals(
    resource: str | None = None, service: str | None = None, name: str | None = None,
    emergency_only: bool = False, only_available: bool = False,
    lat: float | None = None, lon: float | None = None, max_distance_km: float | None = None,
    db: Session = Depends(get_db),
):
    """Public directory: search/filter verified hospitals (bed type, service, distance, emergency)."""
    out = []
    q = select(Hospital).where(Hospital.verification_status == "verified", Hospital.account_status == "active")
    for h in db.scalars(q.order_by(Hospital.name)).all():
        if name and name.lower() not in h.name.lower(): continue
        if emergency_only and not h.emergency_available: continue
        if service and service.lower() not in h.service_list: continue
        if resource:
            c = next((c for c in h.capacities if c.resource_type == resource), None)
            if not c or c.total_capacity == 0 or (only_available and c.available_capacity <= 0): continue
        if lat is not None and lon is not None and max_distance_km is not None:
            if services.haversine_km(lat, lon, h.latitude, h.longitude) > max_distance_km: continue
        out.append(hospital_out(h))
    return out


@hosp_r.post("/hospitals/signup", response_model=schemas.HospitalOut, status_code=201)
def hospital_signup(data: schemas.HospitalSignupIn, db: Session = Depends(get_db)):
    """A hospital registers itself. It stays hidden from search until an admin verifies it."""
    if db.scalar(select(User).where(User.username == data.staff_username)):
        raise HTTPException(400, "Staff username already taken")
    h = Hospital(**data.model_dump(exclude={"staff_username", "staff_password", "staff_full_name"}),
                 verification_status="pending")
    for r in RESOURCE_TYPES:
        h.capacities.append(Capacity(resource_type=r, total_capacity=0))
    db.add(h); db.flush()
    db.add(User(username=data.staff_username, password_hash=hash_password(data.staff_password),
                full_name=data.staff_full_name or data.staff_username, role="hospital_staff", hospital_id=h.id))
    services.audit(db, None, h.id, "hospital_signup", {"name": h.name})
    for admin_id in db.scalars(select(User.id).where(User.role == "admin")):
        services.notify(db, admin_id, f"New hospital awaiting verification: {h.name}")
    db.commit(); db.refresh(h)
    return hospital_out(h)


@hosp_r.get("/hospitals/{hospital_id}", response_model=schemas.HospitalOut)
def get_hospital(hospital_id: int, db: Session = Depends(get_db)):
    h = db.get(Hospital, hospital_id)
    if not h: raise HTTPException(404, "Hospital not found")
    return hospital_out(h)


@hosp_r.post("/hospitals", response_model=schemas.HospitalOut, status_code=201)
def create_hospital(data: schemas.HospitalIn, db: Session = Depends(get_db),
                    admin: User = Depends(require_roles("admin"))):
    h = Hospital(**data.model_dump(), verification_status="verified")
    for r in RESOURCE_TYPES:
        h.capacities.append(Capacity(resource_type=r, total_capacity=0))
    db.add(h)
    services.audit(db, admin.id, None, "hospital_created", data.name)
    db.commit(); db.refresh(h)
    return hospital_out(h)


@hosp_r.patch("/hospitals/{hospital_id}", response_model=schemas.HospitalOut)
def update_hospital(hospital_id: int, data: schemas.HospitalUpdateIn, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("hospital_staff", "admin"))):
    """Update service availability, emergency department status and contact details."""
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "You can only update your own hospital")
    h = db.get(Hospital, hospital_id)
    if not h: raise HTTPException(404, "Hospital not found")
    changes = data.model_dump(exclude_none=True)
    if "services" in changes:
        changes["services"] = ",".join(s.strip().lower() for s in changes["services"].split(",") if s.strip())
    for k, v in changes.items():
        setattr(h, k, v)
    services.audit(db, user.id, hospital_id, "hospital_profile_update", changes)
    db.commit(); db.refresh(h)
    return hospital_out(h)


@hosp_r.put("/hospitals/{hospital_id}/capacity/{resource_type}", response_model=schemas.CapacityOut)
def update_capacity(hospital_id: int, resource_type: str, data: schemas.CapacityUpdateIn,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_roles("hospital_staff", "admin"))):
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "You can only update your own hospital")
    cap = services.get_capacity(db, hospital_id, resource_type)
    if not cap: raise HTTPException(404, "Resource type not found for this hospital")
    old = {"total": cap.total_capacity, "occupied": cap.occupied_capacity,
           "unavailable": cap.unavailable_capacity}
    if data.total_capacity is not None: cap.total_capacity = data.total_capacity
    if data.occupied_capacity is not None: cap.occupied_capacity = data.occupied_capacity
    if data.unavailable_capacity is not None: cap.unavailable_capacity = data.unavailable_capacity
    if cap.occupied_capacity + cap.reserved_capacity + cap.unavailable_capacity > cap.total_capacity:
        db.rollback()
        raise HTTPException(400, f"Occupied + reserved ({cap.reserved_capacity}) + out of service "
                                 "cannot exceed total capacity")
    new = {"total": cap.total_capacity, "occupied": cap.occupied_capacity,
           "unavailable": cap.unavailable_capacity}
    # flag large sudden jumps for admin review
    suspicious = (abs(new["occupied"] - old["occupied"]) > max(10, 0.5 * max(1, old["total"]))
                  or abs(new["total"] - old["total"]) > max(20, 0.5 * max(1, old["total"])))
    cap.last_updated = utcnow()
    services.snapshot(db, cap)
    services.audit(db, user.id, hospital_id, "capacity_update",
                   {"resource": resource_type, "old": old, "new": new}, suspicious)
    db.commit(); db.refresh(cap)
    return schemas.CapacityOut.model_validate(cap)


@hosp_r.post("/hospitals/{hospital_id}/confirm-capacity")
def confirm_capacity(hospital_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("hospital_staff", "admin"))):
    """'Numbers are still correct' - refreshes last_updated on every resource without changing values."""
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "You can only update your own hospital")
    now = utcnow()
    db.execute(update(Capacity).where(Capacity.hospital_id == hospital_id).values(last_updated=now))
    services.audit(db, user.id, hospital_id, "capacity_confirmed", "All values confirmed")
    db.commit()
    return {"hospital_id": hospital_id, "last_updated": now}


# ====================== SMART MATCHING & AI ======================
@search_r.post("/search/match")
def smart_match(data: schemas.MatchIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    _check_resources(data.required_resources)
    return services.match_hospitals(db, data.required_resources, data.required_service,
                                    data.latitude, data.longitude, data.urgency, data.max_distance_km,
                                    data.emergency_only)


@search_r.post("/classify")
def classify(data: schemas.ClassifyIn, user: User = Depends(current_user)):
    """Patient request classification + referral summarisation from a free-text description."""
    return services.classify_description(data.description)


# ====================== REFERRAL REQUESTS ======================
def _req_out(r: ReferralRequest, user: User) -> dict:
    out = schemas.RequestOut.model_validate(r).model_dump()
    # the confirmation code proves the patient arrived with a valid referral; staff must scan/enter it
    if user.role == "hospital_staff":
        out["confirmation_code"] = ""
    return out


def _get_req(db, rid) -> ReferralRequest:
    services.expire_old_reservations(db)
    r = db.get(ReferralRequest, rid)
    if not r: raise HTTPException(404, "Request not found")
    return r


def _check_access(user: User, r: ReferralRequest):
    if user.role == "admin": return
    if user.role == "hospital_staff" and user.hospital_id == r.selected_hospital_id: return
    if r.created_by == user.id: return
    raise HTTPException(403, "No access to this request")


def _need_status(r: ReferralRequest, allowed: tuple, action: str):
    if r.request_status not in allowed:
        raise HTTPException(400, f"Cannot {action} a request that is '{r.request_status.replace('_', ' ')}'")


@req_r.post("", response_model=schemas.RequestOut, status_code=201)
def create_request(data: schemas.RequestIn, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("patient", "coordinator"))):
    resources = list(dict.fromkeys(data.required_resources + ([data.required_resource] if data.required_resource else [])))
    if not resources:
        raise HTTPException(400, "Choose at least one required resource")
    _check_resources(resources)
    h = db.get(Hospital, data.hospital_id)
    if not h or h.verification_status != "verified" or h.account_status != "active":
        raise HTTPException(404, "Hospital not found or not verified")
    dup = db.scalar(select(ReferralRequest).where(
        ReferralRequest.created_by == user.id, ReferralRequest.selected_hospital_id == h.id,
        ReferralRequest.patient_reference == data.patient_reference,
        ReferralRequest.request_status.in_(OPEN_STATUSES)))
    if dup:
        raise HTTPException(409, f"Request #{dup.id} for this patient at this hospital is still open")

    ai = services.classify_description(data.description) if data.description.strip() else None
    scored = None
    if data.latitude is not None and data.longitude is not None:
        scored = services.score_hospital(h, resources, data.required_service, data.latitude, data.longitude,
                                         data.urgency, 1000)
    r = ReferralRequest(
        patient_reference=data.patient_reference, description=data.description,
        summary=json.dumps(ai["structured"] | {"text": ai["summary"]}) if ai else "",
        required_resource=resources[0], required_resources=",".join(resources),
        required_service=data.required_service, latitude=data.latitude, longitude=data.longitude,
        distance_km=scored["distance_km"] if scored else None,
        match_percent=scored["match_percent"] if scored else None,
        urgency=data.urgency, selected_hospital_id=h.id, created_by=user.id, request_status="request_sent")
    missing = [res for res in resources if not (c := services.get_capacity(db, h.id, res)) or c.available_capacity <= 0]
    if missing:
        r.request_status = "no_capacity"
        r.note = f"No capacity at time of request: {', '.join(missing)}"
    db.add(r); db.flush()
    services.audit(db, user.id, h.id, "request_created", {"request_id": r.id, "resources": resources})
    if r.request_status == "request_sent":
        services.notify_hospital_staff(db, h.id, f"New {data.urgency} referral #{r.id}: "
                                                 f"{', '.join(resources).replace('_', ' ')} for {data.patient_reference}", r.id)
    db.commit(); db.refresh(r)
    return _req_out(r, user)


@req_r.get("", response_model=list[schemas.RequestOut])
def list_requests(status: str | None = None, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    services.expire_old_reservations(db)
    q = select(ReferralRequest).order_by(ReferralRequest.created_at.desc())
    if user.role == "hospital_staff":
        q = q.where(ReferralRequest.selected_hospital_id == user.hospital_id)
    elif user.role != "admin":
        q = q.where(ReferralRequest.created_by == user.id)
    if status: q = q.where(ReferralRequest.request_status.in_(status.split(",")))
    return [_req_out(r, user) for r in db.scalars(q.limit(500)).all()]


@req_r.get("/verify/{code}", response_model=schemas.RequestOut)
def verify_code(code: str, db: Session = Depends(get_db),
                user: User = Depends(require_roles("hospital_staff", "admin"))):
    """QR-based referral confirmation: the hospital scans/enters the code the patient shows."""
    q = select(ReferralRequest).where(ReferralRequest.confirmation_code == code.strip().upper())
    if user.role == "hospital_staff":
        q = q.where(ReferralRequest.selected_hospital_id == user.hospital_id)
    r = db.scalar(q)
    if not r: raise HTTPException(404, "No referral with this code at your hospital")
    return _req_out(r, user)


@req_r.get("/{rid}", response_model=schemas.RequestOut)
def get_request(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _get_req(db, rid); _check_access(user, r)
    return _req_out(r, user)


@req_r.post("/{rid}/review", response_model=schemas.RequestOut)
def start_review(rid: int, db: Session = Depends(get_db),
                 user: User = Depends(require_roles("hospital_staff", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    _need_status(r, ("request_sent",), "review")
    r.request_status = "hospital_reviewing"
    services.notify(db, r.created_by, f"Request #{r.id}: {r.hospital_name} is reviewing your request.", r.id)
    db.commit(); db.refresh(r)
    return _req_out(r, user)


@req_r.post("/{rid}/respond", response_model=schemas.RequestOut)
def respond(rid: int, data: schemas.RespondIn, db: Session = Depends(get_db),
            user: User = Depends(require_roles("hospital_staff", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    _need_status(r, ("request_sent", "hospital_reviewing"), "respond to")
    r.response_seconds = (utcnow() - r.created_at).total_seconds()
    r.note = data.note
    if not data.accept:
        r.request_status = "rejected"
        msg = f"Request #{r.id}: {r.hospital_name} could not accept" + (f" ({data.note})" if data.note else "") + \
              ". Please try another hospital."
    elif not (failed := services.reserve(db, r)):   # all-or-nothing atomic hold for 15 minutes
        r.request_status = "accepted"
        msg = (f"Request #{r.id} accepted by {r.hospital_name}. Bed held for "
               f"{services.RESERVATION_MINUTES} minutes - confirm transfer now. Code {r.confirmation_code}")
    else:
        r.request_status = "no_capacity"
        r.note = (data.note + " | " if data.note else "") + f"Capacity ran out before acceptance: {', '.join(failed)}"
        msg = f"Request #{r.id}: {r.hospital_name} has no free {', '.join(failed)} any more."
    services.notify(db, r.created_by, msg, r.id)
    services.audit(db, user.id, r.selected_hospital_id, "request_response",
                   {"request_id": r.id, "result": r.request_status})
    db.commit(); db.refresh(r)
    return _req_out(r, user)


@req_r.post("/{rid}/transfer", response_model=schemas.RequestOut)
def mark_transferred(rid: int, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("patient", "coordinator", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    _need_status(r, ("accepted",), "transfer")
    r.request_status = "patient_transferred"
    r.transferred_at = utcnow()
    r.expires_at = None  # patient on the way: the hold no longer expires; hospital must confirm admission
    services.notify_hospital_staff(db, r.selected_hospital_id,
                                   f"Patient for request #{r.id} ({r.patient_reference}) is on the way.", r.id)
    services.audit(db, user.id, r.selected_hospital_id, "patient_transferred", {"request_id": r.id})
    db.commit(); db.refresh(r)
    return _req_out(r, user)


@req_r.post("/{rid}/admit", response_model=schemas.RequestOut)
def admit(rid: int, data: schemas.AdmitIn | None = None, db: Session = Depends(get_db),
          user: User = Depends(require_roles("hospital_staff", "admin"))):
    r = _get_req(db, rid); _check_access(user, r)
    _need_status(r, ("accepted", "patient_transferred"), "admit")
    if data and data.confirmation_code and data.confirmation_code.strip().upper() != r.confirmation_code:
        raise HTTPException(400, "Confirmation code does not match this referral")
    services.confirm_admission(db, r)   # reserved -> occupied (capacity updated automatically)
    r.request_status = "admitted"
    services.notify(db, r.created_by, f"Request #{r.id}: {r.patient_reference} has been admitted at {r.hospital_name}.", r.id)
    services.audit(db, user.id, r.selected_hospital_id, "patient_admitted",
                   {"request_id": r.id, "verified_by_code": bool(data and data.confirmation_code)})
    db.commit(); db.refresh(r)
    return _req_out(r, user)


@req_r.post("/{rid}/discharge", response_model=schemas.RequestOut)
def discharge(rid: int, db: Session = Depends(get_db),
              user: User = Depends(require_roles("hospital_staff", "admin"))):
    """Manage admitted patients: discharge frees the bed(s) again."""
    r = _get_req(db, rid); _check_access(user, r)
    _need_status(r, ("admitted",), "discharge")
    services.discharge(db, r)
    r.request_status = "discharged"
    services.audit(db, user.id, r.selected_hospital_id, "patient_discharged", {"request_id": r.id})
    db.commit(); db.refresh(r)
    return _req_out(r, user)


@req_r.post("/{rid}/cancel", response_model=schemas.RequestOut)
def cancel(rid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = _get_req(db, rid); _check_access(user, r)
    _need_status(r, ("request_sent", "hospital_reviewing", "accepted", "patient_transferred"), "cancel")
    services.release(db, r)
    r.request_status = "cancelled"
    r.expires_at = None
    if user.role in ("patient", "coordinator"):
        services.notify_hospital_staff(db, r.selected_hospital_id, f"Request #{r.id} was cancelled by the requester.", r.id)
    else:
        services.notify(db, r.created_by, f"Request #{r.id} was cancelled by {r.hospital_name}.", r.id)
    services.audit(db, user.id, r.selected_hospital_id, "request_cancelled", {"request_id": r.id})
    db.commit(); db.refresh(r)
    return _req_out(r, user)


# ====================== NOTIFICATIONS ======================
@notif_r.get("", response_model=list[schemas.NotificationOut])
def my_notifications(unread_only: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only: q = q.where(Notification.is_read == False)  # noqa: E712
    return db.scalars(q.order_by(Notification.created_at.desc()).limit(30)).all()


@notif_r.post("/read")
def mark_read(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.execute(update(Notification).where(Notification.user_id == user.id).values(is_read=True))
    db.commit()
    return {"ok": True}


# ====================== ANALYTICS ======================
def _occupancy(h: Hospital) -> float:
    t = sum(c.total_capacity for c in h.capacities if c.resource_type in BED_TYPES)
    o = sum(c.occupied_capacity + c.reserved_capacity for c in h.capacities if c.resource_type in BED_TYPES)
    return round(100 * o / t, 1) if t else 0


def _daily_series(dates, days: int) -> list[dict]:
    today = utcnow().date()
    c = Counter(d.isoformat() for d in dates)
    return [{"date": (d := (today - timedelta(days=i)).isoformat()), "count": c.get(d, 0)}
            for i in range(days - 1, -1, -1)]


def _occupancy_trend(db: Session, hospital_id: int | None, days: int) -> list[dict]:
    """Daily average bed occupancy % from capacity snapshots."""
    since = utcnow() - timedelta(days=days)
    q = select(CapacitySnapshot).where(CapacitySnapshot.timestamp >= since,
                                       CapacitySnapshot.resource_type.in_(BED_TYPES))
    if hospital_id: q = q.where(CapacitySnapshot.hospital_id == hospital_id)
    # last snapshot per (day, hospital, resource) -> sum per day
    last = {}
    for s in db.scalars(q.order_by(CapacitySnapshot.timestamp)):
        last[(s.timestamp.date().isoformat(), s.hospital_id, s.resource_type)] = s
    day_tot, day_used = defaultdict(int), defaultdict(int)
    for (d, _, _), s in last.items():
        day_tot[d] += s.total_capacity
        day_used[d] += s.occupied_capacity + s.reserved_capacity
    return [{"date": d, "occupancy_percent": round(100 * day_used[d] / day_tot[d], 1) if day_tot[d] else 0}
            for d in sorted(day_tot)]


@ana_r.get("/hospital/{hospital_id}")
def hospital_analytics(hospital_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_roles("hospital_staff", "admin"))):
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "Not your hospital")
    h = db.get(Hospital, hospital_id)
    if not h: raise HTTPException(404, "Hospital not found")
    caps = {c.resource_type: c for c in h.capacities}
    beds = [c for t, c in caps.items() if t in BED_TYPES]
    total = sum(c.total_capacity for c in beds)
    occupied = sum(c.occupied_capacity for c in beds)
    reserved = sum(c.reserved_capacity for c in beds)
    icu = caps.get("icu")
    reqs = db.scalars(select(ReferralRequest).where(ReferralRequest.selected_hospital_id == hospital_id)).all()
    status_count = Counter(r.request_status for r in reqs)
    resp = [r.response_seconds for r in reqs if r.response_seconds is not None]
    today = utcnow().date()
    return {
        "hospital": h.name,
        "total_beds": total, "occupied_beds": occupied, "reserved_beds": reserved,
        "available_beds": sum(c.available_capacity for c in beds),
        "icu_occupancy_percent": round(100 * (icu.occupied_capacity + icu.reserved_capacity) / icu.total_capacity, 1)
        if icu and icu.total_capacity else 0,
        "capacity_utilization_percent": round(100 * (occupied + reserved) / total, 1) if total else 0,
        "emergency_requests": sum(1 for r in reqs if r.urgency in ("high", "critical")),
        "pending_requests": status_count["request_sent"] + status_count["hospital_reviewing"],
        "accepted_referrals": sum(status_count[s] for s in ("accepted", "patient_transferred", "admitted", "discharged")),
        "rejected_referrals": status_count["rejected"],
        "acceptance_rate_percent": round(100 * sum(status_count[s] for s in ("accepted", "patient_transferred", "admitted", "discharged"))
                                         / max(1, len([r for r in reqs if r.response_seconds is not None])), 1),
        "requests_by_status": dict(status_count),
        "average_response_seconds": round(sum(resp) / len(resp), 1) if resp else None,
        "admissions_today": sum(1 for r in reqs if r.admitted_at and r.admitted_at.date() == today),
        "daily_admissions": _daily_series([r.admitted_at.date() for r in reqs if r.admitted_at], 7),
        "daily_requests": _daily_series([r.created_at.date() for r in reqs], 7),
        "occupancy_trend": _occupancy_trend(db, hospital_id, 7),
        "resources": [schemas.CapacityOut.model_validate(c).model_dump() for c in caps.values()],
    }


@ana_r.get("/forecast/{hospital_id}")
def forecast(hospital_id: int, db: Session = Depends(get_db),
             user: User = Depends(require_roles("hospital_staff", "admin"))):
    """Capacity prediction (when each resource may run out) + demand forecasting by hour."""
    if user.role == "hospital_staff" and user.hospital_id != hospital_id:
        raise HTTPException(403, "Not your hospital")
    reqs = db.scalars(select(ReferralRequest).where(ReferralRequest.selected_hospital_id == hospital_id)).all()
    resp = [r.response_seconds for r in reqs if r.response_seconds is not None][-20:]
    return {"capacity": services.capacity_forecast(db, hospital_id),
            "demand": services.demand_forecast(reqs),
            "predicted_response_minutes": round(sum(resp) / len(resp) / 60, 1) if resp else None}


@ana_r.get("/system")
def system_analytics(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    hospitals = db.scalars(select(Hospital)).all()
    verified = [h for h in hospitals if h.verification_status == "verified"]
    occ = sorted(({"hospital_id": h.id, "hospital": h.name, "city": h.city, "occupancy_percent": _occupancy(h),
                   "latitude": h.latitude, "longitude": h.longitude} for h in verified),
                 key=lambda x: -x["occupancy_percent"])
    # regional view: free beds per city
    region = defaultdict(lambda: {"total": 0, "available": 0, "hospitals": 0})
    for h in verified:
        g = region[h.city or "Unknown"]
        g["hospitals"] += 1
        for c in h.capacities:
            if c.resource_type in BED_TYPES:
                g["total"] += c.total_capacity
                g["available"] += c.available_capacity
    areas = sorted(({"city": k, **v, "available_percent": round(100 * v["available"] / v["total"], 1) if v["total"] else 0}
                    for k, v in region.items()), key=lambda x: x["available_percent"])
    reqs = db.scalars(select(ReferralRequest)).all()
    resp = [r.response_seconds for r in reqs if r.response_seconds is not None]
    by_hour = Counter(r.created_at.hour for r in reqs)
    resource_counts = Counter(res for r in reqs for res in r.resource_list)
    service_counts = Counter(r.required_service for r in reqs if r.required_service)
    resp_by_h = defaultdict(list)
    for r in reqs:
        if r.response_seconds is not None: resp_by_h[r.selected_hospital_id].append(r.response_seconds)
    names = {h.id: h.name for h in hospitals}
    return {
        "total_hospitals": len(verified),
        "pending_hospitals": sum(1 for h in hospitals if h.verification_status == "pending"),
        "total_users": db.scalar(select(func.count(User.id))),
        "total_requests": len(reqs),
        "open_requests": sum(1 for r in reqs if r.request_status in OPEN_STATUSES),
        "admitted_today": sum(1 for r in reqs if r.admitted_at and r.admitted_at.date() == utcnow().date()),
        "suspicious_unreviewed": db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.suspicious == True, AuditLog.reviewed == False)),  # noqa: E712
        "highest_occupancy_hospitals": occ[:5],
        "hospital_occupancy": occ,
        "low_capacity_areas": [a for a in areas if a["available_percent"] < 20],
        "areas": areas,
        "most_requested_resources": resource_counts.most_common(6),
        "most_requested_services": service_counts.most_common(5),
        "requests_by_status": dict(Counter(r.request_status for r in reqs)),
        "average_response_seconds": round(sum(resp) / len(resp), 1) if resp else None,
        "response_time_by_hospital": sorted(
            ({"hospital": names.get(k, k), "average_seconds": round(sum(v) / len(v), 1)} for k, v in resp_by_h.items()),
            key=lambda x: x["average_seconds"]),
        "daily_requests": _daily_series([r.created_at.date() for r in reqs], 14),
        "occupancy_trend": _occupancy_trend(db, None, 14),
        "requests_by_hour": [by_hour.get(h, 0) for h in range(24)],
        "peak_demand_hours": [{"hour": h, "requests": n} for h, n in by_hour.most_common(3)],
    }


# ====================== ADMIN ======================
@admin_r.post("/users", response_model=schemas.UserOut, status_code=201)
def admin_create_user(data: schemas.AdminCreateUserIn, db: Session = Depends(get_db),
                      admin: User = Depends(require_roles("admin"))):
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "Username already taken")
    if data.role == "hospital_staff" and not db.get(Hospital, data.hospital_id or 0):
        raise HTTPException(400, "Hospital staff needs a valid hospital")
    u = User(username=data.username, password_hash=hash_password(data.password),
             full_name=data.full_name or data.username, phone=data.phone, role=data.role,
             hospital_id=data.hospital_id if data.role == "hospital_staff" else None)
    db.add(u)
    services.audit(db, admin.id, u.hospital_id, "user_created", {"username": u.username, "role": u.role})
    db.commit(); db.refresh(u)
    return u


@admin_r.get("/users", response_model=list[schemas.UserOut])
def admin_list_users(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    return db.scalars(select(User).order_by(User.role, User.username)).all()


@admin_r.patch("/users/{uid}/active")
def admin_toggle_user(uid: int, active: bool, db: Session = Depends(get_db),
                      admin: User = Depends(require_roles("admin"))):
    u = db.get(User, uid)
    if not u: raise HTTPException(404, "User not found")
    if u.id == admin.id and not active:
        raise HTTPException(400, "You cannot disable your own account")
    u.is_active = active
    services.audit(db, admin.id, u.hospital_id, "user_status_changed", {"user": u.username, "active": active})
    db.commit()
    return {"id": uid, "is_active": active}


@admin_r.get("/hospitals", response_model=list[schemas.HospitalOut])
def admin_list_hospitals(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    """All hospitals including pending, rejected and suspended ones."""
    return [hospital_out(h) for h in db.scalars(select(Hospital).order_by(Hospital.verification_status, Hospital.name))]


@admin_r.patch("/hospitals/{hid}/verify")
def admin_verify(hid: int, status: str = Query(pattern="^(verified|rejected|pending)$"),
                 db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    h = db.get(Hospital, hid)
    if not h: raise HTTPException(404, "Hospital not found")
    h.verification_status = status
    services.audit(db, admin.id, hid, "verification_changed", status)
    services.notify_hospital_staff(db, hid, f"Your hospital verification status is now: {status}.")
    db.commit()
    return {"hospital_id": hid, "verification_status": status}


@admin_r.patch("/hospitals/{hid}/status")
def admin_account_status(hid: int, status: str = Query(pattern="^(active|suspended)$"),
                         db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    h = db.get(Hospital, hid)
    if not h: raise HTTPException(404, "Hospital not found")
    h.account_status = status
    services.audit(db, admin.id, hid, "account_status_changed", status)
    db.commit()
    return {"hospital_id": hid, "account_status": status}


@admin_r.get("/outdated")
def outdated_hospitals(db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    out = []
    for h in db.scalars(select(Hospital).where(Hospital.verification_status == "verified")).all():
        old = [c for c in h.capacities if c.total_capacity and services.is_outdated(c)]
        if old:
            out.append({"hospital_id": h.id, "name": h.name, "outdated_resources": [c.resource_type for c in old],
                        "oldest_update": min(c.last_updated for c in old)})
    return out


@admin_r.get("/audit-logs")
def audit_logs(suspicious_only: bool = False, hospital_id: int | None = None,
               limit: int = Query(100, le=1000), db: Session = Depends(get_db),
               admin: User = Depends(require_roles("admin"))):
    q = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
    if suspicious_only: q = q.where(AuditLog.suspicious == True)  # noqa: E712
    if hospital_id: q = q.where(AuditLog.hospital_id == hospital_id)
    users = {u.id: u.username for u in db.scalars(select(User))}
    names = {h.id: h.name for h in db.scalars(select(Hospital))}
    return [{"id": a.id, "user_id": a.user_id, "username": users.get(a.user_id, "system"),
             "hospital_id": a.hospital_id, "hospital": names.get(a.hospital_id), "action": a.action,
             "details": a.details, "suspicious": a.suspicious, "reviewed": a.reviewed, "timestamp": a.timestamp}
            for a in db.scalars(q).all()]


@admin_r.patch("/audit-logs/{log_id}/review")
def review_log(log_id: int, db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    a = db.get(AuditLog, log_id)
    if not a: raise HTTPException(404, "Log entry not found")
    a.reviewed = True
    db.commit()
    return {"id": log_id, "reviewed": True}
