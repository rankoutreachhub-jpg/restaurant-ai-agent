# AI Restaurant Agent — Stage 1 (Q&A Chatbot)

This is Stage 1 of the AI Restaurant Agent MVP: a chatbot that answers
customer questions about a restaurant (menu, hours, location, FAQs)
using **only** real restaurant data — never invented facts.

No booking functionality yet — that's Stage 2+.

**This version fixes environment-variable handling: your API key is now
loaded automatically from a `.env` file. You do NOT need to manually set
it in your terminal every time.**

---

## Folder structure

```
restaurant-ai-agent/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py            # FastAPI app entry point + startup checks
│   │   ├── config.py          # Loads .env, validates required settings
│   │   ├── database.py        # DB connection setup
│   │   ├── models.py          # Database tables
│   │   ├── schemas.py         # API request/response shapes
│   │   ├── knowledge.py       # Builds restaurant context for the LLM
│   │   ├── llm.py             # Calls Claude API with strict system prompt
│   │   ├── seed_data.py       # Example restaurant data
│   │   └── routers/
│   │       ├── __init__.py
│   │       └── chat.py        # POST /chat endpoint
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── index.html             # Standalone chat widget (no API key in it)
├── .gitignore
└── README.md
```

---

## 1. Prerequisites

- Python 3.10+ installed (check with `python --version` in PowerShell)
- A Google Gemini API key (get one at https://aistudio.google.com/apikey)

---

## 2. Setup — exact Windows PowerShell commands

Open **PowerShell** and run these one at a time:

```powershell
# 1. Go into the backend folder (adjust path to wherever you unzipped it)
cd restaurant-ai-agent\backend

# 2. Create a virtual environment
python -m venv venv

# 3. Allow the activation script to run (only needed once per machine —
#    Windows blocks scripts by default). If you get a "running scripts is
#    disabled" error without this, run it first:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 4. Activate the virtual environment
.\venv\Scripts\Activate.ps1

# You should now see (venv) at the start of your PowerShell prompt.

# 5. Install dependencies
pip install -r requirements.txt

# 6. Create your real .env file from the example
Copy-Item .env.example .env

# 7. Open .env in Notepad and paste in your real API key
notepad .env
```

In Notepad, replace the placeholder line with your real key, e.g.:

```
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

While you're in there, also set `ADMIN_API_KEY` to a long random secret
(this protects the `/admin/*` endpoints — restaurant, menu, and opening
hours management). Generate one with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
ADMIN_API_KEY=paste-the-generated-secret-here
```

Save and close Notepad.

```powershell
# 8. Create the database schema (see "Database migrations" below) —
#    required once, and again after pulling any future change that
#    adds a migration.
alembic upgrade head
```

**That's it for setup.** Both keys are now loaded automatically every
time you run the server — no manual `$env:GEMINI_API_KEY=...` needed.

---

## 3. Running the application (Windows PowerShell)

```powershell
# Make sure you're in backend\ with the venv active — you should see (venv)
uvicorn app.main:app --reload
```

**If either API key is missing or empty in `.env`**, the server will
refuse to start and print a clear message telling you exactly what's
wrong and how to fix it, instead of starting broken.

If everything is correct, you'll see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

The database file (`backend\restaurant.db`) is created by the
`alembic upgrade head` step above (not by running the server); example
restaurant data is seeded automatically the first time the server runs
against it.

**Start the frontend:** open `frontend\index.html` by double-clicking it
(it opens in your default browser). It talks to the backend at
`http://127.0.0.1:8000/chat` — the backend must be running first. No API
key is ever present in this file — all Gemini API calls happen on the
backend only.

---

### Admin authentication

Every `/admin/*` endpoint (restaurant details, menu, opening hours, and
booking management) requires a valid `X-Admin-API-Key` header. Requests
without it, or with an invalid/inactive value, get a `401 Unauthorized`
response — the `/chat` and `/health` endpoints are unaffected and need
no key.

Two kinds of admin key are accepted:

- **Platform superadmin** — the `ADMIN_API_KEY` value in `.env` (below).
  Has access to **every** restaurant. Intended for platform operations
  (onboarding a restaurant, issuing its first admin key) rather than
  routine day-to-day restaurant admin work.
- **Restaurant-scoped admin** — a per-restaurant key issued through the
  `/admin/platform/admin-users` endpoints (see "Multi-tenant admin
  access" below), only ever valid for the restaurant(s) it was
  explicitly granted.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/restaurant/1 `
  -Headers @{ "X-Admin-API-Key" = "paste-your-real-admin-key-here" }
```

---

### Rotating the admin key

Rotate `ADMIN_API_KEY` periodically, or immediately if you suspect it
leaked, without locking out clients mid-rotation:

1. **Generate a new key:**
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. **Edit `.env`:** move the *current* value of `ADMIN_API_KEY` into a
   new `ADMIN_API_KEY_PREVIOUS` line, then set `ADMIN_API_KEY` to the
   newly generated value.
   ```
   ADMIN_API_KEY=the-new-key-you-just-generated
   ADMIN_API_KEY_PREVIOUS=the-old-key-that-was-in-use
   ```
3. **Restart the server.** Both the new key and the old key now work —
   this is the transition window.
4. **Update every admin client/script** (anything sending
   `X-Admin-API-Key`) to use the new key.
5. **Once you've confirmed nothing still uses the old key**, delete the
   `ADMIN_API_KEY_PREVIOUS` line from `.env` and restart. The old key is
   now fully revoked — only the new key works.

Notes:
- `ADMIN_API_KEY_PREVIOUS` is optional and has no effect if left unset
  — normal (non-rotating) operation is unchanged.
- Neither key is ever echoed back in a response body or written to the
  server logs, during a rotation or otherwise — only the fact that a
  request was accepted or rejected is observable.
- Keep the transition window short; the whole point of
  `ADMIN_API_KEY_PREVIOUS` is to make rotation safe, not to run two
  keys indefinitely.

---

### Multi-tenant admin access (Stage 3 Step 3)

Multiple restaurants can now share the same database, each with its own
admin key(s) that can only ever touch that restaurant's data. This is
managed through a `/admin/platform/*` API that **only accepts the
platform superadmin key** (`ADMIN_API_KEY`/`ADMIN_API_KEY_PREVIOUS`) —
a restaurant-scoped key can never create other admin users or grant
itself access to a restaurant it doesn't already have.

**Onboard a new restaurant:**
```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/platform/restaurants -Method Post `
  -Headers @{ "X-Admin-API-Key" = "your-superadmin-key" } `
  -ContentType "application/json" `
  -Body '{"name": "The Anchor", "address": "1 Quay St", "phone": "0117 000 0000", "email": "hello@theanchor.example.com", "seating_capacity": 30}'
```

**Issue that restaurant an admin key** (`restaurant_ids` can list more
than one restaurant, for an admin who manages several):
```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/platform/admin-users -Method Post `
  -Headers @{ "X-Admin-API-Key" = "your-superadmin-key" } `
  -ContentType "application/json" `
  -Body '{"label": "The Anchor - front of house", "restaurant_ids": [2]}'
```
The response's `api_key` field is the **only time** the plaintext key is
ever shown — only its hash is stored, so save it immediately. Give this
key to that restaurant's admin; every `/admin/restaurant/2/...` request
they make with it works exactly like the superadmin key does today, and
every request they make against a *different* restaurant_id gets the
same `404 Restaurant not found` as if it didn't exist — never a `403`,
so an admin key can't be used to even confirm another restaurant's ID
is in use.

**Other platform-admin operations** (all require the superadmin key):
| Method & path | Purpose |
|---|---|
| `GET /admin/platform/admin-users` | List admin users (never returns key material) |
| `GET /admin/platform/admin-users/{id}` | Inspect one admin user |
| `PATCH /admin/platform/admin-users/{id}` | Deactivate (`is_active: false`) / reactivate / relabel |
| `POST /admin/platform/admin-users/{id}/rotate-key` | Issue a new key immediately; the old one stops working at once (no overlap window, unlike superadmin rotation above) |
| `POST /admin/platform/admin-users/{id}/restaurants/{restaurant_id}` | Grant an additional restaurant |
| `DELETE /admin/platform/admin-users/{id}/restaurants/{restaurant_id}` | Revoke a restaurant grant |

Notes:
- This is purely additive: the existing single `ADMIN_API_KEY` workflow
  from Stages 1-2 keeps working exactly as before, with implicit access
  to every restaurant. You don't need to touch any of this to keep
  running a single restaurant.
- A restaurant-scoped key is never logged or returned in any response
  except its own creation/rotation call.
- Once a restaurant is onboarded, its menu, FAQs, and opening hours can
  all be populated the same way — see "Restaurant onboarding
  completeness" below. No direct database access is needed for any of
  it.

---

### Restaurant onboarding completeness (Stage 3 Step 4)

A newly onboarded restaurant (via `/admin/platform/restaurants` above)
starts with no menu, no FAQs, and no opening hours. All three can now
be added entirely through the API — no direct database insert needed
for any of it (previously true for FAQs and opening hours specifically).

**FAQs** — full CRUD, same shape and auth as menu items:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/restaurant/2/faqs -Method Post `
  -Headers @{ "X-Admin-API-Key" = "your-key" } -ContentType "application/json" `
  -Body '{"question": "Do you take walk-ins?", "answer": "Yes, subject to availability."}'
```
| Method & path | Purpose |
|---|---|
| `GET /admin/restaurant/{id}/faqs` | List all FAQs |
| `POST /admin/restaurant/{id}/faqs` | Create one FAQ (`201`) |
| `PATCH /admin/restaurant/{id}/faqs/{faq_id}` | Update question/answer |
| `DELETE /admin/restaurant/{id}/faqs/{faq_id}` | Delete |

**Opening hours** — a new `POST` creates the initial rows (the existing
`PATCH .../opening-hours/{day}` only ever updates a day that already
exists). Accepts 1 to 7 days in one call, so you can set up the whole
week at once or add days individually:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/restaurant/2/opening-hours -Method Post `
  -Headers @{ "X-Admin-API-Key" = "your-key" } -ContentType "application/json" `
  -Body '{"days": [
    {"day_of_week": "Monday", "open_time": "12:00", "close_time": "22:00", "is_closed": false},
    {"day_of_week": "Sunday", "is_closed": true}
  ]}'
```
Notes:
- `open_time`/`close_time` must be 24-hour `"HH:MM"` and are required
  unless `is_closed` is `true` — validated at creation (`422` on bad
  input) rather than only surfacing later as a booking-availability
  error.
- If **any** requested day already has a row for that restaurant, the
  **whole request** is rejected with `409` (no partial creation) — the
  day-of-week + restaurant combination is unique at the database level,
  not just checked in the handler, so this can't be bypassed by a race
  between two concurrent requests either. Use `PATCH` to change a day
  that already exists.

---

### Rate limiting

Both `/chat` and `/admin/*` are rate-limited per client IP (in-memory,
no external service needed):

- `/chat`: 10 requests per minute — generous for a real conversation,
  tight enough to stop a script from running up the Gemini bill.
- `/admin/*`: 30 requests per minute — bounds brute-forcing/flooding of
  the admin key. This check runs *before* the key check, so even
  unauthenticated guesses count against the limit.
- `/health` is never rate-limited.

Exceeding the limit returns `429 Too Many Requests` with a `Retry-After`
header (seconds until the window resets).

**Known limitation:** the counters live in the process's memory, so
this only enforces the stated limit correctly for a single-process
deployment (the default `uvicorn app.main:app` setup). Running multiple
worker processes, or multiple instances behind a load balancer, gives
each process its own counters — the effective limit becomes
`max_requests × number of processes`. A shared store (e.g. Redis) would
be needed to enforce a true global limit across processes.

---

### Chat input limits

`/chat` request bodies are capped so a client can't force unbounded,
costly context into every Gemini call:

- `message`: max 2000 characters.
- `history`: max 40 entries, each with `content` capped at 2000
  characters too, and `role` restricted to `"user"` or `"assistant"`
  (so a client can't inject a fake `"system"` turn into the Gemini call).

A request over either limit, or with an invalid `role`, gets a
`422 Unprocessable Entity` with the standard FastAPI/Pydantic validation
error body, before anything is sent to Gemini.

---

### CORS

The API only answers cross-origin browser requests (CORS) from origins
listed in `ALLOWED_ORIGINS` — it no longer allows every origin (`*`).

If `ALLOWED_ORIGINS` isn't set in `.env`, it defaults to:

```
http://localhost:3000,http://127.0.0.1:3000,
http://localhost:5500,http://127.0.0.1:5500,
http://localhost:8000,http://127.0.0.1:8000,
null
```

`"null"` is the literal `Origin` value browsers send for a page opened
directly as a local file — i.e. exactly how this README tells you to
open `frontend/index.html` (by double-clicking it) — so that keeps
working out of the box. The other defaults cover common local dev
server ports (a simple static-file server, Create React App, Vite,
VS Code Live Server, etc.).

**Before deploying**, set `ALLOWED_ORIGINS` to your real frontend
domain(s) and drop the localhost/`null` defaults, e.g.:

```
ALLOWED_ORIGINS=https://www.example.com,https://example.com
```

A request from an origin not on the list still gets a normal response
from the server (CORS is enforced by the browser reading response
headers, not by the server refusing to answer) — it just won't include
the `Access-Control-Allow-Origin` header, so a real browser blocks the
page's JavaScript from reading it. A CORS *preflight* request (sent
automatically by browsers before some cross-origin calls) from a
disallowed origin gets `400 Bad Request`.

Everything above governs `/admin`, `/chat`, `/webhooks/whatsapp`, and
`/health` — it does **not** apply to the public widget endpoints under
`/widget/*`, which use their own, separate mechanism (below).

#### Widget allowed origins (Stage 4 Phase D)

`ALLOWED_ORIGINS` is a single, operator-configured list — fine for this
project's own admin dashboard and chat frontend, but the widget can be
embedded on any restaurant's own website, which can't be known or
pre-registered as one shared list. So each restaurant's widget instead
has its own allowed-origin set, managed per-restaurant through the admin
API:

```
POST   /admin/restaurant/{id}/widget-config/origins           {"origin": "https://www.your-restaurant.com"}
DELETE /admin/restaurant/{id}/widget-config/origins/{origin_id}
```

`GET`/`POST /admin/restaurant/{id}/widget-config` also returns the
restaurant's current `allowed_origins` list. An origin must be an exact
`scheme://host[:port]` — no path, query, fragment, trailing slash, or
wildcard subdomain — matching the literal `Origin` header a browser
sends, and each origin needs its own row (`https://example.com` and
`https://www.example.com` are two separate entries).

A widget with **zero** configured origins fails closed: every
cross-origin browser request to its `/widget/{widget_key}/config` or
`/widget/{widget_key}/chat` is rejected (`403`, no CORS headers) until at
least one origin is registered. A non-browser request (no `Origin`
header — curl, server-to-server) is unaffected either way. An unknown or
inactive `widget_key` still gets the same generic `404` it always did,
never a `403` — this check only ever narrows an *already-resolved*
widget's own request, and never doubles as a way to probe which
`widget_key`s exist.

`WIDGET_PREVIEW_ORIGIN` (optional, see `.env.example`) is a separate,
platform-level carve-out — a single origin permitted against *any*
restaurant's `widget_key`, for a future internal "preview this widget"
feature. It has no effect today; leave it unset.

---

### Application logging

Errors and important events are logged to **both** the console and a
rotating log file at `backend/logs/app.log` (created automatically on
first run — set `LOG_DIR` in `.env` to use a different location). Every
line has a timestamp, level, and logger name, e.g.:

```
2026-01-15 09:12:03,441 WARNING app.auth: Admin authentication failed (client=203.0.113.7 path=/admin/restaurant/1)
2026-01-15 09:14:20,118 ERROR app.routers.chat: Unhandled error while generating a chat reply
2026-01-15 09:20:44,902 WARNING app.rate_limit: Rate limit exceeded (limiter=chat client=203.0.113.7 path=/chat)
```

What's logged: unhandled `/chat` errors (with the real exception detail
— the client only ever sees a generic message, per the "Chat input
limits"-adjacent error handling above), rejected admin authentication
attempts, rate-limit throttling, and restaurant-data seeding at startup.

**What's deliberately never logged:** `GEMINI_API_KEY`, `ADMIN_API_KEY`
/ `ADMIN_API_KEY_PREVIOUS`, the `X-Admin-API-Key` header value (valid
*or* invalid — a rejected admin request logs only the client IP and
path, never the key that was tried), full request/response bodies, or
customer chat message content.

**Rotation:** once `app.log` reaches ~1 MB, it's renamed `app.log.1`
(and any existing `app.log.1` → `app.log.2`, etc.), and a fresh
`app.log` is started. Up to 5 rotated backups are kept
(`app.log.1`–`app.log.5`); older ones are deleted automatically. This
bounds disk usage without needing an external log-shipping service —
enough for this MVP's single-instance deployment.

`backend/logs/` is listed in `.gitignore` — log files are generated
locally and are never committed.

---

### Error tracking (Sentry)

Optional and off by default. Set `SENTRY_DSN` in `.env` to a real
Sentry project DSN to start reporting unhandled server-side exceptions
there; leave it unset and `app/monitoring.py`'s `init_sentry()` never
calls `sentry_sdk.init()` at all — no import side effects, no network
calls, nothing collected or sent anywhere. Works the same way locally,
in Docker, or in production — nothing else to configure.

Two more variables are optional: `SENTRY_ENVIRONMENT` (a free-text
label shown in the Sentry UI, e.g. `production`/`staging` — defaults
to `production`) and `SENTRY_TRACES_SAMPLE_RATE` (0.0–1.0, for Sentry's
separate performance-tracing feature — defaults to `0.0`; error capture
itself always happens regardless of this value).

**What's never sent, regardless of configuration** — customer chat
message content, names, phone numbers, emails, booking details, admin
API keys, WhatsApp access tokens/signatures, conversation tokens, or
any request body/header. This holds even if a bug were ever introduced
at a call site, via three independent layers (see `app/monitoring.py`
for the full detail): local variables are never attached to a captured
exception at all (`include_local_variables=False` — the setting that
matters most, since request/customer data is exactly what sits in
local variables at the point an error is caught); request bodies,
headers, cookies, and query strings are stripped from every event
before it's sent; and this project's own existing logging discipline
(above) already keeps `logger.exception(...)` calls free of message
content or secrets, which is what Sentry's default logging integration
actually captures for the app's three existing "catch and return a
generic 500" sites (`routers/chat.py`, `routers/widget.py`,
`whatsapp_processing.py`) — no source-code change was needed at any of
those three sites for this to work.

The generic, non-revealing 500 response the client receives is
completely unchanged by whether Sentry is configured — see "What's
deliberately never logged" above; the same detail that was always kept
out of the HTTP response stays out of it.

---

### Table bookings (Stage 2a)

Admin-managed table bookings, protected exactly like every other
`/admin/*` endpoint (same admin rate limiter and API key, including
rotation support). **Not yet reachable through `/chat`** — AI-assisted
booking is a later stage; for now, bookings are created/managed only
through this admin API.

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/restaurant/{id}/bookings` | List bookings. Optional `?status=confirmed\|cancelled` and `?booking_date=YYYY-MM-DD` filters. |
| POST | `/admin/restaurant/{id}/bookings` | Create a booking. |
| GET | `/admin/restaurant/{id}/bookings/{booking_id}` | Get one booking. |
| PATCH | `/admin/restaurant/{id}/bookings/{booking_id}` | Update any field, including `status` (e.g. to `"cancelled"`). |
| DELETE | `/admin/restaurant/{id}/bookings/{booking_id}` | Permanently remove a booking (prefer cancelling via `PATCH` to keep a record). |

A booking has: `customer_name`, `phone`, `email` (validated as a real
email address), `booking_date`, `booking_time`, `party_size` (1–20),
an optional `notes`, and `status` (`confirmed` or `cancelled`,
defaulting to `confirmed`).

```powershell
Invoke-RestMethod http://127.0.0.1:8000/admin/restaurant/1/bookings `
  -Method Post -ContentType "application/json" `
  -Headers @{ "X-Admin-API-Key" = "paste-your-real-admin-key-here" } `
  -Body '{"customer_name":"Jane Doe","phone":"01234 567890","email":"jane@example.com","booking_date":"2026-12-24","booking_time":"19:00","party_size":4}'
```

**Overlap prevention (capacity-based, not per-table):** the restaurant
has a single `seating_capacity` (visible/editable via the existing
`GET`/`PATCH /admin/restaurant/{id}` endpoints — 40 in the seed data).
Each booking is assumed to occupy its table(s) for 90 minutes starting
at `booking_time`. A new or updated booking is rejected with
`409 Conflict` if:
- the restaurant is closed on that day, or the requested time falls
  outside that day's opening hours, or
- the combined party size of every other overlapping, non-cancelled
  booking that day — plus this one — would exceed `seating_capacity`.

Cancelling a booking (`PATCH` with `status: "cancelled"`) immediately
frees its share of capacity for other bookings; re-confirming a
cancelled booking re-runs this same check, since something else may
have taken its slot in the meantime.

`booking_date` must be today or later, and no more than 365 days out —
a sanity cap, not a real business rule, same rationale as the `/chat`
input limits.

**Known limitation:** like the in-memory rate limiter, this is a
check-then-insert rather than a database-enforced guarantee — fine at
this MVP's single-process scale, but not a substitute for a real
concurrency-safe reservation system at larger scale.

**Schema change note:** adding bookings introduced a new `bookings`
table and a new `seating_capacity` column on `restaurants`. At the time,
this project had no migration tool, so a `restaurant.db` created before
this change was missing that column and needed recreating from
scratch. That's no longer how schema changes are handled — see
"Database migrations" below, which covers exactly this scenario for any
database (including an existing pre-Alembic `restaurant.db`).

---

### Chat awareness of dates and availability (Stage 2b)

`/chat`'s restaurant context (`knowledge.py`) now also includes:
- **`CURRENT DATE`**, so the assistant resolves "today"/"tomorrow"/
  "Saturday" correctly instead of guessing.
- **An `AVAILABILITY SUMMARY`** for the next 7 days: each day's opening
  hours (or `Closed`), and how many seats are already reserved that day
  out of `seating_capacity` — computed live from real, confirmed
  bookings, the same data `/admin/*/bookings` manages.

This is **read-only** — the assistant can now discuss availability
using real numbers, but still cannot create, change, or cancel a
booking through chat; it still directs customers to phone/email for
that, same as before. AI-assisted booking through `/chat` is Stage 2c.

---

### Conversation persistence (Stage 3 Step 5)

`/chat` now persists every conversation server-side instead of relying
entirely on the client to remember it. This is fully backward
compatible: the response body is still exactly `{"reply": "..."}`, and
sending a `conversation_token` is optional.

**Resuming a conversation:**
1. The first call for a new conversation gets back an
   `X-Conversation-Token` response header — an opaque, high-entropy
   value, not a small guessable number. It identifies no customer and
   grants no admin privilege; it's simply the handle that lets a later
   call resume this one conversation's history.
2. Send that value back as `conversation_token` in the request body on
   the next call to continue the same conversation — the server then
   loads its own persisted history (capped at 40 turns, same limit as
   before) instead of trusting whatever `history` the client sends.
3. An unknown token, or a token that belongs to a **different**
   `restaurant_id` than the one in the request, is treated exactly like
   no token at all — a new conversation silently starts. This is
   deliberate: the endpoint never confirms or denies that a given token
   belongs to someone else.
4. Omitting `conversation_token` entirely behaves exactly as it always
   has — the client's own `history` is trusted, nothing overrides it.

**What's stored:** each turn's `role` (`user`/`assistant` only — the
system prompt and restaurant context are rebuilt fresh from live data
on every call and never persisted, so a stored conversation never goes
stale relative to menu/hours/FAQ changes), `content` (same 2000-
character cap as today), and whether that assistant reply followed a
booking-tool call. No customer-identity columns exist on the
conversation record itself — only the messages, whatever the customer
actually typed.

**Admin access** (read-only, reuses the existing admin key + restaurant
scoping exactly like every other resource):
| Method & path | Purpose |
|---|---|
| `GET /admin/restaurant/{id}/conversations` | List conversations, most recent first (`limit`/`offset`, max 200) |
| `GET /admin/restaurant/{id}/conversations/{conversation_id}/messages` | That conversation's messages, oldest first |

These use the conversation's plain integer id, never the public token —
admins already have an authenticated key; there's no reason to expose
the anonymous-customer-facing handle to them.

Notes:
- Message content can contain whatever PII a customer typed (name,
  phone, email) during a booking conversation — the same trust boundary
  as the existing `bookings` table, not a new category of risk. Never
  logged; see `app/conversations.py` and the logging tests.
- No retention/deletion policy is enforced yet — `created_at`/
  `updated_at` are exactly what a future scheduled cleanup job would
  filter on, but no such job exists today. Treat persisted conversations
  as kept indefinitely until that's built.
- The current `frontend/index.html` doesn't yet capture/resend
  `X-Conversation-Token` — until it's updated, each of its calls starts
  its own single-exchange conversation server-side (harmless: Gemini
  still gets full context via the client's own `history`, exactly as
  before this stage).

### WhatsApp integration (Stage 3 Step 6B)

Customers can message a restaurant's WhatsApp Business number and get
the exact same AI assistant as web `/chat` — same restaurant data, same
booking capability (`app/booking_tool.py`, shared with web chat, not a
second implementation), same "never invent facts" system prompt. This is
opt-in per restaurant: nothing WhatsApp-related activates until an
operator maps a restaurant to a `phone_number_id` (see "Admin: mapping a
restaurant's WhatsApp number" below).

**v1 architecture, deliberately minimal:**
- **One WhatsApp number per restaurant.** `whatsapp_numbers` maps one
  Meta `phone_number_id` to one restaurant — enforced with a unique
  constraint at the database level, not just in application code.
- **No task queue.** Processing (Gemini, the booking tool, the outbound
  Send API call) runs in a FastAPI `BackgroundTasks` callback, after the
  webhook has already acknowledged Meta with HTTP 200 — no Redis,
  Celery, RQ, or other broker. If the process restarts mid-processing,
  that one in-flight message is simply lost; there is no persistent
  retry queue. Meta will not redeliver a message it already got a 200
  for, so this is a real, accepted v1 limitation, not a bug.
- **One platform-wide access token**, not a per-restaurant credential —
  see `WHATSAPP_ACCESS_TOKEN` below. `whatsapp_numbers` stores no
  secrets, only the public `phone_number_id`/`display_phone_number`.
- **Text messages only.** Media, buttons, location, interactive lists,
  and template messages are all out of scope for v1 — a non-text message
  is dropped (logged, never processed) rather than mishandled.

**Meta's 24-hour customer-service window:** a message from a phone
number the restaurant has already heard from resumes that conversation
if the last activity was within 24 hours (UTC); otherwise a new
conversation starts, mirroring Meta's own session rule
(`app/conversations.get_or_create_whatsapp_conversation`).

**Message idempotency:** Meta can and does redeliver webhook events.
Every inbound message's id is checked against every previously-persisted
message before any processing happens — a redelivered message is
detected and dropped before Gemini, before the booking tool, and before
a second outbound reply, never processed twice.

**Webhook security** (`GET`/`POST /webhooks/whatsapp`):
- `GET` is Meta's one-time verification handshake — validates
  `hub.verify_token` (constant-time comparison) and echoes back
  `hub.challenge`.
- Every `POST` must carry a valid `X-Hub-Signature-256` header — an
  HMAC-SHA256 of the **raw** request body, keyed with
  `WHATSAPP_APP_SECRET` — checked before any JSON parsing or database
  access. An invalid or missing signature is rejected outright.
- Restaurant identity is **never** taken from the payload itself — only
  from `phone_number_id`, looked up against `whatsapp_numbers`. An
  unrecognised `phone_number_id` drops the message; it never falls back
  to any restaurant.

**Admin: mapping a restaurant's WhatsApp number** (superadmin-only, same
authorization model as every other platform-admin endpoint):
| Method & path | Purpose |
|---|---|
| `POST /admin/platform/restaurants/{id}/whatsapp-number` | Create or replace this restaurant's mapping (upsert — there's only ever one) |
| `DELETE /admin/platform/restaurants/{id}/whatsapp-number` | Remove the mapping |

**Setting it up locally:** Meta needs a public HTTPS URL to send webhook
events to, which `http://127.0.0.1:8000` isn't. Use a tunnel such as
[ngrok](https://ngrok.com/) (`ngrok http 8000`) during local development
and testing, and point the Meta App Dashboard's webhook URL at the
tunnel's HTTPS URL plus `/webhooks/whatsapp`. This is a local-dev-only
requirement — a real deployment just needs its own real public HTTPS
URL, no tunnel involved.

**Required environment variables** (see `.env.example` — all optional;
WhatsApp simply stays inert, rejecting every webhook request, until
they're set):
- `WHATSAPP_VERIFY_TOKEN` — must match what you enter as the webhook's
  "Verify token" in the Meta App Dashboard.
- `WHATSAPP_APP_SECRET` — your Meta app's App Secret, used to verify
  every webhook POST's signature.
- `WHATSAPP_ACCESS_TOKEN` — used to call the Meta Graph "send message"
  API.
- `WHATSAPP_API_VERSION` — defaults to `v21.0` if not set.

**Known v1 limitations** (all deliberate, matching the points above):
no persistent retry queue, no per-restaurant WhatsApp credentials, no
template messages, no rich media/buttons/location/interactive lists, and
no automated retention/deletion job for WhatsApp conversations (same
as web chat — see "Conversation persistence" above).

---

## 4. Exact commands to test each part

### D. Test `/health`

In a **new** PowerShell window (leave the server running in the first one):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected output:
```
status
------
ok
```

(If you prefer curl-style output: `curl.exe http://127.0.0.1:8000/health`)

### E. Test `/chat`

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/chat `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message": "What are your opening hours?", "history": [], "restaurant_id": 1}'
```

Expected: a JSON object with a `reply` field containing a natural-language
answer built from the seeded opening hours.

You can also test interactively at **http://127.0.0.1:8000/docs** —
FastAPI's auto-generated test page — which is often easier than the
command line while you're getting familiar with the API.

### F. Verify the database was created

```powershell
Test-Path .\restaurant.db
```

Expected output: `True`. You can also check its size:
```powershell
Get-Item .\restaurant.db | Select-Object Name, Length
```

### G. Verify the seeded restaurant data exists

Easiest way — ask the chatbot itself via `/docs` or the `/chat` command
above with a question like `"What's on your menu?"` and confirm it lists
real items (Fish and Chips, Steak and Ale Pie, etc.).

To check directly against the database without going through the LLM:
```powershell
python -c "from app.database import SessionLocal; from app import models; db = SessionLocal(); r = db.query(models.Restaurant).first(); print(r.name if r else 'NOT FOUND'); print('Menu items:', db.query(models.MenuItem).count())"
```
Expected output:
```
The Kings Arms
Menu items: 11
```

---

## 5. Final Stage 1 test checklist

- [ ] `pip install -r requirements.txt` completes with no errors
- [ ] Running `uvicorn app.main:app --reload` with a valid key in `.env` starts cleanly with no manual environment variable commands
- [ ] Deleting/emptying `GEMINI_API_KEY` or `ADMIN_API_KEY` in `.env` and restarting the server produces a clear, readable error message and the server exits (does NOT start broken)
- [ ] `.env` is listed in `.gitignore` and is never referenced from `frontend/index.html`
- [ ] Calling any `/admin/*` endpoint with no `X-Admin-API-Key` header, or the wrong value, returns `401 Unauthorized`
- [ ] Calling an `/admin/*` endpoint with the correct `X-Admin-API-Key` header succeeds
- [ ] With `ADMIN_API_KEY_PREVIOUS` set, both the current `ADMIN_API_KEY` and the previous key succeed; an unrelated key and a missing key still return `401`
- [ ] Removing `ADMIN_API_KEY_PREVIOUS` and restarting makes the old key stop working (only the current key succeeds)
- [ ] Sending more than 10 `/chat` requests within a minute returns `429 Too Many Requests` on the 11th
- [ ] Sending more than 30 `/admin/*` requests within a minute (with or without a valid key) returns `429 Too Many Requests`
- [ ] Sending a `/chat` message over 2000 characters, or with more than 40 history entries, returns `422 Unprocessable Entity`
- [ ] A CORS preflight request from an origin not in `ALLOWED_ORIGINS` returns `400 Bad Request`
- [ ] A CORS preflight request from an origin in `ALLOWED_ORIGINS` (or `null`, for the file-opened frontend) succeeds with the matching `Access-Control-Allow-Origin` header
- [ ] `backend/logs/app.log` is created after the server starts, and its lines have a timestamp, level, and logger name
- [ ] A failed `/admin/*` auth attempt is logged (client + path) without the submitted key ever appearing in `app.log`
- [ ] Creating a booking within capacity and opening hours returns `201`; a second overlapping booking that would exceed `seating_capacity` returns `409`
- [ ] Creating a booking outside opening hours, or on a day the restaurant is closed, returns `409`
- [ ] Cancelling a booking (`PATCH` with `status: "cancelled"`) frees its capacity for a new overlapping booking
- [ ] `alembic upgrade head` creates `backend\restaurant.db` with all 5 tables (no manual `create_all()` step exists anymore)
- [ ] Running `alembic upgrade head` again (already at head) is a no-op — no error, no duplicate tables
- [ ] `docker compose up --build` starts a working app against PostgreSQL: migrations apply, `/health` returns `200`, and an authenticated admin request (e.g. `GET /admin/restaurant/1`) succeeds
- [ ] `Invoke-RestMethod http://127.0.0.1:8000/health` returns `{"status": "ok"}`
- [ ] `restaurant.db` appears in `backend\` after running `alembic upgrade head`
- [ ] The seeded restaurant ("The Kings Arms") and its menu/FAQs are queryable from the database
- [ ] Opening `frontend/index.html` shows the chat widget with a greeting
- [ ] Sending a real message (with your real API key in place) gets a natural UK-English reply within a few seconds
- [ ] The 10 "should work" questions (see below) get accurate answers matching `seed_data.py`
- [ ] The 10 "must refuse" questions get an honest "I don't have that information" instead of a guess
- [ ] Restarting the server does NOT duplicate the seeded restaurant data
- [ ] Viewing page source of `frontend/index.html` (Ctrl+U in browser) confirms no API key appears anywhere in it

---

## 6. Ten test questions that SHOULD work

1. "What are your opening hours on Saturday?"
2. "Do you have any vegetarian options?"
3. "How much is the fish and chips?"
4. "Where are you located?"
5. "Is there parking nearby?"
6. "Do you allow dogs?"
7. "Is there wheelchair access?"
8. "What desserts do you have?"
9. "Do you show football matches?"
10. "What's your phone number?"

## 7. Ten questions where the AI MUST refuse to guess

1. "Do you have a vegan burger?" (not on the menu — must not invent one)
2. "Can I book a table for 4 people tomorrow at 7pm?" (booking not built yet)
3. "What's your wifi password?" (not in the data)
4. "Do you have a private dining room?" (not in the data)
5. "Are you open on Christmas Day?" (not specified in opening hours)
6. "What's the calorie count of the steak?" (not in the data)
7. "Can I bring my own birthday cake?" (not in the data)
8. "Do you do delivery through Deliveroo?" (not in the data)
9. "Is the pie gluten-free?" (not tagged as such — must not assume)
10. "What's the corkage fee?" (not in the data)

For all of these, the assistant should say something like "I don't have
that information to hand — I'll flag it to the team" rather than guessing.

---

## 8. Known limitations of Stage 1 (by design)

- Multiple restaurants are fully supported (see "Multi-tenant admin
  access" and "Restaurant onboarding completeness" above) — onboarding,
  menu, FAQs, and opening hours are all API-driven, with no direct
  database access required. What's still missing is a browser-based
  admin *interface*; every admin/platform-admin operation today is an
  authenticated HTTP call (curl/PowerShell/Postman), not a UI.
- Table bookings exist and are both admin-managed (see "Table bookings"
  above) and reachable through AI-assisted booking via Gemini function
  calling (`app/booking_tool.py`), on both web `/chat` and, since Stage 3
  Step 6B, WhatsApp (see "WhatsApp integration" below) — the exact same
  booking implementation either way. No calendar sync, payments, SMS, or
  any channel beyond web chat and WhatsApp yet.
- Restaurant/menu/hours data is editable via the `/admin/*` API (see
  "Admin authentication" above), but there's still no admin *interface*
  — a future stage. Schema changes themselves now go through Alembic
  (see "Database migrations" below), not editing `seed_data.py`.
- Conversations are now persisted server-side (see "Conversation
  persistence" above), but the current `frontend/index.html` doesn't
  yet capture/resend the resumption token, so its own conversations
  still fragment into single-exchange records until it's updated — see
  that section's notes. No retention/deletion policy is enforced yet.
- CORS is restricted to `ALLOWED_ORIGINS` (see "CORS" below) rather than
  allowing all origins — but its default value still includes common
  localhost dev origins and `"null"` for ease of local testing, so set
  it to your actual domain(s) before going live.

---

## 9. Docker

A production Dockerfile is provided at the repository root (not inside
`backend/`) so it can `COPY` just `backend/requirements.txt` and
`backend/app` — no tests, dev-only dependencies, or local `.env` file
ever end up in the image.

**Build** (from the repository root):
```bash
docker build -t restaurant-ai-agent .
```

**Run** — all configuration is supplied as real environment variables
at container-start time, never baked into the image:
```bash
docker run -p 8000:8000 \
  -e GEMINI_API_KEY=your-real-key \
  -e ADMIN_API_KEY=your-real-admin-secret \
  restaurant-ai-agent
```
The app is then reachable at `http://localhost:8000` (try `/health` or
`/docs`). Any other setting from `backend/.env.example`
(`ALLOWED_ORIGINS`, `DATABASE_URL`, `LOG_DIR`, `ADMIN_API_KEY_PREVIOUS`,
...) can be passed the same way with additional `-e` flags or
`--env-file path/to/your.env` (a real one, never committed).

Notes:
- The image runs as a non-root user and defines a `HEALTHCHECK` that
  polls `/health` (pure Python — no `curl`/`wget` needed in the slim
  base image).
- Without a mounted volume or a `DATABASE_URL` pointing elsewhere, the
  SQLite database and log file created inside the container
  (`/app/restaurant.db`, `/app/logs/app.log`) are lost when the
  container is removed. For a persistent database, point `DATABASE_URL`
  at PostgreSQL instead — see "PostgreSQL" below.
- This does not replace the local development workflow above (a venv
  is still the fastest local dev loop); it's the way this app runs
  anywhere Docker is available, including in CI (see below).

---

## 10. Continuous Integration (CI)

Every push and pull request runs `.github/workflows/ci.yml` on GitHub
Actions, with two jobs:

- **`test`** — installs `backend/requirements.txt` and
  `backend/requirements-dev.txt` on Python 3.11, then runs the full
  `pytest` suite from `backend/`. No secrets are configured or needed:
  `tests/conftest.py` already supplies safe fake values for
  `GEMINI_API_KEY`/`ADMIN_API_KEY` before the app is imported, and every
  test that touches "Gemini" stubs the client — nothing in the suite
  makes a real network call.
- **`docker`** — builds the image from this repository's `Dockerfile`,
  starts a container with fake (non-secret) key values, and polls
  `/health` until it responds, catching any Dockerfile or startup
  regression on every change. Since the image runs `alembic upgrade
  head` before starting the server, this also catches a broken
  migration — the container fails to become healthy if one fails.

Both must pass before merging.

---

## 11. Database migrations (Alembic)

Schema is managed entirely by [Alembic](https://alembic.sqlalchemy.org/)
— nothing creates or alters tables automatically at app startup. This
applies to every environment (SQLite or PostgreSQL, local venv, Docker,
or deployed) so there is exactly one way schemas ever get created.

**Required commands** (run from `backend/`, with your `.env` in place):
```powershell
# Apply all pending migrations — required once after first setup, and
# again after pulling any change that adds a new migration.
alembic upgrade head

# After changing backend/app/models.py, generate a new migration...
alembic revision --autogenerate -m "describe the change"
# ...then ALWAYS hand-review the generated file in
# backend/alembic/versions/ before running upgrade again. Autogenerate
# is a strong starting point, not a guarantee — it can miss things.

# Revert the most recent migration if something's wrong:
alembic downgrade -1
```

**If you have an existing `backend/restaurant.db` from before Alembic
was introduced** (it has all the right tables already, Alembic just
doesn't know that yet), running `alembic upgrade head` blind will fail
with a "table already exists" error. Two options:
1. **Recommended for local dev** — delete `backend/restaurant.db` and
   run `alembic upgrade head` to recreate it from scratch. There's no
   real data in a local dev database worth protecting; this is the same
   "just delete it and restart" guidance this project has always given
   for schema changes.
2. **If you've entered real data via the admin API and want to keep
   it** — run `alembic stamp head` once instead. This marks the
   database as already being at the current schema *without* running
   any migration SQL, adopting Alembic going forward without touching
   existing tables or rows.

**Why this doesn't run automatically at app startup:** it did, briefly,
via `models.Base.metadata.create_all()` — but that only ever *creates
missing tables*, silently, with no history and no way to alter an
existing column safely. Mixing that with a real migration tool is a
well-known way to get schema drift that's hard to debug. Alembic is now
the only mechanism, in every environment.

---

## 12. PostgreSQL

SQLite remains the zero-setup default (see §§1–3) — nothing about it
changed. PostgreSQL is a fully-supported alternative for anyone who
wants production parity locally, via the same `DATABASE_URL` config
already documented in `.env.example`.

### Quick start with Docker Compose

```bash
cp backend/.env.example backend/.env   # then fill in real API keys
docker compose up --build
```

This starts a `postgres:16-alpine` container plus the app, wired
together — the app's `alembic upgrade head` runs automatically as part
of its container startup (see the Dockerfile), so the database is ready
by the time the app answers requests. Reachable at
`http://localhost:8000`. Data persists in the `pgdata` named volume;
`docker compose down -v` removes it for a clean slate.

### Without Docker Compose

Point `DATABASE_URL` at any reachable PostgreSQL instance and run the
same migration step:
```
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
```
```powershell
alembic upgrade head
uvicorn app.main:app --reload
```

### Notes

- The driver is `psycopg2` (via `psycopg2-binary`), not `psycopg` v3 —
  deliberately, so a bare `postgresql://...` connection string from a
  managed provider (Render, Railway, RDS, Supabase, ...) works without
  rewriting it to `postgresql+psycopg://...`. This app has no use for
  psycopg3's async support, since every route is synchronous.
- Managed providers often require SSL — check if you need to append
  `?sslmode=require` to their connection string.
- `database.py` applies `pool_pre_ping=True` automatically whenever
  `DATABASE_URL` isn't SQLite — recovers cleanly from a managed
  provider silently closing an idle connection.
- **Known limitation carried forward unchanged:** booking availability
  is still a check-then-insert (see "Table bookings" above), not yet a
  database-enforced guarantee. PostgreSQL supports `EXCLUDE` constraints
  (via the `btree_gist` extension) that could make this atomic — a
  worthwhile follow-up, deliberately not bundled into this change.

---

## 13. PostgreSQL Disaster Recovery

A practical runbook for backing up and recovering the production
database, built around the existing `railway_pg_backup_restore_test.ps1`
script at the repo root (never modify that file — see "Important safety
rules" below).

### Current production database

- Production runs on **Railway PostgreSQL**.
- **The current Railway plan has no managed automated backups or
  point-in-time recovery (PITR).** Nothing about this can be turned on
  from this repository — it's a Railway plan/service setting. Until
  it's confirmed otherwise, treat backups as **entirely manual and
  operator-driven** (see "Current gaps").
- `DATABASE_URL` (the production connection string) is stored as a
  Railway environment variable/secret. It must **never** be printed,
  logged, committed, or pasted into a chat/AI session — treat it exactly
  like `ADMIN_API_KEY`/`GEMINI_API_KEY` (see "Admin authentication"
  above).

### Manual backup procedure

**Preconditions:**
- Docker Desktop installed and running, on your **own machine** — not
  a cloud sandbox or CI runner (the script's own header says so
  explicitly; it also isn't meant to run inside this project's CI).
- Railway dashboard access, to copy the Postgres service's **external/
  public** connection string (Connect tab) — not the internal
  `*.railway.internal` one, which isn't reachable from outside Railway's
  network.

**Required environment variable** (set in the same PowerShell session
you'll run the script from, never written to a file):
```powershell
$env:RAILWAY_PROD_DATABASE_URL = "postgresql://user:pass@host:port/dbname"
```

**Running the backup:**
```powershell
.\railway_pg_backup_restore_test.ps1
```
This performs a **read-only** `pg_dump` against production, restores
the dump into a disposable local Docker container, and prints
side-by-side row counts so you can confirm the backup is complete and
correct (see "Backup verification" below). It does not modify
production in any way.

**Where backup artifacts are stored:** on your own machine, under
`%USERPROFILE%\pg_backups\restaurant-ai-agent\` — outside this
repository entirely, so they can never end up staged or committed by
accident. Each run creates a timestamped `railway_backup_<timestamp>.dump`
plus a matching `.objects.txt` listing.

**How to verify the backup completed:** the script prints the backup
file path and size ("Backup created: ... bytes") once `pg_dump` finishes
successfully, and fails loudly (`throw`) if `pg_dump` errors or the
expected file doesn't appear — there is no silent partial-success case.

### Backup verification

- The script restores the dump into a **separate, disposable, local**
  Docker container (`raia_scratch_restore_db`), never into production,
  and then prints matching row-count/foreign-key tables for production
  vs. the restored copy so you can compare them by eye.
- Production is queried read-only for this comparison, wrapped in
  `BEGIN; SET TRANSACTION READ ONLY; ...; ROLLBACK;` — even a mistake in
  the query can't write to production.
- Confirm the restore is correct by comparing the two printed tables:
  per-table row counts, the `alembic_version` value, and the
  foreign-key counts should all match exactly.
- Once satisfied, remove only the scratch container with:
  ```powershell
  .\railway_pg_backup_restore_test.ps1 -Cleanup
  ```
  This never touches production and never deletes the backup file
  itself — remove that manually if/when you no longer need it.

### Disaster recovery procedure

If the production database is lost, corrupted, or otherwise
unrecoverable in place:

1. **Get the most recent good backup.** Either use one already produced
   by the manual procedure above, or run it now against whatever is
   still reachable (if production is only partially degraded).
2. **Provision a replacement PostgreSQL database** (a new Railway
   Postgres service, or another instance) — do not attempt to restore
   into the broken instance if it can be avoided.
3. **Restore the backup into the replacement database**, not the
   scratch container this time. Set the replacement connection string
   as an environment variable first — **never paste a real
   `DATABASE_URL` containing credentials into shared documentation, a
   chat/AI session, or anywhere it would land in shell history you
   don't control**; prefer setting it directly in your own local
   session rather than typing it inline in a command:
   ```powershell
   $env:REPLACEMENT_DATABASE_URL = "<set securely in your local PowerShell session>"

   docker run --rm -v "$env:USERPROFILE\pg_backups\restaurant-ai-agent:/backup" `
     postgres:16-alpine `
     pg_restore --no-owner --no-privileges --verbose `
       --dbname="$env:REPLACEMENT_DATABASE_URL" `
       "/backup/railway_backup_<timestamp>.dump"
   ```
4. **Run Alembic migrations carefully** against the replacement database
   — do not assume the backup already reflects `alembic upgrade head`.
   Reuse the same environment variable rather than retyping the
   connection string:
   ```powershell
   $env:DATABASE_URL = $env:REPLACEMENT_DATABASE_URL
   alembic current   # check what the restored DB thinks its version is
   alembic upgrade head
   ```
5. **Update the production `DATABASE_URL`** in Railway's dashboard to
   point at the replacement database — set it directly in Railway's own
   environment-variable UI, never by pasting it into a document, ticket,
   or chat message first.
6. **Redeploy/restart the backend** on Railway so it picks up the new
   `DATABASE_URL`.
7. **Verify `/health`** responds `{"status": "ok"}` (this now includes a
   real database connectivity check — see app/main.py).
8. **Verify admin access** against the replacement database. Set the
   real admin key in your own local session first — do not write it
   directly into the command, and never commit it, paste it into this
   README, or let it appear in logs/chat:
   ```powershell
   $env:ADMIN_API_KEY = "<set securely in your local PowerShell session>"

   Invoke-RestMethod https://<production-url>/admin/restaurant/1 `
     -Headers @{ "X-Admin-API-Key" = $env:ADMIN_API_KEY }
   ```
9. **Verify chat** — send a real message through `/chat` or a
   restaurant's widget and confirm a real, data-grounded reply comes
   back (not an error).
10. **Verify booking** — create one real test booking through the admin
    API or chat and confirm it's created and appears back in the admin
    bookings list.
11. **Verify the booking confirmation email** — confirm the test
    booking's confirmation email actually arrives (only meaningful if
    `RESEND_API_KEY`/`EMAIL_FROM` are configured; if they aren't, the
    booking should still succeed without one — see "Table bookings"
    above).

### Important safety rules

- **Never restore directly over production** without an explicit,
  deliberate recovery decision — every restore in the routine backup
  procedure targets a disposable scratch database, never production.
- **Never expose `DATABASE_URL`** — don't print it, log it, paste it
  into a chat/AI session, a document, or a ticket, or commit it
  anywhere. Prefer setting it directly in an environment/session (your
  own shell, or Railway's own environment-variable UI) over typing it
  inline in a command.
- **Never commit `.dump`/`.sql` backup files** — they belong on the
  operator's own machine, outside this repository, never staged.
- **Never modify `railway_pg_backup_restore_test.ps1`** as part of a
  recovery — if it needs a genuine change, that's a separate, deliberate
  task with its own review, not something to edit under incident
  pressure.

### Recovery checklist

- [ ] A recent backup is available and its file size/objects list look reasonable
- [ ] The backup has been restore-verified (row counts/foreign-key counts match production)
- [ ] The replacement PostgreSQL database is provisioned and reachable
- [ ] The backup has been restored into the replacement database
- [ ] `alembic upgrade head` has been run against the replacement database
- [ ] Railway's `DATABASE_URL` has been updated to the replacement database
- [ ] The backend has been redeployed/restarted
- [ ] `/health` returns `{"status": "ok"}`
- [ ] Admin access works against the replacement database
- [ ] Chat returns a real, data-grounded reply
- [ ] A test booking can be created and appears in the admin bookings list
- [ ] The booking confirmation email arrives (if configured)

### Current gaps

- **No Railway-managed automated backups or PITR on the current plan.**
  This is a Railway plan/service setting, not something this repository
  configures — confirm directly in the Railway dashboard if this ever
  changes.
- **No automatic external backup scheduler yet.** The only backup
  mechanism today is the manual script above, run on demand by an
  operator — nothing runs it on a schedule.
- **Recovery is currently entirely operator-driven** — there is no
  automated failover or restore trigger; every step above is a manual
  decision and action by whoever is responding to the incident.
