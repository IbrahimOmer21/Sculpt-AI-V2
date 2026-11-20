# Sculpt AI V2

Turn a sentence (or your own PNG/JPG) into a 3D sculpture you can view in the browser and download as GLB or STL.
Built with Flask, Flask-Login, Flask-SQLAlchemy (SQLite) and the Meshy API. Front end is plain HTML/CSS/JS.

## Setup (Windows)

```
py -3 -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`:

- `SECRET_KEY`: any long random string (`python -c "import secrets; print(secrets.token_hex(32))"`)
- `MESHY_API_KEY`: your Meshy key
- `USE_MOCK_AI=1` (optional, development only): fake images and a pyramid model instead of Meshy, so no credits are spent

## Run

```
venv\Scripts\python run.py
```

Open http://localhost:5000 and create an account. Debug mode is off unless you set `FLASK_DEBUG=1`.

Local test account (exists only in this machine's `instance/sculpt.db`, made with mock AI data):
email `demo@example.com`, password `demo-pass-123`. Delete `instance/sculpt.db` and `storage/` to start fresh.

## Tests

```
venv\Scripts\python -m unittest
```

Tests use an in-memory database, a temporary upload folder and a fake Meshy, so they never touch real data or credits.

## Layout

```
app/
  __init__.py          create_app: config, database, Flask-Login, JSON error handlers, CSRF guard
  config.py            settings read from .env
  models/              User, Generation, Iteration
  routes/
    auth_routes.py     /signup /login /logout
    generation_routes.py  generations, sculpture, upload, /files (owner-checked), recent prompts
    iteration_routes.py   /api/generation/<id>/iterate
  services/
    meshy_service.py   Meshy calls with timeouts and polling
    mock_service.py    offline stand-in (USE_MOCK_AI=1)
  templates/           base, auth (login + signup), index (the app)
  static/              style.css, main.js
storage/               generated and uploaded files (created on first run, not public)
instance/sculpt.db     SQLite database (created on first run)
```

## Rules the server enforces

- Login required everywhere; other users' generations and files return 404.
- 3 images per generation, 1 sculpture per generation (403 after that).
- 3 sculptures per user per rolling 24 hours (429); failed attempts don't count.
- A sculpture is marked "in progress" before Meshy is called, so a double click can't start two jobs (409).
- Uploads: PNG/JPG only (checked by extension and file signature), max 10 MB (413), only on an empty generation (409), saved under a random name.
- Blank prompts are rejected (400) before Meshy is called.
- All API errors are JSON: `{"error": "..."}`.
