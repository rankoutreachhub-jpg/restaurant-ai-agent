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
- CORS currently allows all origins (`*`) for ease of local testing —
  this should be restricted to your actual domain before going live.
