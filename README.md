# AngazaCare

AngazaCare is a mental health monitoring web application built with Flask, SQLite, and Chart.js.

## Setup

Install dependencies:

```bash
pip install flask flask-login flask-sqlalchemy bcrypt
```

Run the app:

```bash
python app.py
```

Run with Docker:

```bash
docker build -t angazacare .
docker run --rm -p 5000:5000 --env-file .env angazacare
```

Or use Docker Compose:

```bash
docker compose up --build
```

Visit http://localhost:5000

Login with:

- Email: `demo@angazacare.com`
- Password: `password123`

## Password reset

No email provider is configured. In local development, password reset requests
show a generic confirmation and the one-time reset URL for an existing account
is written to the application log. Links expire after 30 minutes and can only
be used once. Configure email delivery before using password resets in a
production deployment; reset URLs are not logged outside development.

## Admin activity dashboard

Sign in through the normal login page. Accounts with the `admin` role can view
system-wide patient activity at `/admin`; psychiatrist accounts remain limited
to their active assigned patients. Grant the `admin` role only through a trusted
database console, never through public registration. For SQLite, for example:

```sql
UPDATE user SET role = 'admin' WHERE email = 'trusted-admin@example.com';
```

Chat prompts and replies are stored in `chat_interaction`. Authenticated records
use the user ID; anonymous records use a SHA-256 hash of a random session token.
The dashboard withholds prompt text, assessment scores, answers, and AI summaries
unless `consent_to_clinician_review` is enabled. Activity records contain
sensitive health information and should be protected accordingly in database
access, backups, and retention policies.

## Separate admin website

The standalone frontend is in `admin-portal/` and fetches authenticated JSON
from the Flask application. For local development, run the Flask backend in one
terminal with `python app.py`, then serve the portal from another terminal:

```powershell
python -m http.server 5173 --bind 127.0.0.1 --directory admin-portal
```

Open http://127.0.0.1:5173. The portal's API URL is set in the
`admin-api-base` meta tag in `admin-portal/index.html`. For deployment, point it
to the Flask API and set `ADMIN_PORTAL_ORIGINS` to the portal's exact HTTPS
origin. Host the portal and API on the same site so the secure session cookie
can be sent with credentialed requests.
