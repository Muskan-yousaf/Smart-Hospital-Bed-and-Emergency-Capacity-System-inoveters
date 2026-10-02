# MedBed Connect — Smart Hospital Bed & Emergency Capacity System

A web platform where hospitals publish live bed, ICU, ventilator and emergency capacity. Patients and ambulance coordinators use it to find the most suitable hospital, send a referral, and get a bed held while the patient is transferred.

**Core flow:** Need → Capacity → Matching → Referral → Admission → Updated Capacity

## Features

| Area | What it does |
|---|---|
| Smart hospital matching | Ranks hospitals by required resources, distance, travel time, current occupancy, emergency department and specialist service, and how fresh the data is. Unsuitable hospitals are listed with the reason. |
| AI assist | Reads a free-text case description and picks out the needed facilities, urgency, vitals (SpO2, BP, pulse), age and gender. Produces a short structured referral summary. |
| Referral workflow | Request sent → Hospital reviewing → Accepted → Patient transferred → Admitted → Discharged (plus Rejected / Cancelled / Expired / No capacity) |
| Reservation control | When a request is accepted, one unit of every required resource is held for 15 minutes, all or nothing. The reservation is a single atomic SQL update, so the last bed can never be given to two patients. Expired holds are released automatically. |
| QR referral confirmation | The patient gets a QR code / 6-character code. The hospital verifies it on arrival. |
| Capacity management | 10 resource types (general, emergency, ICU, NICU, ventilator, OT, isolation, dialysis, trauma, ambulance), each with total, occupied, reserved and out-of-service counts. "May be outdated" warnings appear after 60 minutes without an update. |
| Forecasting | Predicts when each resource may run out (trend over the last 48 h) and how many requests to expect in the next 6 hours. |
| Dashboards & analytics | Hospital KPIs and charts; system-wide occupancy, regional heatmap, peak demand hours, most requested resources and services, response times |
| Security & accuracy | Verified hospital accounts, role-based access (patient / coordinator / hospital staff / admin), JWT login, audit log, suspicious-change flagging with admin review |
| Notifications | In-app notifications for every status change |
| Comparison | Compare hospitals side by side before sending a referral |

## Tech stack
- **Backend:** Python, FastAPI, SQLAlchemy, SQLite (PostgreSQL supported through `DATABASE_URL`), JWT
- **Frontend:** HTML/CSS/JavaScript (single page), Leaflet + OpenStreetMap, Chart.js, QRCode.js
- **Tests:** pytest

## Run locally
```bash
python -m venv venv
venv\Scripts\activate            # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open **http://127.0.0.1:8000** for the app, or **http://127.0.0.1:8000/docs** for the API documentation.

Demo data (7 hospitals, users, 7 days of history) is created automatically on first start. To reset it, stop the server and delete `hospital.db`.

Run the tests:
```bash
python -m pytest -q
```

## Demo accounts
| Role | Username | Password |
|---|---|---|
| Patient / attendant | `patient1` | `patient123` |
| Ambulance coordinator | `coord1` | `coord123` |
| Hospital staff (City Care Hospital) | `staff1` | `staff123` |
| Hospital staff (other hospitals) | `staff2`, `staff3`, `staff5`, `hala` | `staff123` |
| Administrator | `admin` | `admin123` |

## Project structure
```
smart-hospital/
├── app/
│   ├── main.py        Starts the app, creates tables, seeds demo data, expires 15-min holds, serves the frontend
│   ├── database.py    Database connection
│   ├── models.py      Tables: User, Hospital, Capacity, CapacitySnapshot, ReferralRequest, Notification, AuditLog
│   ├── schemas.py     Request/response validation
│   ├── security.py    Password hashing, JWT, role checks
│   ├── services.py    Matching, AI classification & summary, reservation, forecasting, notifications, audit
│   ├── routes.py      All API endpoints
│   └── seed.py        Demo data
├── frontend/
│   └── index.html     User interface for all four roles
├── tests/
│   └── test_flow.py   End-to-end tests (full referral flow, double-booking, permissions)
├── GAPS_AND_IMPROVEMENTS.md
└── requirements.txt
```

**How the parts connect:** Browser (`frontend/index.html`) → REST API (`routes.py`) → business logic (`services.py`) → database (`models.py`). A background task in `main.py` releases expired reservations every 30 seconds.

## Configuration (environment variables)
| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | development value | JWT signing key. **Set this in production.** |
| `DATABASE_URL` | `sqlite:///./hospital.db` | Database connection |
| `CORS_ORIGINS` | `*` | Allowed frontend origins |
| `SEED_DEMO_DATA` | `1` | Set to `0` to skip demo data |
