# Gap Analysis & Improvement Plan
**Project:** Smart Hospital Bed & Emergency Capacity System
**Compared against:** Hackathon brief (sections 1–11)

This document lists:
1. What the brief asks for
2. What the **original code (v1)** did or did not cover
3. What is **already fixed in v2** (the current files)
4. What is **still missing**, and how to build it

Status keys: ✅ done · 🟡 partly done · ❌ missing

---

## 1. Requirement-by-requirement gaps

### 1.1 Main flow (brief §1, §3, §10)
`Need → Search → Capacity check → Matching → Referral → Reservation → Admission → Capacity update → Analytics`

| Step | v1 (original) | Gap in v1 | v2 now |
|---|---|---|---|
| Choose the needed facility | ✅ Checkboxes | When several resources were selected, only the **first** one went into the request. A request for "ICU + ventilator" reserved only the ICU bed. | ✅ The request stores every resource. All of them are reserved together, and if one fails none are held. |
| Enter location | ✅ Lat/lon + GPS | No area presets, so typing coordinates was hard | ✅ Area dropdown + GPS + manual entry |
| Remove hospitals that lack the facility | 🟡 | A hospital with `total = 0` was shown as "Not Available" instead of "not offered" | ✅ Shown as "Not offered", with the reason |
| Rank suitable hospitals | ✅ | The score used distance, availability and freshness only. **Current occupancy** (a factor named in §4) was not used. The UI didn't show why a hospital ranked where it did. | ✅ Hospital load added to the score. Each card shows a score breakdown and a rank. |
| Send referral | ✅ | Used browser `prompt()`. Nothing stopped the same patient being sent twice. | ✅ Proper form. Duplicate open requests are blocked (HTTP 409). |
| Hospital reviews / accepts / rejects | ✅ | Rejection reason was hard-coded | ✅ Reason picker |
| Patient transferred | ✅ | — | ✅ Plus a `transferred_at` timestamp and a staff notification |
| Admission confirmed | ✅ | No proof the right patient arrived | ✅ QR code / 6-character confirmation code |
| Capacity updated automatically | ✅ | Beds were never released after admission (no discharge) | ✅ Discharge frees the bed(s) |
| Analytics updated | 🟡 | Numbers only, no charts | ✅ Charts: trends, per-day, per-hour, per-resource |
| Status `Searching` | ❌ | Defined but never used | ❌ Still unused (see §2, item 8) |

### 1.2 User roles (brief §2)

| Role / ability | v1 | v2 |
|---|---|---|
| **Patient**: search, pick service/bed, see availability, distance & details, submit request, track status | ✅ mostly. Hospital was shown as `#1` instead of its name. | ✅ Hospital name, progress stepper, live countdown for the bed hold |
| **Coordinator**: search by patient condition | 🟡 Rule-based classifier | ✅ Better classifier: reads vitals (SpO2, BP, pulse), age, gender; gives a confidence score |
| **Coordinator**: compare nearby hospitals | ❌ | ✅ Pick 2+ hospitals and see them side by side |
| **Coordinator**: view emergency resources, track responses, confirm transfer | ✅ | ✅ Plus a live capacity map |
| **Staff**: update beds / ICU / ventilators / ED capacity | ✅ | ✅ +/− steppers, unsaved-row highlight, "all numbers still correct" button |
| **Staff**: accept or reject requests | ✅ | ✅ Sorted by urgency, AI summary shown |
| **Staff**: manage admitted patients | ❌ | ✅ "Admitted patients" page with discharge |
| **Staff**: update service availability | ❌ No endpoint or UI | ✅ Hospital profile page: services, ED open/closed, contact |
| **Admin**: manage hospitals | 🟡 The list showed only verified hospitals. "Suspend" set verification to `rejected`, which hid the hospital from the list for good. | ✅ All hospitals listed. Separate verify/reject and suspend/re-activate actions. Add hospital. |
| **Admin**: verify hospital accounts | 🟡 No way for a hospital to sign up | ✅ Public hospital signup → pending → admin verifies |
| **Admin**: monitor capacity, review wrong/inactive records | 🟡 | ✅ Outdated-data list, suspicious-change review, "mark reviewed" |
| **Admin**: system statistics | ✅ Numbers only | ✅ Charts + regional heatmap |
| **Admin**: manage users and permissions | 🟡 API only, no UI | ✅ Users page: create, assign role and hospital, enable/disable |

### 1.3 Smart matching & AI (brief §4)

| Feature | v1 | v2 | Still possible |
|---|---|---|---|
| Hospital recommendation | ✅ | ✅ Rank + breakdown | Learn the weights from past accept/reject outcomes |
| Capacity prediction | ❌ | 🟡 Straight-line trend over 48 h of snapshots → "runs out in ~X h" | Seasonal model (hour of day × weekday), or Prophet/ARIMA once real data exists |
| Demand forecasting | ❌ | 🟡 Average requests per hour over the last 7 days → expected requests for the next 6 h | Per-service forecast, holiday/event effects |
| Request classification | 🟡 Keywords only | ✅ Keywords + vitals + age/gender + confidence | Optional LLM call with the rule-based result as fallback (§2, item 3) |
| Referral summarisation | ❌ It just cut the text at 140 characters | ✅ Structured: age, gender, condition, vitals, needs | LLM summary in a fixed clinical format |

### 1.4 Capacity & real-time handling (brief §5, §6)

| Requirement | v1 | v2 |
|---|---|---|
| 10 resource categories | ✅ | ✅ |
| Total / available / occupied / temporarily unavailable | ✅ | ✅ Plus "reserved" shown separately |
| Not just "Available / Full" | ✅ | ✅ Free/total counts on every card |
| **Prevent two people getting the same bed** | ❌ **Race condition.** The code read `available`, then incremented `reserved` in Python. Two staff accepting at the same moment could both get the last bed. | ✅ One conditional SQL `UPDATE … WHERE free > 0`. There is a test that proves it. |
| 15-minute temporary hold | ✅ | ✅ Plus a countdown in the UI and a notification when the hold expires |
| Capacity history | ❌ | ✅ `capacity_snapshots` table (needed for trends and forecasting) |

### 1.5 Data model (brief §7)
Every field in the brief exists. v2 adds `city`, `required_resources`, `summary`, `confirmation_code`, `distance_km`, `match_percent`, `transferred_at`, `discharged_at`, plus new `Notification` and `CapacitySnapshot` tables.

### 1.6 Search, dashboard & analytics (brief §8)

| Item | v1 | v2 |
|---|---|---|
| Filter by bed type, ICU, ventilator, hospital name, distance, service, emergency | 🟡 The API had filters, the UI didn't | ✅ Directory page with filters, plus filters in the matcher |
| Staff KPIs (total/occupied/available beds, ICU occupancy, emergency requests, accepted/rejected, avg response, daily admissions, utilisation) | ✅ Numbers | ✅ Numbers + charts + acceptance rate |
| Hospitals with highest occupancy | ✅ | ✅ Chart |
| **Areas** with low capacity | ❌ It listed *hospitals* above 85%, not areas (there was no city/area field) | ✅ Grouped by city |
| Most requested **services** | ❌ Only resources were counted | ✅ Both |
| Avg response time per hospital | ❌ System-wide only | ✅ Per hospital |
| Capacity trends by date | ❌ It counted *how many times* staff edited capacity, not occupancy | ✅ Daily occupancy % from snapshots |
| Peak demand periods | ✅ Top 3 hours | ✅ Full 24-hour chart |

### 1.7 Verification, security & data accuracy (brief §9)

| Control | v1 | v2 | Still to do |
|---|---|---|---|
| Verified hospital accounts | 🟡 | ✅ | Upload a licence document for the admin to check |
| Staff login + role permissions | ✅ | ✅ | — |
| Automatic last-updated timestamp | ✅ | ✅ | — |
| Outdated-data alert | ✅ | ✅ Banner for staff, list for admin | Email/SMS reminder to staff after X minutes |
| Audit log | ✅ | ✅ Readable "old → new" diffs | — |
| Admin review of suspicious updates | 🟡 Flag only | ✅ Flag + review workflow | Smarter detection, e.g. many edits in a short time |
| Protect patient information | ❌ | 🟡 HTML escaping (v1 could run injected HTML from patient names). Confirmation code hidden from staff. | See §2, item 5 |
| Protect admin controls | ✅ Role checks | ✅ Admin can't disable their own account | Rate limiting, 2FA for admins |

### 1.8 "Additional features" list (brief §10)

| Feature | v1 | v2 |
|---|---|---|
| Live hospital map | ✅ Results only | ✅ Results map, directory map, admin map |
| Estimated travel time | 🟡 Straight line × 1.3 | 🟡 Same formula | 
| Ambulance tracking | ❌ | ❌ |
| Automatic nearest-hospital recommendation | ✅ | ✅ |
| SMS / push notifications | ❌ | 🟡 In-app notifications (bell + toast). No SMS/push yet. |
| QR-based referral confirmation | ❌ | ✅ |
| Hospital wait-time prediction | ❌ | 🟡 Average of the last 20 response times |
| Capacity forecasting | ❌ | 🟡 Linear trend |
| Patient transfer tracking | 🟡 Status only | 🟡 Status + timestamps (no live GPS) |
| Regional capacity heatmap | ❌ | ✅ Coloured circles by occupancy + area table |

### 1.9 Submission (brief §11)

| Deliverable | Status |
|---|---|
| Demo video | ❌ Not recorded. A suggested script is in §4 below. |
| GitHub repo **or** deployed link | ❌ The folder is not a git repo and is not deployed |
| Project explanation document (PDF/Word) | 🟡 README has a structure table. A separate document is still needed. |

---

## 2. What is still missing, in priority order

### 🔴 High priority (do before submitting)

1. **Test v2 in a real browser.** The backend tests pass (3/3: full flow, double-booking, permissions). The new frontend has **not been opened in a browser yet**. Click through all 4 roles and fix anything broken.
2. **Deploy it** so judges get a link. The easiest path:
   - Backend + frontend together (FastAPI already serves `frontend/index.html` at `/`) on **Render** or **Railway**
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Set the `SECRET_KEY` environment variable (it currently falls back to a hard-coded value)
   - Use free **PostgreSQL** (Neon/Supabase) via `DATABASE_URL`. SQLite data is wiped on every redeploy on these hosts. Add `psycopg[binary]` to requirements.
3. **Push to GitHub** with a `.gitignore` that excludes `venv/`, `*.db` and `__pycache__/`.
4. **Write the explanation document**: folder/file purposes and how the parts connect (§3 below can be the starting point).

### 🟠 Medium priority (makes the project stronger)

5. **Security hardening**
   - Rate-limit `/auth/login` (`slowapi`) against password guessing
   - Restrict CORS with `CORS_ORIGINS=https://your-domain` instead of `*`
   - Use `argon2`/`bcrypt` (`passlib`) instead of PBKDF2 at 100k rounds
   - Shorter tokens (e.g. 1 h) + refresh token; the 12-hour token is long for medical data
   - Only an admin should approve coordinator accounts (today anyone can register as a coordinator)
   - Store less patient data: keep a reference/MRN rather than full names, and add a retention period
6. **Real-time push instead of polling.** The UI refreshes every 10–12 s. A WebSocket or Server-Sent Events endpoint (`/ws`) that pushes "capacity changed" and "request status changed" would make it truly live and cut server load.
7. **Real travel time.** Replace the straight-line × 1.3 estimate with OSRM (free, OpenStreetMap-based) or the Google Distance Matrix API. Cache results per hospital pair.
8. **Use the `Searching` status / broadcast referral.** Let the coordinator send one request to the **top 3** hospitals. The status stays `searching` until the first one accepts; the others are cancelled automatically. This removes the "call one by one" problem the brief describes.
9. **SMS / push notifications.** Twilio (SMS) or Firebase Cloud Messaging (push). The `services.notify()` function is already the single place to plug this in.
10. **Database migrations (Alembic).** Today a schema change means deleting `hospital.db`. Alembic lets the schema change without losing data.

### 🟢 Nice to have (extra marks)

11. **Ambulance tracking.** The coordinator's phone sends GPS every 10 s (`POST /requests/{id}/location`). The hospital sees a moving marker and a live ETA.
12. **Optional LLM for classification/summary.** Call an LLM API only when an API key is set; otherwise use the current rule-based classifier. This keeps the demo working offline.
13. **Better forecasting.** Once there is real data: hour-of-day × weekday averages, or Prophet. Show a confidence band, not a single number.
14. **Mobile.** Turn the page into a PWA (manifest + service worker) so it installs on a phone. That covers "web or mobile" without a separate app.
15. **Urdu / bilingual interface** for patients and attendants.
16. **Split the frontend.** `index.html` is a single ~600-line file. For a long-lived project, move to Vite + React/Vue with separate components. For the hackathon the single file is fine and easy to deploy.
17. **More automated tests:** expiry of the 15-minute hold, hospital signup → verify → visible in search, frontend smoke test with Playwright.
18. **Hospital licence upload** during signup, so the admin has something real to verify.

---

## 3. Current project structure (for the explanation document)

| Path | Purpose |
|---|---|
| `app/main.py` | Starts the app, creates tables, seeds demo data, runs the 30-second loop that expires 15-minute holds, serves the frontend at `/` |
| `app/database.py` | Database connection (`DATABASE_URL`, SQLite by default) |
| `app/models.py` | Tables: User, Hospital, Capacity, CapacitySnapshot, ReferralRequest, Notification, AuditLog |
| `app/schemas.py` | Input/output validation (Pydantic) |
| `app/security.py` | Password hashing, JWT login, role checks |
| `app/services.py` | Business logic: smart matching, AI classification & summary, atomic reservation, forecasting, notifications, audit |
| `app/routes.py` | All API endpoints (auth, hospitals, matching, requests, notifications, analytics, admin) |
| `app/seed.py` | 7 demo hospitals, users, 7 days of history |
| `frontend/index.html` | The whole user interface (4 role dashboards, charts, maps, QR) |
| `tests/test_flow.py` | Automated end-to-end tests |

**How the parts connect:** Browser (`frontend/index.html`) → REST API (`routes.py`) → logic (`services.py`) → database (`models.py` / `database.py`). The background loop in `main.py` releases expired holds.

---

## 4. Suggested demo video script (3–4 minutes)

1. **Coordinator:** type *"60 year old man unconscious after heart attack, SpO2 84%"* → **Analyse** → ICU + ventilator and *critical* are filled in → search → ranked list + map → compare 2 hospitals → send referral.
2. **Staff (staff1):** notification arrives → request shows the AI summary → **Accept** → the capacity page shows ICU "reserved +1".
3. **Coordinator:** countdown + QR code → **Confirm transfer**.
4. **Staff:** enter the code → **Admit** → occupied +1. Then show the dashboard charts and forecast.
5. **Admin:** system overview, heatmap, verify the pending hospital (Hala Children Hospital), suspicious-change review in the audit log.
6. End on the brief's core line: *Need → Capacity → Matching → Referral → Admission → Updated Capacity*.

---

## 5. Notes on the v2 changes
- The old database was **renamed** to `hospital.backup-v1.db`, not deleted. v2 creates a fresh `hospital.db` with demo data on first start.
- Run: `pip install -r requirements.txt` → `uvicorn app.main:app --reload` → open http://127.0.0.1:8000
- Tests: `python -m pytest -q`
