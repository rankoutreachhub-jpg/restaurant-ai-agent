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

The database file (`backend\restaurant.db`) and example restaurant data
are created automatically the first time you run this.

**Start the frontend:** open `frontend\index.html` by double-clicking it
(it opens in your default browser). It talks to the backend at
`http://127.0.0.1:8000/chat` — the backend must be running first. No API
key is ever present in this file — all Gemini API calls happen on the
backend only.

---

### Admin authentication

Every `/admin/*` endpoint (restaurant details, menu, opening hours, and
any booking-management endpoints added later) requires a valid
`X-Admin-API-Key` header matching the `ADMIN_API_KEY` value in `.env`.
Requests without it, or with the wrong value, get a `401 Unauthorized`
response — the `/chat` and `/health` endpoints are unaffected and need
no key.

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
- [ ] `Invoke-RestMethod http://127.0.0.1:8000/health` returns `{"status": "ok"}`
- [ ] `restaurant.db` appears in `backend\` after first run
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

- Only one restaurant exists (ID 1) — multi-tenant support is designed
  into the database structure (every table has `restaurant_id`) but not
  yet exposed via an admin interface.
- No booking, no calendar, no payments, no WhatsApp/SMS — future stages.
- Editing restaurant data requires editing `seed_data.py` directly and
  restarting with a fresh database (delete `restaurant.db` and restart).
  An admin editing interface is a future stage.
- Conversation history is only kept in the browser tab (frontend
  JavaScript variable) — refreshing the page clears it. No conversations
  are persisted to the database yet.
- CORS is restricted to `ALLOWED_ORIGINS` (see "CORS" below) rather than
  allowing all origins — but its default value still includes common
  localhost dev origins and `"null"` for ease of local testing, so set
  it to your actual domain(s) before going live.
