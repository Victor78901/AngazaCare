# Deployment Guide for AngazaCare

## Deploy to Vercel

Vercel detects the Flask application in the root `app.py`. Connect the GitHub repository to Vercel and deploy from the project root; no custom build command is required. Static assets are served from `public/`.

Set these environment variables in both Preview and Production:
- `SECRET_KEY`: a stable random secret, unique per environment
- `DATABASE_URL`: a persistent PostgreSQL connection string
- `SESSION_COOKIE_SECURE`: `true`
- `RATELIMIT_STORAGE_URI`: optional shared Redis URI; without it, rate limits are per function instance
- `GEMINI_API_KEY`: optional, required for Gemini-backed features

Vercel sets `VERCEL` automatically. If `DATABASE_URL` is omitted, the app uses SQLite under `/tmp`; that storage is temporary and must not be used for persistent user data. Initialize the PostgreSQL schema before sending traffic; database creation is intentionally not run during serverless imports.

For local Vercel-compatible development, install dependencies and run `vercel dev` from the repository root.

## Deploy to Render.com

### Step 1: Prepare Your Repository
```bash
# Initialize git if not already done
git init
git add .
git commit -m "Initial commit - AngazaCare Mental Health App"

# Push to GitHub
git remote add origin https://github.com/YOUR_USERNAME/mindwell.git
git branch -M main
git push -u origin main
```

### Step 2: Deploy on Render
1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repository
4. Fill in the form:
   - **Name**: `angazacare` (or your preferred name)
   - **Environment**: `Docker`
   - **Branch**: `main`
   - **Dockerfile path**: `./Dockerfile`

5. Add Environment Variables in the "Environment" tab:
   - `GEMINI_API_KEY`: Your Gemini API key (from .env)
   - `USE_FALLBACK_ONLY`: `false`
   - `APP_ENV`: `production`
   - `SECRET_KEY`: A unique, stable random secret; generate one with `python -c "import secrets; print(secrets.token_hex(32))"`
   - `SESSION_COOKIE_SECURE`: `true`
   - `RATELIMIT_STORAGE_URI`: A private Redis connection URL shared by all app instances

   Configure a shared rate-limit store for multi-instance production deployments; otherwise, rate limits are stored in process memory.

6. Click **"Create Web Service"**

### Step 3: Access Your App
Once deployment completes (2-5 minutes), you'll get a URL like:
```
https://angazacare-xxxx.onrender.com
```

---

## Alternative: Deploy with Docker Locally

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop) installed

### Build and Run
```bash
cd c:\Users\merit\mindwell
docker-compose up --build
```

### Access
```
http://localhost:5000
```

---

## Features Ready to Deploy
✅ AI Chatbot (with Gemini)
✅ PHQ-9 Mental Health Assessment
✅ Mood Tracker
✅ Admin Dashboard with Chat History
✅ Emergency Contacts
✅ Breathing Exercises
✅ Database with SQLite (persistent storage on Render)

---

## Support
- **Database**: Uses SQLite with Docker volume persistence
- **Port**: 5000
- **Production Server**: Gunicorn with 4 workers
- **Health Check**: Available at `/`

