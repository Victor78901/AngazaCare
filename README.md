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
