# Monad

**Habits, tasks, study, community, and memories, all in one place.**

Monad is a personal productivity web app built with Flask. It combines a daily planner, monthly habit tracker, focus timer, journal, photo memories, study tools for students, and small accountability communities into a single installable Progressive Web App (PWA). AI features powered by Google Gemini help with onboarding, scheduling, and analytics.

<p align="center">
  <img src="website/static/monad_logo.png" alt="Monad logo" width="120">
</p>

---

## Features

### Planning
- **Daily view**: tasks with priority, due/end time, categories, quick notes, reminders, and recurring days.
- **Weekly view**: a week at a glance with offset navigation.
- **Time map**: block out your day in minutes, link blocks to tasks, and let AI generate a schedule from your goals and routine.

### Habits
- Monthly habit grid with flexible frequencies (daily, N times a week, or specific weekdays).
- Per-day reflections, streak tracking, and a monthly streaks page.
- Habits can be the target of a focus session, so focused time accrues to the habit.

### Focus timer
- Timer and stopwatch modes with ambient sounds (rain, forest, café, lo-fi).
- Runs in the background across page navigation from a shared `localStorage` session store.
- Floating timer pill on every page, draggable and remembered between visits.
- Picture-in-Picture floating timer over other apps (Document PiP on desktop, video PiP on Android).
- Local notification when a session completes.

### Journal & memories
- Daily journal with mood, accomplishments, improvements, learnings, and gratitude.
- Photo memories by day and month, with captions and a "final photo" of the day. Files are stored in Supabase Storage.

### Study (optional, can be disabled in settings)
- Subjects, class timetable with room and reminder notes, exams and assignments with weightage.
- Study notes with file attachments (PDF, image, docx).
- Exam results with per-subject marks and AI-generated study tips.
- Student profile with institution details and photo.

### Community & Connect
- Create or join communities with an invite code, share habits, and set a group goal.
- Group and direct messaging with file sharing.
- One-to-one and group calls using WebRTC with HTTP polling for signalling.

### Insights
- Achievements page with daily stars and crowns.
- Analytics page with an AI-written report on peak hours, time leaks, and recommendations.

### Notifications
- Web Push (VAPID) subscriptions per user.
- Cron-triggered reminder endpoints for habits, morning check-ins, classes, exams, and tasks.

### Personalisation
- Six themes (default, ocean, forest, sunset, dark, haikyuu), a virtual pet, motto, year goal, and 12h/24h time format.
- AI-assisted onboarding suggests starter habits, tasks, and a motto from your goals and routine.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3, Flask 3, Flask-Login, Flask-WTF (CSRF), Flask-Limiter |
| Database | PostgreSQL via Flask-SQLAlchemy (Supabase hosted) |
| File storage | Supabase Storage (`uploads` bucket) |
| Auth | Email + password with OTP verification, Google OAuth via Authlib |
| Email | Brevo transactional API (OTP), Flask-Mail fallback config |
| AI | Google Gemini 2.5 Flash via REST |
| Push | pywebpush + VAPID |
| Frontend | Jinja templates, vanilla JavaScript, service worker, PWA manifest |
| Server | Gunicorn (see `Procfile`) |

---

## Project layout

```
Monad/
├── start.py                  # App entry point (loads .env, creates the Flask app)
├── Procfile                  # Gunicorn command for hosting platforms
├── requirements.txt
├── website/
│   ├── __init__.py           # create_app(): config, extensions, blueprints, light schema migrations
│   ├── auth.py               # Login, sign-up, OTP verification, password reset, Google OAuth
│   ├── views.py              # All app routes: planner, habits, focus, study, community, push, AI
│   ├── models.py             # SQLAlchemy models
│   ├── storage.py            # Supabase Storage upload / public URL helpers
│   ├── templates/            # Jinja pages
│   └── static/
│       ├── focus_timer_core.js   # Pure time arithmetic for the focus timer
│       ├── focus_timer_store.js  # localStorage-backed session store
│       ├── focus_timer.js        # Floating pill, PiP, sounds, notifications
│       ├── sw.js                 # Service worker
│       ├── manifest.json         # PWA manifest
│       ├── sounds/ focus_images/ icons/ splash/
├── tests/                    # Node test files for the focus timer modules
└── docs/superpowers/         # Design specs and implementation plans
```

---

## Running locally

### 1. Prerequisites
- Python 3.11 or newer
- A PostgreSQL database (a free Supabase project works)
- A Supabase Storage bucket named `uploads` (public)
- Node.js 18 or newer (only for running the JavaScript tests)

### 2. Install

```bash
git clone https://github.com/jsharonsweety28-ai/Monad.git
cd Monad
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

### 3. Configure

Create a `.env` file in the project root. `SECRET_KEY` and `DATABASE_URL` are required at startup. The rest enable specific features.

```env
# Required
SECRET_KEY=change-me
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Supabase Storage (photos, notes, chat attachments)
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your-service-or-anon-key

# Email OTP (sign-up verification, password reset)
BREVO_API_KEY=
BREVO_SENDER_EMAIL=
MAIL_USERNAME=
MAIL_PASSWORD=

# Google sign-in
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# AI features (onboarding, schedule generation, analytics report, study tips)
GEMINI_API_KEY=

# Web Push
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=

# Shared secret for the /api/push/* reminder endpoints
CRON_SECRET=

# Optional
FLASK_DEBUG=true
```

Tables are created automatically on first run, and a few additive column migrations are applied on every start.

### 4. Run

```bash
python start.py
```

The app is served at `http://localhost:5000`. With `FLASK_DEBUG=true` the session cookie is not marked `Secure`, so you can also test over plain http from `127.0.0.1` or a phone on the same network. Leave `FLASK_DEBUG` unset in production to keep the cookie Secure-only.

---

## Tests

The focus timer's time arithmetic and session store are covered by Node's built-in test runner:

```bash
node --test tests/focus_timer_core.test.js tests/focus_timer_store.test.js
```

---

## Deployment

The `Procfile` runs Gunicorn with one worker and four threads, suitable for Render, Railway, Heroku-style platforms:

```
web: gunicorn start:app --timeout 120 --workers 1 --threads 4
```

Set the same environment variables from the `.env` section in your host's dashboard.

### Scheduled reminders

Push reminders are sent when an external scheduler calls these endpoints with the `CRON_SECRET`:

| Endpoint | Purpose |
|---|---|
| `/api/push/morning-reminder` | Daily morning check-in |
| `/api/push/habit-reminder` | Habits not yet ticked |
| `/api/push/task-reminder` | Tasks due soon |
| `/api/push/class-reminder` | Upcoming class slots |
| `/api/push/exam-reminder` | Upcoming exams |

Point a cron service (for example cron-job.org or a platform cron) at each URL on the cadence you want.

---

## Design docs

Feature designs and implementation plans live in [`docs/superpowers/`](docs/superpowers/), including the background focus timer, floating PiP timer, cross-page focus sound, habit editing, and completion notifications.

---

## Contributing

Issues and pull requests are welcome. Keep changes small and focused, and describe the user-facing behaviour in the PR.

## License

No license has been chosen yet. All rights reserved by the author until one is added.
