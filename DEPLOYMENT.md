# Deployment Guide for AngazaCare

## Quick Deployment to Render.com (Recommended)

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

