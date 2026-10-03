import hashlib
import json
import math
import os
import random
import re
import secrets
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
try:
    import google.generativeai as genai
except Exception:
    genai = None
from dotenv import load_dotenv

MOOD_CHECKIN_TITLE = "Right-now check-in"
MOOD_CHECKIN_DESCRIPTION = "Choose what best describes how you feel right now. This check-in is not a diagnosis."
MOOD_CHECKIN_SUBMIT = "Complete check-in"
MOOD_CHECKIN_SUBMITTED = "Thanks for checking in. Consider one small thing that could support you right now."
MOOD_CHECKIN_INCOMPLETE = "Please choose an answer for each question."
MOOD_CHECKIN_QUESTIONS = [
    "😊 How are you feeling right now?",
    "🌱 How hopeful do you feel right now?",
    "🫧 How calm do you feel right now?",
    "🤝 How connected do you feel right now?",
    "🔋 How is your energy right now?",
]
MOOD_CHECKIN_OPTIONS = [
    ("very-low", "😢 Very low"),
    ("low", "🙁 A little low"),
    ("okay", "😐 Okay"),
    ("good", "🙂 Pretty good"),
    ("very-good", "😊 Very good"),
]
from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, session
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
import bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import generate_csrf, validate_csrf
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms.validators import ValidationError

from models import (
    Assessment,
    MoodEntry,
    Recommendation,
    User,
    db,
    BreathingSession,
    Assignment,
    ClinicianViewLog,
    PasswordResetToken,
    ChatInteraction,
)

load_dotenv()

# Set to True to use fallback responses only (when API quota is exhausted)
USE_FALLBACK_ONLY = os.getenv("USE_FALLBACK_ONLY", "false").lower() == "true"

if genai is not None and not USE_FALLBACK_ONLY:
    try:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))
    except Exception:
        genai = None

IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
if IS_VERCEL:
    app = Flask(__name__, instance_path="/tmp/instance", static_folder=None)
    try:
        os.makedirs("/tmp/instance", exist_ok=True)
    except OSError as error:
        app.logger.warning("Could not create Vercel instance directory: %s", error)
else:
    app = Flask(__name__, static_folder="public/static", static_url_path="/static")

app_env = os.getenv("APP_ENV", "production" if IS_VERCEL else "development").lower()
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    secret_key = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY is not set; using a temporary key. Configure SECRET_KEY "
        "to keep sessions valid across serverless invocations."
    )
session_cookie_secure_default = "true" if app_env == "production" or IS_VERCEL else "false"
session_cookie_secure = os.getenv("SESSION_COOKIE_SECURE", session_cookie_secure_default).lower() == "true"
if app_env == "production" and not session_cookie_secure:
    app.logger.warning("SESSION_COOKIE_SECURE=false is ignored in production.")
    session_cookie_secure = True
app.config["SECRET_KEY"] = secret_key
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = session_cookie_secure
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
database_url = os.getenv("DATABASE_URL")
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
else:
    if IS_VERCEL:
        database_url = "sqlite:////tmp/mindwell.db"
        app.logger.warning(
            "DATABASE_URL is not set on Vercel; using temporary SQLite storage "
            "at /tmp/mindwell.db. Data will not persist."
        )
    else:
        database_url = "sqlite:///angazacare.db"
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
admin_portal_origins_default = (
    "http://127.0.0.1:5173,http://localhost:5173"
    if app_env != "production" and not IS_VERCEL
    else ""
)
ADMIN_PORTAL_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("ADMIN_PORTAL_ORIGINS", admin_portal_origins_default).split(",")
    if origin.strip()
}

db.init_app(app)
rate_limit_storage_uri = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=rate_limit_storage_uri,
)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"


@app.after_request
def add_admin_portal_cors(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if request.path.startswith("/api/admin/") and origin in ADMIN_PORTAL_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers.add("Vary", "Origin")
    return response

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

PHQ9_QUESTIONS = [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed? Or the opposite — being so fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead, or of hurting yourself in some way",
    "How difficult have these problems made it for you to do your work, take care of things at home, or get along with other people?"
]

MOOD_CHECKIN_COPY = {
    "en": {
        "title": "Right-now check-in",
        "description": "Choose what best describes how you feel right now. This check-in is not a diagnosis.",
        "submit": "Complete check-in",
        "submitted": "Thanks for checking in. Consider one small thing that could support you right now.",
        "incomplete": "Please choose an answer for each question.",
        "questions": [
            "😊 How are you feeling right now?",
            "🌱 How hopeful do you feel right now?",
            "🫧 How calm do you feel right now?",
            "🤝 How connected do you feel right now?",
            "🔋 How is your energy right now?",
        ],
        "options": [
            ("very-low", "😢 Very low"),
            ("low", "🙁 A little low"),
            ("okay", "😐 Okay"),
            ("good", "🙂 Pretty good"),
            ("very-good", "😊 Very good"),
        ],
    },
    "sw": {
        "title": "Ukaguzi wa hali yako sasa hivi",
        "description": "Chagua jibu linaloeleza vizuri zaidi jinsi unavyohisi sasa hivi. Huu si utambuzi wa ugonjwa.",
        "submit": "Kamilisha ukaguzi",
        "submitted": "Asante kwa kushiriki jinsi unavyohisi. Fikiria jambo moja dogo linaloweza kukusaidia sasa hivi.",
        "incomplete": "Tafadhali chagua jibu kwa kila swali.",
        "questions": [
            "😊 Unajisikiaje sasa hivi?",
            "🌱 Una matumaini kwa kiasi gani sasa hivi?",
            "🫧 Unahisi utulivu kiasi gani sasa hivi?",
            "🤝 Unahisi kuwa karibu na wengine kiasi gani?",
            "🔋 Una nguvu kiasi gani sasa hivi?",
        ],
        "options": [
            ("very-low", "😢 Chini sana"),
            ("low", "🙁 Chini kidogo"),
            ("okay", "😐 Sawa"),
            ("good", "🙂 Vizuri kiasi"),
            ("very-good", "😊 Vizuri sana"),
        ],
    },
}

PHQ9_QUESTIONS_SW = [
    "Kupuuza kuvutiwa au kufurahia mambo",
    "Kujihisi chini, mwenye huzuni, au kukata tamaa",
    "Matatizo ya kulala au kulala kupita kiasi",
    "Kujihisi kuchoka au kuwa na nguvu ndogo",
    "Kula kidogo au kula kupita kiasi",
    "Kujihisi vibaya kuhusu wewe mwenyewe — au kuwa umeshindwa au kukosea kwa familia",
    "Matatizo ya kuzingatia mambo, kama kusoma gazeti au kutazama televisheni",
    "Kuhama polepole sana kiasi kwamba wengine wanaweza wamethambua? Au kinyume chake — kuwa na haraka mno au kutokuwa na utulivu",
    "Mawazo kwamba utakuwa bora zaidi ukifa, au kuwa umejisumbua kwa njia yoyote",
    "Je, matatizo haya yamefanya iwe vigumu kufanya kazi zako, kutunza vitu nyumbani, au kuendana na watu wengine?"
]

DAILY_QUOTES = [
    "A small step each day can create a meaningful change.",
    "Rest is a vital part of progress, not a luxury.",
    "You are more resilient than you believe.",
    "Notice the good, even on the hardest days.",
    "Breathe slowly. You deserve calm.",
    "Kindness to yourself is a powerful act.",
    "Today is an opportunity to support your wellbeing.",
    "Every moment is a chance to reset and move forward.",
    "Trust your instincts and honor your feelings.",
    "Healthy habits begin with one mindful choice.",
]

BREATHING_TECHNIQUES = {
    "box": {"inhale": 4, "hold": 4, "exhale": 4, "name": "Box Breathing"},
    "478": {"inhale": 4, "hold": 7, "exhale": 8, "name": "4-7-8 Breathing"},
    "deep": {"inhale": 4, "hold": 2, "exhale": 6, "name": "Deep Breathing"}
}

ASSESSMENT_LEVELS = [
    (0, 4, "Minimal", "You are doing well. Keep supporting your mental health with healthy habits.", "#64ffda"),
    (5, 9, "Mild", "Some stress may be present. Light self-care and reflection can help.", "#ffc864"),
    (10, 14, "Moderate", "Consider sharing your feelings with a trusted person or professional.", "#ff8a64"),
    (15, 30, "Severe", "Urgent support is recommended. Reach out to a mental health professional.", "#ff6464"),
]

RECOMMENDATION_RULES = [
    {
        "score_min": 0,
        "score_max": 4,
        "title": "Positive mood support",
        "tips": [
            "Keep a consistent sleep schedule.",
            "Try light exercise like walking or stretching.",
            "Practice gratitude by noting three good moments today.",
        ],
    },
    {
        "score_min": 5,
        "score_max": 9,
        "title": "Mild support",
        "tips": [
            "Try deep breathing or guided meditation for 5 minutes.",
            "Write a short journal entry about how you feel.",
            "Stay connected with a friend or family member.",
        ],
    },
    {
        "score_min": 10,
        "score_max": 14,
        "title": "Moderate support",
        "tips": [
            "Consider talking to a counselor or therapist.",
            "Use a stress-management technique like progressive muscle relaxation.",
            "Set small achievable goals and celebrate your progress.",
        ],
    },
    {
        "score_min": 15,
        "score_max": 30,
        "title": "Urgent support",
        "tips": [
            "Reach out to a trusted professional or crisis line.",
            "Keep emergency contacts handy and share your needs with someone close.",
            "Practice grounding exercises when anxiety or stress rises.",
        ],
    },
]

EMERGENCY_CONTACTS = [
    {
        "name": "Kenya Red Cross",
        "number": ["1190", "1199"],
        "description": "24/7 Toll-Free",
        "description_key": "contact_red_cross",
        "hours": "24/7",
        "hours_key": "hours_24_7",
    },
    {
        "name": "Befrienders Kenya",
        "number": ["+254 722 178 177"],
        "description": "Suicide Prevention & Support; calls, SMS, and WhatsApp",
        "description_key": "contact_befrienders",
        "hours": "Not specified",
        "hours_key": "hours_unspecified",
        "whatsapp": "254722178177",
    },
    {
        "name": "Childline Kenya",
        "number": ["116"],
        "description": "Youth & Adolescents; Toll-Free",
        "description_key": "contact_childline",
        "hours": "Not specified",
        "hours_key": "hours_unspecified",
    },
    {
        "name": "NACADA",
        "number": ["1192"],
        "description": "Substance Use & Addiction",
        "description_key": "contact_nacada",
        "hours": "Not specified",
        "hours_key": "hours_unspecified",
    },
    {
        "name": "National Emergency Lines",
        "number": ["999", "112"],
        "description": "Immediate emergency assistance",
        "description_key": "contact_emergency",
        "hours": "Not specified",
        "hours_key": "hours_unspecified",
    },
]


def fetch_json(url, data=None):
    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "AngazaCare/1.0 hospital finder"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def geocode_location(query):
    params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1})
    results = fetch_json(f"https://nominatim.openstreetmap.org/search?{params}")
    if not results:
        return None
    return {"lat": float(results[0]["lat"]), "lng": float(results[0]["lon"])}


def distance_km(lat1, lng1, lat2, lng2):
    earth_radius_km = 6371
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    delta_lat = lat2 - lat1
    delta_lng = math.radians(lng2 - lng1)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    return earth_radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def find_nearby_facilities(lat, lng):
    query = f"[out:json][timeout:20];(nwr(around:20000,{lat},{lng})[amenity~\"^(hospital|clinic)$\"];);out center tags;"
    response = fetch_json(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
    )
    facilities = []
    seen = set()
    for element in response.get("elements", []):
        tags = element.get("tags", {})
        center = element.get("center", element)
        facility_lat = center.get("lat")
        facility_lng = center.get("lon")
        name = tags.get("name") or tags.get("operator")
        if not name or facility_lat is None or facility_lng is None:
            continue
        facility_lat, facility_lng = float(facility_lat), float(facility_lng)
        key = (name.casefold(), round(facility_lat, 5), round(facility_lng, 5))
        if key in seen:
            continue
        seen.add(key)
        address = ", ".join(filter(None, (
            tags.get("addr:housenumber", "") + (" " if tags.get("addr:housenumber") and tags.get("addr:street") else "") + tags.get("addr:street", ""),
            tags.get("addr:suburb") or tags.get("addr:city") or tags.get("addr:town", ""),
            tags.get("addr:postcode", ""),
        )))
        facilities.append({
            "name": name,
            "type": tags.get("amenity", "healthcare"),
            "address": address,
            "lat": facility_lat,
            "lng": facility_lng,
            "distance_km": round(distance_km(lat, lng, facility_lat, facility_lng), 1),
        })
    facilities.sort(key=lambda facility: facility["distance_km"])
    return facilities


def get_daily_quote():
    today = date.today()
    quotes = get_translations().get("daily_quotes", DAILY_QUOTES)
    index = (today.year + today.month + today.day) % len(quotes)
    return quotes[index]


def get_assessment_level(score):
    for minimum, maximum, label, message, color in ASSESSMENT_LEVELS:
        if minimum <= score <= maximum:
            severity_key = label.lower()
            return {
                "label": label,
                "display_label": translate(f"severity_{severity_key}"),
                "message": translate(f"severity_{severity_key}_message"),
                "color": color,
            }
    return ASSESSMENT_LEVELS[-1]


def migrate_db():
    if not os.path.exists("angazacare.db"):
        return

    connection = sqlite3.connect("angazacare.db")
    cursor = connection.cursor()
    cursor.execute("PRAGMA table_info(user);")
    columns = [row[1] for row in cursor.fetchall()]

    if "role" not in columns:
        cursor.execute("ALTER TABLE user ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'patient';")
    if "consent_to_clinician_review" not in columns:
        cursor.execute("ALTER TABLE user ADD COLUMN consent_to_clinician_review BOOLEAN NOT NULL DEFAULT 0;")
    if "last_login_at" not in columns:
        cursor.execute("ALTER TABLE user ADD COLUMN last_login_at DATETIME;")
    if "login_count" not in columns:
        cursor.execute("ALTER TABLE user ADD COLUMN login_count INTEGER NOT NULL DEFAULT 0;")
    connection.commit()
    connection.close()


def init_db():
    with app.app_context():
        migrate_db()
        if not os.path.exists("angazacare.db"):
            db.create_all()
            seed_database()
        else:
            db.create_all()
            if User.query.count() == 0:
                seed_database()


def hash_password(password):
    return generate_password_hash(password)


def password_validation_error(password):
    if len(password) < 12:
        return "Password must be at least 12 characters long."
    if len(password) > 128:
        return "Password must be no more than 128 characters long."
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password) or not re.search(r"[0-9]", password) or not re.search(r"[^A-Za-z0-9\s]", password):
        return "Include uppercase and lowercase letters, a number, and a special character."
    return None


def check_password(password, hashed):
    if hashed.startswith(("$2a$", "$2b$", "$2y$")):
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    return check_password_hash(hashed, password)


def seed_database():
    demo_users = [
        {"name": "Demo User", "email": "demo@angazacare.com", "password": "password123", "role": "patient", "consent": True},
        {"name": "Test User", "email": "test@angazacare.com", "password": "test123", "role": "patient", "consent": False},
        {"name": "Dr. Admin", "email": "admin@angazacare.com", "password": "admin123", "role": "psychiatrist", "consent": False},
    ]

    for user_data in demo_users:
        user = User(
            name=user_data["name"],
            email=user_data["email"],
            password_hash=hash_password(user_data["password"]),
            role=user_data.get("role", "patient"),
            consent_to_clinician_review=user_data.get("consent", False),
        )
        db.session.add(user)
    db.session.commit()

    patients = User.query.filter_by(role="patient").all()
    psychiatrist = User.query.filter_by(role="psychiatrist").first()
    if psychiatrist:
        for patient in patients:
            assignment = Assignment(
                psychiatrist_id=psychiatrist.id,
                patient_id=patient.id,
                active=True,
            )
            db.session.add(assignment)

    for user in patients:
        for day_offset in range(1, 15):
            entry_date = date.today() - timedelta(days=day_offset)
            mood_score = 1 + ((user.id + day_offset) % 5)
            stress_level = 1 + ((user.id + day_offset * 2) % 10)
            note = "Reflecting on my day." if day_offset % 3 == 0 else "Maintaining healthy habits."
            mood_entry = MoodEntry(
                user_id=user.id,
                mood_score=mood_score,
                stress_level=stress_level,
                note=note,
                date=entry_date,
            )
            db.session.add(mood_entry)

        assessments = [
            {"score": 3, "answers": [0] * 10},
            {"score": 8, "answers": [1] * 8 + [0, 0]},
            {"score": 16, "answers": [2] * 8 + [0, 0]},
        ]
        for assessment_data in assessments:
            severity = get_assessment_level(assessment_data["score"])["label"]
            assessment = Assessment(
                user_id=user.id,
                score=assessment_data["score"],
                severity=severity,
                answers_json=json.dumps(assessment_data["answers"]),
            )
            db.session.add(assessment)

    for rule in RECOMMENDATION_RULES:
        recommendation = Recommendation(
            score_range_min=rule["score_min"],
            score_range_max=rule["score_max"],
            tips=json.dumps(rule["tips"]),
        )
        db.session.add(recommendation)

    db.session.commit()


def get_mood_chart_data(user):
    today = date.today()
    labels = []
    mood_values = []
    stress_values = []
    has_recent_entries = False
    for offset in reversed(range(7)):
        day = today - timedelta(days=offset)
        labels.append(day.strftime("%b %d"))
        entry = MoodEntry.query.filter_by(user_id=user.id, date=day).first()
        has_recent_entries = has_recent_entries or entry is not None
        mood_values.append(entry.mood_score if entry else None)
        stress_values.append(entry.stress_level if entry else None)

    # If the last 7 days contain no actual records, fall back to the most recent available entries.
    if has_recent_entries:
        return labels, mood_values, stress_values

    recent_entries = (
        MoodEntry.query.filter_by(user_id=user.id)
        .order_by(MoodEntry.date.desc())
        .limit(7)
        .all()
    )
    if recent_entries:
        recent_entries = list(reversed(recent_entries))
        labels = [entry.date.strftime("%b %d") for entry in recent_entries]
        mood_values = [entry.mood_score for entry in recent_entries]
        stress_values = [entry.stress_level for entry in recent_entries]

    return labels, mood_values, stress_values


def get_streak(user):
    streak = 0
    day = date.today()
    while True:
        entry = MoodEntry.query.filter_by(user_id=user.id, date=day).first()
        if entry:
            streak += 1
            day -= timedelta(days=1)
        else:
            break
    return streak

def check_crisis_flag(user):
    today = date.today()
    low_mood_count = 0
    for day_offset in range(3):
        entry = MoodEntry.query.filter_by(user_id=user.id, date=today - timedelta(days=day_offset)).first()
        if entry and entry.mood_score == 1:
            low_mood_count += 1
    last_assessment = Assessment.query.filter_by(user_id=user.id).order_by(Assessment.created_at.desc()).first()
    high_score = last_assessment and last_assessment.score >= 20
    return low_mood_count == 3 or high_score


def get_supportive_fallback(user_message=None, lang="en"):
    """Get a simple topical fallback response when AI is unavailable."""
    language_text = TRANSLATIONS.get(lang, TRANSLATIONS["en"])

    def localized_response(key):
        return language_text.get(key, TRANSLATIONS["en"][key])

    if lang in ("ki", "kln"):
        text = (user_message or "").lower()
        if any(word in text for word in ["stress", "stressed", "anxiety", "anxious", "worried", "nervous", "tension"]):
            return localized_response("chat_stress")
        if any(word in text for word in ["sleep", "insomnia", "tired", "rest", "awake", "sleepless"]):
            return localized_response("chat_sleep")
        if any(word in text for word in ["sad", "depressed", "hopeless", "down", "low mood", "unhappy"]):
            return localized_response("chat_sad")
        if any(word in text for word in ["motivate", "motivation", "goal", "productive", "energy"]):
            return localized_response("chat_motivation")
        if any(word in text for word in ["friend", "family", "support", "alone", "help", "talk"]):
            return localized_response("chat_support")
        return localized_response(f"chat_default_{random.randint(1, 3)}")

    if user_message:
        text = user_message.lower()
        if any(word in text for word in ["stress", "stressed", "anxiety", "anxious", "worried", "nervous", "tension"]):
            if lang == "sw":
                return "Ninaelewa msongo unavyoweza kuumiza. Jaribu kupumua kwa utulivu, pumua ndani kwa tarakimu nne, shikilia kwa nane, na uachilie kwa tarakimu nane."
            return "Stress can feel overwhelming. Try a short breathing break: inhale slowly, hold for a few seconds, and exhale. Small steps can help you feel steadier."
        if any(word in text for word in ["sleep", "insomnia", "tired", "rest", "awake", "sleepless"]):
            if lang == "sw":
                return "Mapumziko ni muhimu. Jaribu kuchukua mlozi wa kiafya kabla ya kulala, epuka skrini kwa dakika 30 kabla ya usingizi, na panga saa ya kulala saa ile ile kila usiku."
            return "Sleep habits make a big difference. Avoid screens before bed, keep your room calm, and try going to sleep at the same time each night."
        if any(word in text for word in ["sad", "depressed", "hopeless", "down", "low mood", "unhappy"]):
            if lang == "sw":
                return "Ni sawa kuhisi huzuni kwa wakati fulani. Jaribu kushiriki hisia zako na rafiki au familia, na fanya jambo ndogo unalolipenda leo."
            return "Feeling down is hard, and you don't have to face it alone. Reach out to someone you trust and try one small activity that usually lifts your mood."
        if any(word in text for word in ["motivate", "motivation", "goal", "productive", "energy"]):
            if lang == "sw":
                return "Chukua hatua ndogo ya kwanza leo. Weka lengo la rahisi, ukumbuke kusherehekea mafanikio madogo, na umuulize rafiki atakusaidie kuendelea."
            return "Start with a very small goal and celebrate progress, not perfection. Breaking a task into tiny steps can make it feel more realistic and easier to move forward."
        if any(word in text for word in ["friend", "family", "support", "alone", "help", "talk"]):
            if lang == "sw":
                return "Kutafuta msaada ni hatua nzuri. Shirikiana na mtu unayemwamini au omba muda wa kuzungumza kuhusu mambo yanayokufanya uhisi hivi."
            return "Asking for support is a strong step. Talk to someone you trust and let them know what you're feeling so they can be there with you."
    if lang == "sw":
        fallback_responses = [
            "Ninaelewa na niko hapa kukusaidia. Sewasawa, tuanze kwa hatua ndogo.",
            "Jaribu kupumua kwa utulivu na umwombe rafiki au mshauri kukusaidia kupitia hisia zako.",
            "Una haki kuhisi hivi. Tafuta njia moja ndogo ya kujitunza leo, kama kutembea au kuandika mawazo yako.",
        ]
    else:
        fallback_responses = [
            "I hear you and I'm here to support you. Tell me more about how you're feeling today.",
            "You're not alone. I can help you calm your mind and find one small step forward.",
            "Small steps can make a difference. Start with one breath and one simple action.",
        ]
    # Pick a friendly fallback if no specific topical response was returned above
    return random.choice(fallback_responses)


def blur_text(value, reveal_start=2, reveal_end=2, mask_char="•"):
    if not value:
        return ""
    if len(value) <= reveal_start + reveal_end + 2:
        return mask_char * len(value)
    return value[:reveal_start] + mask_char * (len(value) - reveal_start - reveal_end) + value[-reveal_end:]


def blur_email(email):
    if not email or "@" not in email:
        return blur_text(email)
    local, domain = email.split("@", 1)
    hidden_local = blur_text(local, reveal_start=1, reveal_end=1)
    if "." in domain:
        domain_name, ext = domain.rsplit(".", 1)
        hidden_domain = blur_text(domain_name, reveal_start=1, reveal_end=1)
        return f"{hidden_local}@{hidden_domain}.{ext}"
    return f"{hidden_local}@{blur_text(domain, reveal_start=1, reveal_end=1)}"


def get_patient_ai_summary(patient):
    latest_assessment = Assessment.query.filter_by(user_id=patient.id).order_by(Assessment.created_at.desc()).first()
    latest_mood = MoodEntry.query.filter_by(user_id=patient.id).order_by(MoodEntry.date.desc()).first()
    consent = "yes" if patient.consent_to_clinician_review else "no"
    assessment_text = (
        f"Latest assessment score {latest_assessment.score} ({latest_assessment.severity}). "
        if latest_assessment else "No assessment history available. "
    )
    mood_text = (
        f"Most recent mood rating {latest_mood.mood_score} with stress {latest_mood.stress_level}. "
        if latest_mood else "No recent mood entries available. "
    )
    user_message = (
        "Provide a short, clinician-facing summary for a psychiatrist based on this patient context. "
        f"Patient consent to clinician review: {consent}. "
        f"{assessment_text}{mood_text}"
    )

    ai_text = None
    if genai is not None:
        try:
            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                system_instruction="You are a supportive clinical assistant helping a psychiatrist review anonymized patient trends.",
            )
            response = model.generate_content(
                user_message,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.35,
                    top_p=0.85,
                    max_output_tokens=120,
                )
            )
            ai_text = response.text.strip() if response.text else None
        except Exception as e:
            app.logger.exception(f"Patient AI summary failed: {e}")

    if not ai_text:
        ai_text = get_supportive_fallback(user_message, lang=get_language())

    return {
        "prompt": user_message,
        "response": ai_text,
    }


def get_recent_mood_summary(user, days=7):
    today = date.today()
    start_date = today - timedelta(days=days)
    entries = MoodEntry.query.filter(
        MoodEntry.user_id == user.id,
        MoodEntry.date >= start_date,
        MoodEntry.date <= today
    ).all()

    if not entries:
        return None

    mood_total = sum(entry.mood_score for entry in entries)
    stress_total = sum(entry.stress_level for entry in entries)
    notes = [entry.note for entry in entries if entry.note]
    return {
        "days": len(entries),
        "average_mood": round(mood_total / len(entries), 1),
        "average_stress": round(stress_total / len(entries), 1),
        "recent_note": notes[-1] if notes else None,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            validate_csrf(request.form.get("csrf_token", ""))
        except ValidationError:
            flash("Your form expired. Please try again.", "danger")
            return render_template("register.html", csrf_token=generate_csrf()), 400

        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash(get_text("invalid_credentials"), "danger")
            return render_template("register.html", csrf_token=generate_csrf()), 400
        password_error = password_validation_error(password)
        if password_error:
            flash(password_error, "danger")
            return render_template("register.html", csrf_token=generate_csrf()), 400
        existing = User.query.filter(db.func.lower(User.email) == email).first()
        if existing:
            flash(get_text("email_registered"), "warning")
        else:
            new_user = User(
                name=name,
                email=email,
                password_hash=hash_password(password),
            )
            db.session.add(new_user)
            db.session.commit()
            flash(get_text("account_created"), "success")
            return redirect(url_for("login"))

    return render_template("register.html", csrf_token=generate_csrf())


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        try:
            validate_csrf(request.form.get("csrf_token", ""))
        except ValidationError:
            flash("Your form expired. Please try again.", "danger")
            return render_template("forgot_password.html", csrf_token=generate_csrf()), 400

        email = (request.form.get("email") or "").strip().lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            user = User.query.filter(db.func.lower(User.email) == email).first()
            if user:
                now = datetime.utcnow()
                PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update(
                    {"used_at": now}, synchronize_session=False
                )
                token = secrets.token_urlsafe(32)
                db.session.add(
                    PasswordResetToken(
                        user_id=user.id,
                        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                        expires_at=now + timedelta(minutes=30),
                    )
                )
                db.session.commit()
                if app_env == "development":
                    reset_url = url_for("reset_password", token=token, _external=True)
                    app.logger.info("Development password reset URL: %s", reset_url)

        flash(
            "If an account exists for that email, password reset instructions have been generated.",
            "info",
        )

    return render_template("forgot_password.html", csrf_token=generate_csrf())


@app.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def reset_password(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    reset_record = PasswordResetToken.query.filter_by(
        token_hash=token_hash, used_at=None
    ).first()
    now = datetime.utcnow()
    if not reset_record or reset_record.expires_at <= now:
        flash("This password reset link is invalid or expired. Request a new one.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        try:
            validate_csrf(request.form.get("csrf_token", ""))
        except ValidationError:
            flash("Your form expired. Please try again.", "danger")
            return render_template(
                "reset_password.html", csrf_token=generate_csrf(), token=token
            ), 400

        password = request.form.get("password") or ""
        confirmation = request.form.get("confirm_password") or ""
        password_error = password_validation_error(password)
        if password_error:
            flash(password_error, "danger")
            return render_template(
                "reset_password.html", csrf_token=generate_csrf(), token=token
            ), 400
        if password != confirmation:
            flash("The passwords do not match.", "danger")
            return render_template(
                "reset_password.html", csrf_token=generate_csrf(), token=token
            ), 400

        user_id = reset_record.user_id
        updated = PasswordResetToken.query.filter_by(
            id=reset_record.id, used_at=None
        ).filter(PasswordResetToken.expires_at > now).update(
            {"used_at": now}, synchronize_session=False
        )
        if updated != 1:
            db.session.rollback()
            flash("This password reset link is invalid or expired. Request a new one.", "danger")
            return redirect(url_for("forgot_password"))

        user = db.session.get(User, user_id)
        if not user:
            db.session.rollback()
            flash("This password reset link is invalid or expired. Request a new one.", "danger")
            return redirect(url_for("forgot_password"))

        user.password_hash = hash_password(password)
        PasswordResetToken.query.filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.id != reset_record.id,
            PasswordResetToken.used_at.is_(None),
        ).update({"used_at": now}, synchronize_session=False)
        db.session.commit()
        flash("Your password has been reset. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", csrf_token=generate_csrf(), token=token)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            validate_csrf(request.form.get("csrf_token", ""))
        except ValidationError:
            flash(get_text("invalid_credentials"), "danger")
            return render_template("login.html", csrf_token=generate_csrf()), 400

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash(get_text("invalid_credentials"), "danger")
            return render_template("login.html", csrf_token=generate_csrf()), 400

        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user and check_password(password, user.password_hash):
            user.last_login_at = datetime.utcnow()
            user.login_count = (user.login_count or 0) + 1
            if user.password_hash.startswith(("$2a$", "$2b$", "$2y$")):
                user.password_hash = hash_password(password)
            session.clear()
            db.session.commit()
            login_user(user, remember=request.form.get("remember") == "on")
            return redirect(url_for("dashboard"))
        flash(get_text("invalid_credentials"), "danger")

    return render_template("login.html", csrf_token=generate_csrf())


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash(get_text("logged_out"), "info")
    return redirect(url_for("login"))


def admin_required(func):
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or getattr(current_user, "role", None) not in {"admin", "psychiatrist"}:
            flash(get_text("invalid_credentials"), "danger")
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


def build_admin_activity_data(patient_ids, include_anonymous=False):
    active_since = datetime.utcnow() - timedelta(days=30)
    chat_scope = ChatInteraction.user_id.in_(patient_ids)
    if include_anonymous:
        chat_scope = chat_scope | ChatInteraction.user_id.is_(None)

    chat_query = ChatInteraction.query.filter(chat_scope)
    assessment_query = Assessment.query.filter(Assessment.user_id.in_(patient_ids))
    recent_chats = chat_query.order_by(ChatInteraction.created_at.desc()).limit(50).all()
    recent_assessment_records = assessment_query.order_by(Assessment.created_at.desc()).limit(50).all()

    active_user_ids = {
        row[0]
        for row in db.session.query(ChatInteraction.user_id)
        .filter(chat_scope, ChatInteraction.user_id.isnot(None), ChatInteraction.created_at >= active_since)
        .distinct()
        .all()
    }
    active_user_ids.update(
        row[0]
        for row in db.session.query(Assessment.user_id)
        .filter(Assessment.user_id.in_(patient_ids), Assessment.created_at >= active_since)
        .distinct()
        .all()
    )
    active_user_ids.update(
        row[0]
        for row in db.session.query(MoodEntry.user_id)
        .filter(MoodEntry.user_id.in_(patient_ids), MoodEntry.date >= active_since.date())
        .distinct()
        .all()
    )

    recent_assessments = []
    for assessment_record in recent_assessment_records:
        has_consent = bool(
            assessment_record.user
            and assessment_record.user.consent_to_clinician_review
        )
        answers = assessment_record.answers if has_consent else []
        details = [
            {"question": PHQ9_QUESTIONS[index], "answer": answer}
            for index, answer in enumerate(answers)
            if index < len(PHQ9_QUESTIONS)
        ]
        recent_assessments.append({
            "user_id": assessment_record.user_id,
            "created_at": assessment_record.created_at,
            "score": assessment_record.score if has_consent else None,
            "severity": assessment_record.severity if has_consent else None,
            "has_consent": has_consent,
            "details": details,
        })

    return {
        "total_active_users": len(active_user_ids),
        "total_assessments": assessment_query.count(),
        "total_chat_interactions": chat_query.count(),
        "recent_chats": recent_chats,
        "recent_assessments": recent_assessments,
    }


def admin_api_access_error():
    if not current_user.is_authenticated:
        return jsonify({"error": "Sign in required."}), 401
    if current_user.role not in {"admin", "psychiatrist"}:
        return jsonify({"error": "Administrator access required."}), 403
    return None


@app.route("/api/admin/auth/session", methods=["GET"])
def api_admin_auth_session():
    response = jsonify({
        "csrf_token": generate_csrf(),
        "authenticated": current_user.is_authenticated,
        "role": current_user.role if current_user.is_authenticated else None,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/auth/login", methods=["POST"])
@limiter.limit("5 per minute", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    try:
        validate_csrf(data.get("csrf_token", ""))
    except ValidationError:
        return jsonify({"error": "Your session expired. Reload and try again."}), 400

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = None
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        user = User.query.filter(db.func.lower(User.email) == email).first()
    if (
        not user
        or not check_password(password, user.password_hash)
        or user.role not in {"admin", "psychiatrist"}
    ):
        return jsonify({"error": "Invalid credentials or admin access unavailable."}), 401

    if user.password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        user.password_hash = hash_password(password)
        db.session.commit()
    session.clear()
    login_user(user, remember=False)
    response = jsonify({
        "authenticated": True,
        "role": user.role,
        "csrf_token": generate_csrf(),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/auth/logout", methods=["POST"])
def api_admin_logout():
    data = request.get_json(silent=True) or {}
    try:
        validate_csrf(data.get("csrf_token", ""))
    except ValidationError:
        return jsonify({"error": "Your session expired. Reload and try again."}), 400
    if current_user.is_authenticated:
        logout_user()
    session.clear()
    response = jsonify({"authenticated": False, "csrf_token": generate_csrf()})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/dashboard", methods=["GET"])
@limiter.limit("60 per minute", methods=["GET"])
def api_admin_dashboard():
    access_error = admin_api_access_error()
    if access_error:
        return access_error

    if current_user.role == "admin":
        patient_ids = [
            row[0]
            for row in db.session.query(User.id).filter(User.role == "patient").all()
        ]
        activity_scope = "All patient accounts"
        activity_data = build_admin_activity_data(patient_ids, include_anonymous=True)
    else:
        patient_ids = [
            row[0]
            for row in db.session.query(Assignment.patient_id)
            .join(User, User.id == Assignment.patient_id)
            .filter(
                Assignment.psychiatrist_id == current_user.id,
                Assignment.active.is_(True),
                User.role == "patient",
            )
            .all()
        ]
        activity_scope = "Assigned patients"
        activity_data = build_admin_activity_data(patient_ids)

    response = jsonify({
        "scope": activity_scope,
        "stats": {
            "active_users_30_days": activity_data["total_active_users"],
            "assessments": activity_data["total_assessments"],
            "chat_interactions": activity_data["total_chat_interactions"],
        },
        "chats": [
            {
                "created_at": interaction.created_at.isoformat(),
                "user_id": interaction.user_id,
                "session_id": interaction.session_hash[:8] if interaction.session_hash else None,
                "prompt": (
                    interaction.user_message
                    if interaction.user and interaction.user.consent_to_clinician_review
                    else None
                ),
            }
            for interaction in activity_data["recent_chats"]
        ],
        "assessments": [
            {
                "user_id": assessment["user_id"],
                "created_at": assessment["created_at"].isoformat(),
                "score": assessment["score"],
                "severity": assessment["severity"],
                "responses": assessment["details"],
            }
            for assessment in activity_data["recent_assessments"]
        ],
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    if current_user.role == "admin":
        patient_ids = [
            row[0]
            for row in db.session.query(User.id).filter(User.role == "patient").all()
        ]
        activity_data = build_admin_activity_data(patient_ids, include_anonymous=True)
        return render_template(
            "admin_dashboard.html",
            activity_scope="All patient accounts",
            **activity_data,
        )

    patient_records = (
        User.query
        .join(Assignment, Assignment.patient_id == User.id)
        .filter(
            Assignment.psychiatrist_id == current_user.id,
            Assignment.active == True,
            User.role == "patient",
        )
        .order_by(User.id.asc())
        .all()
    )

    patients = []
    for patient in patient_records:
        summary = (
            get_patient_ai_summary(patient)
            if patient.consent_to_clinician_review
            else None
        )
        patients.append({
            "id": patient.id,
            "masked_name": blur_text(patient.name, reveal_start=2, reveal_end=2),
            "masked_email": blur_email(patient.email),
            "consent_to_clinician_review": patient.consent_to_clinician_review,
            "ai_summary": summary,
        })

    patient_ids = [patient.id for patient in patient_records]
    activity_data = build_admin_activity_data(patient_ids)
    return render_template(
        "admin.html",
        patients=patients,
        activity_scope="Assigned patients",
        **activity_data,
    )


@app.route("/admin/user/<int:patient_id>")
@login_required
@admin_required
def admin_user_detail(patient_id):
    patient = User.query.get_or_404(patient_id)
    assignment = Assignment.query.filter_by(
        psychiatrist_id=current_user.id,
        patient_id=patient.id,
        active=True,
    ).first()
    if not assignment:
        flash("Access denied.", "danger")
        return redirect(url_for("admin_dashboard"))

    full_access = bool(patient.consent_to_clinician_review)

    assessments_query = Assessment.query.filter_by(user_id=patient.id).order_by(Assessment.created_at.desc())
    mood_query = MoodEntry.query.filter_by(user_id=patient.id).order_by(MoodEntry.date.desc())

    assessments = [
        {
            "date": a.created_at,
            "score": a.score,
            "severity": a.severity,
            "details": [
                {"question": PHQ9_QUESTIONS[index], "answer": answer}
                for index, answer in enumerate(a.answers)
                if full_access and index < len(PHQ9_QUESTIONS)
            ],
        }
        for a in assessments_query.all()
    ]
    chat_messages = ChatInteraction.query.filter_by(user_id=patient.id).order_by(
        ChatInteraction.created_at.desc()
    ).limit(100).all()

    if full_access:
        mood_entries = [
            {
                "date": m.date,
                "mood_score": m.mood_score,
                "stress_level": m.stress_level,
                "note": m.note,
            }
            for m in mood_query.all()
        ]
    else:
        mood_entries = [
            {
                "date": m.date,
                "mood_score": m.mood_score,
                "stress_level": m.stress_level,
                "note": None,
            }
            for m in mood_query.limit(30).all()
        ]

    ai_summary = get_patient_ai_summary(patient) if full_access else None
    masked_name = blur_text(patient.name, reveal_start=2, reveal_end=2)
    masked_email = blur_email(patient.email)

    # Log the view for auditing
    try:
        log = ClinicianViewLog(psychiatrist_id=current_user.id, patient_id=patient.id, note="Viewed from admin UI")
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return render_template(
        "admin_user.html",
        patient=patient,
        assessments=assessments,
        chat_messages=chat_messages,
        mood_entries=mood_entries,
        full_access=full_access,
        ai_summary=ai_summary,
        masked_name=masked_name,
        masked_email=masked_email,
    )


@app.route("/set_language/<lang>")
def set_language(lang):
    """Set the user's language preference."""
    if lang in TRANSLATIONS:
        session["language"] = lang
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/api/user-summary")
@login_required
def api_user_summary():
    latest_assessment = (
        Assessment.query.filter_by(user_id=current_user.id)
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )
    today_entry = MoodEntry.query.filter_by(user_id=current_user.id, date=date.today()).first()
    response = jsonify({
        "assessment": {
            "score": latest_assessment.score,
            "severity": latest_assessment.severity,
            "created_at": latest_assessment.created_at.isoformat(),
        } if latest_assessment else None,
        "today_mood": {
            "mood_score": today_entry.mood_score,
            "stress_level": today_entry.stress_level,
            "note": today_entry.note or "",
        } if today_entry else None,
        "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
        "login_count": current_user.login_count or 0,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/mood-history")
@login_required
def api_mood_history():
    today = date.today()
    entries = MoodEntry.query.filter(
        MoodEntry.user_id == current_user.id,
        MoodEntry.date >= today - timedelta(days=6),
        MoodEntry.date <= today,
    ).all()
    entries_by_date = {entry.date: entry for entry in entries}
    days = [today - timedelta(days=offset) for offset in reversed(range(7))]
    response = jsonify({
        "labels": [day.strftime("%b %d") for day in days],
        "mood": [entries_by_date[day].mood_score if day in entries_by_date else None for day in days],
        "stress": [entries_by_date[day].stress_level if day in entries_by_date else None for day in days],
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/dashboard")
@login_required
def dashboard():
    today_entry = MoodEntry.query.filter_by(user_id=current_user.id, date=date.today()).first()
    labels, mood_data, stress_data = get_mood_chart_data(current_user)
    crisis_detected = check_crisis_flag(current_user)
    local_hour = datetime.now().hour
    if get_language() == "sw":
        time_greeting = "Habari za asubuhi" if 5 <= local_hour < 12 else "Habari za mchana" if local_hour < 17 else "Habari za jioni"
    else:
        time_greeting = "Good Morning" if 5 <= local_hour < 12 else "Good Afternoon" if local_hour < 17 else "Good Evening"
    
    return render_template(
        "dashboard.html",
        daily_quote=get_daily_quote(),
        time_greeting=time_greeting,
        today_entry=today_entry,
        chart_labels=labels,
        mood_chart=mood_data,
        stress_chart=stress_data,
        has_chart_data=any(value is not None for value in mood_data),
        chart_entry_count=sum(value is not None for value in mood_data),
        crisis_detected=crisis_detected,
        current_lang=get_language(),
        t=get_translations(),
    )


@app.route("/assessment", methods=["GET", "POST"])
@login_required
def assessment():
    translations = get_translations()
    checkin_copy = MOOD_CHECKIN_COPY.get(get_language(), MOOD_CHECKIN_COPY["en"])
    questions = checkin_copy["questions"]
    answer_options = checkin_copy["options"]
    valid_answers = {value for value, _ in answer_options}
    selected_answers = [None] * len(questions)
    submitted = False
    answer_error = None
    if request.method == "POST":
        submitted_answers = [request.form.get(f"q{i}") for i in range(len(questions))]
        selected_answers = [value if value in valid_answers else None for value in submitted_answers]
        submitted = all(value in valid_answers for value in submitted_answers)
        if not submitted:
            answer_error = checkin_copy["incomplete"]

    return render_template(
        "assessment.html",
        questions=questions,
        answer_options=answer_options,
        answer_values=[value for value, _ in answer_options],
        selected_answers=selected_answers,
        submitted=submitted,
        answer_error=answer_error,
        checkin_copy=checkin_copy,
        current_lang=get_language(),
        t=translations,
    )


@app.route("/mood_tracker", methods=["GET", "POST"])
@login_required
def mood_tracker():
    today_entry = MoodEntry.query.filter_by(user_id=current_user.id, date=date.today()).first()
    if request.method == "POST":
        mood_score = int(request.form.get("mood_score", 3))
        stress_level = int(request.form.get("stress_level", 5))
        note = request.form.get("note", "")
        if today_entry:
            today_entry.mood_score = mood_score
            today_entry.stress_level = stress_level
            today_entry.note = note
        else:
            today_entry = MoodEntry(
                user_id=current_user.id,
                mood_score=mood_score,
                stress_level=stress_level,
                note=note,
                date=date.today(),
            )
            db.session.add(today_entry)
        db.session.commit()
        flash(get_text("today_mood_recorded"), "success")
        return redirect(url_for("mood_tracker", saved=1))

    return render_template(
        "mood_tracker.html",
        today_entry=today_entry,
    )


@app.route("/recommendations")
@login_required
def recommendations():
    last_assessment = (
        Assessment.query.filter_by(user_id=current_user.id)
        .order_by(Assessment.created_at.desc(), Assessment.id.desc())
        .first()
    )
    answers = last_assessment.answers if last_assessment else []
    if not isinstance(answers, list):
        answers = []
    answers = [
        answer if isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer <= 3 else 0
        for answer in answers[:10]
    ]

    language = get_language()
    recommendation_texts = {
        "en": {
            "interest": "Schedule one enjoyable activity today, even for ten minutes.",
            "mood": "Tell someone you trust how you feel; consider speaking with a counselor.",
            "sleep": "Keep a regular wake-up time and wind down quietly before bed.",
            "energy": "Start one task with a five-minute step, then take a break.",
            "appetite": "Try regular meals and water; seek care if eating changes persist.",
            "self_worth": "Challenge one self-critical thought with what you would tell a friend.",
            "concentration": "Focus on one task for a short timed block, then take a break.",
            "restlessness": "Pause for a calming breath and tell someone you trust how you feel.",
            "impact": "Consider speaking with a mental-health or primary-care professional.",
            "urgent": "Tell someone you trust and contact a mental-health professional now. If you are in immediate danger, call emergency services; in Kenya, call 999 or 112.",
            "steady": "Keep supportive routines and stay connected with people you trust.",
        },
        "sw": {
            "interest": "Panga shughuli moja unayoifurahia leo, hata kwa dakika kumi.",
            "mood": "Mwambie mtu unayemwamini jinsi unavyojisikia.",
            "sleep": "Amka kwa wakati unaofanana kila siku na tulia kabla ya kulala.",
            "energy": "Anza kazi moja kwa hatua ya dakika tano, kisha pumzika.",
            "appetite": "Jaribu kula milo ya kawaida na kunywa maji; tafuta msaada ikiwa mabadiliko yanaendelea.",
            "self_worth": "Jibu wazo moja la kujikosoa kwa huruma unayompa rafiki.",
            "concentration": "Fanya kazi moja kwa muda mfupi uliopanga, kisha pumzika.",
            "restlessness": "Vuta pumzi ya kutuliza na mwambie mtu unayemwamini jinsi unavyojisikia.",
            "impact": "Fikiria kuzungumza na mtaalamu wa afya ya akili au mhudumu wa afya.",
            "urgent": "Mwambie mtu unayemwamini na uwasiliane na mtaalamu wa afya ya akili sasa. Ukiwa hatarini, piga huduma za dharura; nchini Kenya piga 999 au 112.",
            "steady": "Endelea na mazoea yanayokusaidia na wasiliana na watu unaowaamini.",
        },
        "ki": {
            "interest": "Hũthũra ihinda rĩa gwĩka kĩndũ kĩrĩa ũkenagia, o na nĩ dagĩka ikũmi.",
            "mood": "Ũganĩre na mũndũ ũrĩ na mwĩhoko ũrĩa wĩĩhĩtĩte; ũhũthĩre ũteithio wa mũrũgamĩrĩri.",
            "sleep": "Rĩrĩra na ũrarĩre ihinda rĩmwe o mũthenya, ũtue ũhoro mbere ya kũraha.",
            "energy": "Tandika wĩra na kahinda ka ndagĩka ithano, hĩndĩ ĩyo ũhurũke.",
            "appetite": "Rĩa irio ihinda-inĩ rĩa kawaida na ũnyue maaĩ; rora mũteithia angĩkorwo mabadiliko nĩ maendete.",
            "self_worth": "Cookeria ciugo cia kwĩtua ũũru na ũrĩa ũgacokeria mũrata waku.",
            "concentration": "Hũthũra ihinda inini gũthikĩria wĩra ũmwe, hĩndĩ ĩyo ũhurũke.",
            "restlessness": "Taha na ũtuli na ũganĩre na mũndũ ũrĩ na mwĩhoko.",
            "impact": "Gwĩciria gũganĩra na mũteithia wa ũgima wa mũoyo kana wa mwĩrĩ.",
            "urgent": "Ũganĩre na mũndũ ũrĩ na mwĩhoko na ũcaria mũteithia rĩu. Angĩkorwo ũrĩ mathĩnainĩ ma haraka, itana 999 kana 112 Kenya.",
            "steady": "Endelea na mĩtugo ĩgũteithagia na ũtũũre hakuhĩ na andũ ũrĩ na mwĩhoko.",
        },
        "kln": {
            "interest": "Konyi tugul eng activity nebo kogenyini, even minutes taman.",
            "mood": "Share feelings nebo inendet agobo trusted person; consider counselor.",
            "sleep": "Keep wake-up time nebo tugul ak wind down before sleeping.",
            "energy": "Start task nebo tugul eng minutes tano, then take a break.",
            "appetite": "Try regular meals ak water; seek care if changes persist.",
            "self_worth": "Challenge self-critical thought eng words nebo friend.",
            "concentration": "Focus task nebo tugul eng short time, then break.",
            "restlessness": "Pause agobo calm breath ak share feelings agobo trusted person.",
            "impact": "Consider mental-health ak primary-care professional.",
            "urgent": "Tell trusted person ak contact mental-health professional now. If immediate danger, call 999 ak 112 eng Kenya.",
            "steady": "Keep supportive routines ak stay connected agobo trusted people.",
        },
    }
    recommendation_text = recommendation_texts.get(language, recommendation_texts["en"])

    answer_topics = {
        0: "interest",
        1: "mood",
        2: "sleep",
        3: "energy",
        4: "appetite",
        5: "self_worth",
        6: "concentration",
        7: "restlessness",
        9: "impact",
    }
    scored_topics = sorted(
        ((index, answers[index] if index < len(answers) else 0) for index in answer_topics),
        key=lambda item: item[1],
        reverse=True,
    )
    personalized_tips = [
        recommendation_text[answer_topics[index]]
        for index, _ in scored_topics[:3]
        if answer_topics[index] in recommendation_text
    ]
    urgent_recommendation = bool(len(answers) > 8 and answers[8] > 0)
    if urgent_recommendation:
        personalized_tips.insert(0, recommendation_text["urgent"])
        personalized_tips = personalized_tips[:3]
    if not personalized_tips and answers:
        personalized_tips = [recommendation_text["steady"]]

    return render_template(
        "recommendations.html",
        assessment=last_assessment,
        personalized_tips=personalized_tips,
        t=get_translations(),
    )


@app.route("/api/geocode", methods=["POST"])
@login_required
def api_geocode():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "")).strip()
    if len(query) < 2 or len(query) > 160:
        return jsonify({"error": get_text("api_enter_town_city")}), 400
    try:
        location = geocode_location(query)
    except Exception:
        app.logger.exception("Location lookup failed")
        return jsonify({"error": get_text("api_location_unavailable")}), 502
    if location is None:
        return jsonify({"error": get_text("api_no_matching_location")}), 404
    return jsonify(location)


@app.route("/api/recommend", methods=["POST"])
@login_required
def api_recommend():
    payload = request.get_json(silent=True) or {}
    try:
        lat = float(payload.get("lat"))
        lng = float(payload.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": get_text("api_invalid_location")}), 400
    if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return jsonify({"error": get_text("api_invalid_location")}), 400
    try:
        facilities = find_nearby_facilities(lat, lng)
    except Exception:
        app.logger.exception("Nearby facility lookup failed")
        maps_query = urllib.parse.urlencode({"api": "1", "query": f"hospitals near {lat},{lng}"})
        return jsonify({
            "error": get_text("api_facility_unavailable"),
            "fallback": {
                "text": get_text("api_facility_fallback"),
                "maps_url": f"https://www.google.com/maps/search/?{maps_query}",
            },
        }), 502
    nearest = next(
        (facility for facility in facilities if facility["type"] == "hospital"),
        facilities[0] if facilities else None,
    )
    return jsonify({"facilities": facilities, "nearest": nearest})


@app.route("/emergency")
@login_required
def emergency():
    return render_template("emergency.html", contacts=EMERGENCY_CONTACTS)

@app.route("/breathing", methods=["GET", "POST"])
@login_required
def breathing():
    if request.method == "POST":
        technique = request.form.get("technique", "box")
        duration = int(request.form.get("duration", 5))
        session_entry = BreathingSession(
            user_id=current_user.id,
            technique=BREATHING_TECHNIQUES[technique]["name"],
            duration_minutes=duration,
            date=date.today(),
        )
        db.session.add(session_entry)
        db.session.commit()
        flash(get_text("breathing_saved").format(duration=duration, technique=BREATHING_TECHNIQUES[technique]["name"]), "success")
        return redirect(url_for("breathing"))
    
    return render_template("breathing.html", techniques=BREATHING_TECHNIQUES)


KIRAYA_KB = {
    "crisis_keywords_en": ["hurt myself", "kill myself", "suicide", "want to die", "end it", "can't take it"],
    "crisis_keywords_sw": ["nidhuru", "kufa", "jiuapo", "kumalizia", "haiwezi"],
    "befrienders": "0800 720 177",
}


# Language translations for UI
TRANSLATIONS = {
    "en": {
        "dashboard": "Home",
        "assessment": "Assessment",
        "mood_tracker": "Mood Tracker",
        "recommendations": "Recommendations",
        "emergency": "Emergency",
        "logout": "Logout",
        "login": "Login",
        "register": "Register",
        "language": "Language",
        "english": "English",
        "kiswahili": "Kiswahili",
        "kikuyu": "Gĩkũyũ",
        "kalenjin": "Kalenjin",
        "good_day": "Good day",
        "todays_wellness": "Today's wellness snapshot",
        "last_login": "Last login",
        "never_logged_in": "No login recorded yet.",
        "login_count": "Total logins",
        "times_logged_in": "times logged in",
        "todays_journal": "Today's journal",
        "no_journal": "No journal entry saved today.",
        "todays_mood": "Today's mood",
        "current_streak": "Current streak",
        "days_of_checkins": "days of daily check-ins",
        "no_assessment": "No assessment yet.",
        "record_feelings": "Record your feelings in the mood tracker.",
        "mood_label": "mood",
        "stress_label": "stress",
        "weekly_mood_chart": "Weekly mood chart",
        "no_chart_data": "No mood check-ins yet. Add one in Mood Tracker to see your trend.",
        "single_chart_entry": "One check-in is shown. Add another to see a trend line.",
        "personalized_recommendations": "Recommendations based on your assessment",
        "based_on_latest_assessment": "Based on your latest assessment responses:",
        "recommendation_disclaimer": "These supportive suggestions are not a diagnosis or a substitute for professional care.",
        "priority_support": "Priority support",
        "kenya_emergency_numbers": "In Kenya, call 999 or 112 for immediate emergency help.",
        "no_personalized_recommendations": "Complete an assessment to get recommendations based on your answers.",
        "check_mental_health": "Check in with a quick mental health survey.",
        "share_mood": "Share your mood, stress, and reflections.",
        "get_support_tips": "Get support tips based on your latest score.",
        "access_support": "Access urgent Kenya-specific support contacts.",
        "features": "Features",
        "daily_checkins_description": "Log mood, stress, and note your feelings in one place.",
        "insightful_charts_description": "See your weekly trends and understand what affects your wellbeing.",
        "guided_recommendations_description": "Receive thoughtful tips based on your assessment score.",
        "emergency_resources_description": "Access urgent Kenya-specific contacts when you need them.",
        "box_description": "Box breathing helps calm your nervous system by creating a steady rhythm.",
        "four_seven_eight_description": "4-7-8 breathing activates your parasympathetic nervous system for deep relaxation.",
        "deep_description": "Deep breathing increases oxygen flow and reduces stress and anxiety.",
        "ai_assistant": "AngazaCare AI",
        "type_message": "Type a message...",
        "send": "Send",
        "ai_resting": "AngazaCare AI is resting, please try again",
        "footer": "AngazaCare © 2026 — Mental health support that feels personal.",
        "positive_mood": "Positive mood support",
        "mild_support": "Mild support",
        "moderate_support": "Moderate support",
        "severe_support": "Severe support",
        "email": "Email",
        "password": "Password",
        "name": "Name",
        "submit": "Submit",
        "create_account": "Create Account",
        "sign_in": "Sign In",
        "account_created": "Account created successfully. Please log in.",
        "email_registered": "Email already registered.",
        "invalid_credentials": "Invalid email or password.",
        "today_mood_recorded": "Today’s mood has been recorded.",
        "breathing_saved": "Breathing session saved! {duration} minutes of {technique}.",
        "logged_out": "You have been logged out.",
        "assessment_questions": PHQ9_QUESTIONS,
        "severity_minimal": "Minimal",
        "severity_mild": "Mild",
        "severity_moderate": "Moderate",
        "severity_severe": "Severe",
        "severity_minimal_message": "You are doing well. Keep supporting your mental health with healthy habits.",
        "severity_mild_message": "Some stress may be present. Light self-care and reflection can help.",
        "severity_moderate_message": "Consider sharing your feelings with a trusted person or professional.",
        "severity_severe_message": "Urgent support is recommended. Reach out to a mental health professional.",
        "translation_review_notice": "Draft translation: please seek support from a fluent speaker if anything is unclear.",
        "forgot_password": "Forgot Password?",
        "forgot_password_description": "Enter your account email. If it matches an account, reset instructions will be generated.",
        "request_reset_link": "Request Reset Link",
        "back_to_login": "Back to login",
        "choose_new_password": "Choose a New Password",
        "new_password": "New password",
        "confirm_new_password": "Confirm new password",
        "reset_password": "Reset Password",
        "password_policy_hint": "Use at least 12 characters with uppercase and lowercase letters, a number, and a special character.",
        "remember_me": "Remember me",
        "show_password": "Show password",
        "hide_password": "Hide password",
        "session_duration": "Session duration (minutes):",
        "box_breathing": "Box Breathing (4-4-4-4)",
        "four_seven_eight_breathing": "4-7-8 Breathing",
        "deep_breathing": "Deep Breathing (4-2-6)",
        "breathe_in": "Breathe In",
        "breathe_hold": "Hold",
        "breathe_out": "Breathe Out",
        "hospital_recommendation": "Hospital Recommendation",
        "find_nearby_hospitals": "Find nearby hospitals and clinics, or search by town or city.",
        "search_town_city": "Search town or city",
        "search_location": "Search location (town or city)",
        "search": "Search",
        "use_my_location": "Use my location",
        "requesting_location": "Requesting your location…",
        "search_hospitals_maps": "Search hospitals on Google Maps",
        "emergency_map_note": "If this is an emergency, call your local emergency number. In Kenya, call",
        "emergency_contacts_link": "emergency contacts",
        "clinic": "Clinic",
        "hospital": "Hospital",
        "get_directions": "Get directions",
        "directions_to_nearest": "Get directions to nearest",
        "away": "away",
        "facility_data_credit": "Facility data",
        "voice_chat_button": "Voice Chat",
        "voice_input_start": "Tap the mic and speak.",
        "voice_input_stop": "Stop listening",
        "voice_input_unsupported": "Voice input is not supported in this browser.",
        "voice_input_listening": "Listening...",
        "voice_input_unavailable": "Your browser cannot use voice input at the moment.",
        "voice_input_error": "There was an error with voice recognition. Please try again.",
        "type_message_empty": "Please type a message to send.",
        "chat_crisis_message": "I hear you and I care. Please contact a trusted person or emergency support now.",
        "chat_stress": "Stress can feel overwhelming. Try a short breathing break and contact someone you trust.",
        "chat_sleep": "Keep a regular sleep routine and wind down quietly before bed.",
        "chat_sad": "Feeling down is hard. Reach out to someone you trust and try one small activity that usually helps.",
        "chat_motivation": "Start with one very small goal and recognize progress, not perfection.",
        "chat_support": "Asking for support is a strong step. Tell someone you trust how you feel.",
        "chat_default_1": "I hear you and I am here to support you. Tell me more about how you feel today.",
        "chat_default_2": "You are not alone. We can find one small step forward together.",
        "chat_default_3": "Small steps can make a difference. Start with one breath and one simple action.",
        "contact_red_cross": "24/7 toll-free",
        "contact_befrienders": "Suicide prevention and support; calls, SMS, and WhatsApp",
        "contact_childline": "Youth and adolescents; toll-free",
        "contact_nacada": "Substance use and addiction",
        "contact_emergency": "Immediate emergency assistance",
        "hours_24_7": "24/7",
        "hours_unspecified": "Not specified",
        "available_hours": "Available",
        "score": "score",
        "finding_facilities": "Finding nearby hospitals and clinics…",
        "nearby_facilities_missing": "No nearby hospitals or clinics were found within 20 km.",
        "nearby_facilities_suggestion": "Try another town or city, or search nearby hospitals directly on Google Maps.",
        "nearby_facilities_heading": "Nearby hospitals and clinics",
        "nearby_facility_found": "nearby facility found.",
        "nearby_facilities_found": "nearby facilities found.",
        "nearby_facility_error": "Nearby facility search is unavailable.",
        "nearby_facility_failed": "Nearby facility search failed.",
        "location_finding": "Finding that location…",
        "location_failed": "Location lookup failed.",
        "location_unavailable": "Location is unavailable in this browser. Enter a town or city to search.",
        "location_permission_failed": "Location access was unavailable. Enter a town or city to find nearby care.",
        "location_manual_suggestion": "Enter a town or city, open Google Maps, or use the emergency contacts below.",
        "location_check_suggestion": "Check the spelling or enter a nearby town or city. You can also open Google Maps or the emergency contacts below.",
        "location_query_required": "Enter a town or city to search.",
        "clinic_distance": "km away",
        "facility_result_details": "{type} · {distance} km away",
        "close_chat": "Close chat window",
        "admin": "Admin",
        "open_ai_chat": "Open AngazaCare AI chat",
        "password_too_short": "Password must be at least 12 characters long.",
        "password_too_long": "Password must be no more than 128 characters long.",
        "password_complexity": "Include uppercase and lowercase letters, a number, and a special character.",
        "password_valid": "Password meets the requirements.",
        "password_mismatch": "The passwords do not match.",
        "or": "or",
        "or_view": "or view the",
        "mood_axis_label": "Mood (1-5)",
        "stress_axis_label": "Stress (1-10)",
        "admin_dashboard": "Admin Dashboard",
        "assigned_patients_list": "Assigned patients list:",
        "patient": "Patient",
        "consent": "Consent",
        "actions": "Actions",
        "yes": "Yes",
        "no": "No",
        "view": "View",
        "system_admin": "System Admin",
        "engagement_overview": "Engagement overview across patient accounts.",
        "active_users_30_days": "Active users (30 days)",
        "assessments_taken": "Assessments taken",
        "chatbot_interactions": "Chatbot interactions",
        "recent_chatbot_activity": "Recent chatbot activity",
        "scope_consent_notice": "Scope: {scope}. Message content is shown only with clinician-review consent.",
        "timestamp": "Timestamp",
        "user_session": "User / session",
        "prompt": "Prompt",
        "prompt_withheld": "Prompt withheld; no clinician-review consent.",
        "no_chat_activity": "No chatbot activity recorded.",
        "recent_assessment_results": "Recent assessment results",
        "assessment_consent_notice": "Responses and scores are available only when clinician-review consent is recorded.",
        "user_id": "User ID",
        "clinical_summary_responses": "Clinical summary / responses",
        "view_responses": "View responses",
        "score_responses_withheld": "Score and responses withheld; no clinician-review consent.",
        "no_assessments": "No assessments recorded.",
        "name_label": "Name:",
        "email_label": "Email:",
        "consent_review_label": "Consent to clinician review:",
        "ai_summary_withheld": "AI summary withheld; clinician-review consent is not recorded.",
        "record_access_denied": "Full record access denied by patient consent.",
        "ai_chat_history": "AI Chatbot History",
        "date_time": "Date & Time",
        "patient_question": "Patient Question",
        "ai_response": "AI Response",
        "response_withheld": "Response withheld; no clinician-review consent.",
        "no_chat_history": "No chat history available.",
        "assessments_phq9": "Assessments (PHQ-9)",
        "question": "Question",
        "answer": "Answer",
        "assessment_card": "Assessment - {date} | Score: {score} ({severity})",
        "no_assessments_available": "No assessments available.",
        "recent_mood_entries": "Recent Mood Entries",
        "date": "Date",
        "note": "Note",
        "not_available": "Not available",
        "ai_summary": "AI Summary",
        "summary_withheld": "Summary withheld; no clinician-review consent.",
        "scope_all_patients": "All patient accounts",
        "scope_assigned_patients": "Assigned patients",
        "app_title": "Mental Health Support",
        "chat_crisis_response": "I hear you and I care. Please reach Befrienders Kenya: {phone} (24/7, free). Contact someone you trust now.",
        "chat_empty_message": "Please enter a message.",
        "api_enter_town_city": "Enter a town or city name.",
        "api_location_unavailable": "Location lookup is temporarily unavailable. Please try again.",
        "api_no_matching_location": "No matching town or city was found. Try another search.",
        "api_invalid_location": "A valid location is required.",
        "api_facility_unavailable": "Nearby facility search is temporarily unavailable.",
        "api_facility_fallback": "Search for nearby hospitals directly on Google Maps, or use the Emergency page for support contacts.",
    },
    "sw": {
        "dashboard": "Mwanzo",
        "assessment": "Tathmini",
        "mood_tracker": "Kufuatilia Hali ya Jini",
        "recommendations": "Mapendekezo",
        "emergency": "Dharura",
        "logout": "Ondoka",
        "login": "Ingia",
        "register": "Jiandikishe",
        "language": "Lugha",
        "english": "English",
        "kiswahili": "Kiswahili",
        "kikuyu": "Gĩkũyũ",
        "kalenjin": "Kalenjin",
        "good_day": "Habari",
        "todays_wellness": "Picha ya afya yako leo",
        "last_login": "Uingiaji wa mwisho",
        "never_logged_in": "Hakuna uingiaji uliorekodiwa bado.",
        "login_count": "Jumla ya mara za kuingia",
        "times_logged_in": "mara umeingia",
        "todays_journal": "Jarida la leo",
        "no_journal": "Hakuna maandishi ya jarida yaliyohifadhiwa leo.",
        "todays_mood": "Hali ya jini leo",
        "current_streak": "Kamba ya sasa",
        "days_of_checkins": "siku za ukaguzi wa kila siku",
        "no_assessment": "Hakuna tathmini bado.",
        "record_feelings": "Rekodi hisia zako katika kufuatilia hali ya jini.",
        "mood_label": "hali ya jini",
        "stress_label": "msongo wa mawazo",
        "weekly_mood_chart": "Chati ya hali ya jini ya kila wiki",
        "no_chart_data": "Bado hakuna taarifa za hali yako. Rekodi hali yako ili kuona mwenendo.",
        "single_chart_entry": "Taarifa moja inaonyeshwa. Rekodi nyingine ili kuona mstari wa mwenendo.",
        "personalized_recommendations": "Mapendekezo kulingana na tathmini yako",
        "based_on_latest_assessment": "Kulingana na majibu yako ya tathmini ya hivi karibuni:",
        "recommendation_disclaimer": "Mapendekezo haya ni ya kukusaidia tu; si utambuzi wa ugonjwa wala mbadala wa huduma ya mtaalamu.",
        "priority_support": "Msaada wa kipaumbele",
        "kenya_emergency_numbers": "Nchini Kenya, piga 999 au 112 kwa msaada wa dharura wa haraka.",
        "no_personalized_recommendations": "Kamilisha tathmini ili kupata mapendekezo kulingana na majibu yako.",
        "check_mental_health": "Jaribu tathmini ya haraka ya afya ya akili.",
        "share_mood": "Shiriki hali yako, msongo wa mawazo, na mawazo yako.",
        "get_support_tips": "Pata vidokezo vya msaada kulingana na alama yako ya mwisho.",
        "access_support": "Fikirini rasilimali za dharura za Kenya.",
        "ai_assistant": "AI ya AngazaCare",
        "type_message": "Andika ujumbe...",
        "send": "Tuma",
        "ai_resting": "AI ya AngazaCare inapumzika, tafadhali jaribu tena",
        "footer": "AngazaCare © 2026 — Msaada wa afya ya akili ambao unajisikia binafsi.",
        "welcome_title": "Karibu AngazaCare",
        "welcome_headline": "Fuata hisia zako, shinda msongo, na upate msaada wa utulivu.",
        "welcome_description": "AngazaCare inakusaidia kuelewa afya yako ya hisia kwa zana za tathmini, ufuatiliaji wa hisia za kila siku, na mwongozo wa ustawi uliobinafsishwa.",
        "get_started": "Anza sasa",
        "feature_secure": "Ingia kwa usalama na dashibodi ya maendeleo yako binafsi",
        "feature_assessment": "Tathmini ya afya ya akili kwa mtindo wa PHQ-9",
        "feature_mood_logging": "Kurekodi hisia za kila siku na msongo wa mawazo kwa chati",
        "feature_support": "Mapendekezo ya msaada na mawasiliano ya dharura",
        "features": "Vipengele",
        "daily_checkins": "Ufuatiliaji wa kila siku",
        "daily_checkins_description": "Rekodi hisia, msongo, na hisia zako mahali pamoja.",
        "insightful_charts": "Chati za ufahamu",
        "insightful_charts_description": "Tazama mwelekeo wako wa wiki na elewa kinachokuathiri.",
        "guided_recommendations": "Mapendekezo yaliyoongozwa",
        "guided_recommendations_description": "Pata vidokezo vya busara kulingana na alama yako ya tathmini.",
        "emergency_resources": "Rasilimali za dharura",
        "emergency_resources_description": "Fikia mawasiliano ya haraka ya Kenya unayohitaji.",
        "login_title": "Ingia",
        "login_button": "Ingia",
        "need_account": "Unahitaji akaunti?",
        "register_here": "Jisajili hapa",
        "register_title": "Jiandikishe",
        "full_name": "Jina Kamili",
        "already_have_account": "Tayari una akaunti?",
        "login_here": "Ingia hapa",
        "assessment_title": "Tathmini ya Afya ya Akili",
        "assessment_description": "Jibu kila taarifa kulingana na jinsi ulivyohisi kwa wiki mbili zilizopita.",
        "not_at_all": "Sio kabisa",
        "several_days": "Siku kadhaa",
        "more_than_half": "Zaidi ya nusu ya siku",
        "nearly_every_day": "Karibu kila siku",
        "submit_assessment": "Tuma Tathmini",
        "mood_tracker_title": "Ufuatiliaji wa Hisia za Kila Siku",
        "mood_score": "Alama ya Hisia",
        "stress_level": "Kiwango cha Msongo",
        "journal_note": "Kumbukumbu ya jarida (hiari)",
        "note_placeholder": "Unajisikiaje leo?",
        "save_mood": "Hifadhi Hisia",
        "weekly_trend": "Mwelekeo wa wiki",
        "recommendations_title": "Mapendekezo ya Ustawi",
        "last_assessment_score": "Alama yako ya tathmini ya mwisho ilikuwa",
        "no_assessment_recorded": "Hakuna tathmini iliyorekodiwa bado.",
        "complete_assessment": "Kamilisha",
        "assessment_link": "tathmini",
        "to_receive_tips": "kupata vidokezo vilivyopewa mwili.",
        "emergency_support": "Msaada wa Dharura",
        "emergency_context": "Ikiwa unahitaji msaada wa haraka, wasiliana na mojawapo ya rasilimali hizi mara moja.",
        "phone": "Simu",
        "email_label": "Barua pepe",
        "call_now": "Piga sasa",
        "breathing_title": "Mazoezi ya Kupumua Yaliyoongozwa",
        "breathing_subtitle": "Pata utulivu kwa mbinu zilizoko za kupumua",
        "choose_technique": "Chagua mbinu:",
        "box_description": "Kupumua kwa box husaidia kutuliza mfumo wako wa neva kwa kuunda mdundo thabiti.",
        "four_seven_eight_description": "Kupumua 4-7-8 huchochea mfumo wako wa neva wa parasympathetic kwa utulivu wa kina.",
        "deep_description": "Kupumua kwa kina huongeza mtiririko wa oksijeni na kupunguza msongo na wasiwasi.",
        "start_session": "Anza Kikao",
        "pause": "Sitisha",
        "resume": "Endelea",
        "reset": "Weka upya",
        "save_session": "Hifadhi Kikao",
        "tips_breathing": "Vidokezo kwa Kupumua Bora",
        "tip_position": "Pata mkao mzuri, ukiwa umeketi au umelala",
        "tip_nose": "Pumua kwa pua ikiwa inawezekana",
        "tip_natural": "Usilazimishe pumzi yako — iiruke kwa asili",
        "tip_consistency": "Fanya mara kwa mara kwa matokeo bora",
        "tip_daily": "Tumia hii kila siku kwa uendeshaji bora wa msongo",
        "phase_in": "Pumua ndani",
        "phase_hold": "Shikilia",
        "phase_out": "Pumua nje",
        "call_action": "Piga sasa",
        "sign_in": "Ingia",
        "create_account": "Unda Akaunti",
        "mild_support": "Msaada sawa",
        "moderate_support": "Msaada wa kawaida",
        "severe_support": "Msaada muhimu",
        "email": "Barua pepe",
        "password": "Nenosiri",
        "name": "Jina",
        "submit": "Wasilisha",
        "create_account": "Unda Akaunti",
        "sign_in": "Ingia",
        "account_created": "Akaunti imefungua kwa mafanikio. Tafadhali ingia.",
        "email_registered": "Barua pepe tayari imejisajili.",
        "invalid_credentials": "Barua pepe au nenosiri batili.",
        "today_mood_recorded": "Hali yako ya hisia ya leo imehifadhiwa.",
        "breathing_saved": "Kikao cha kupumua kimehifadhiwa! Dakika {duration} za {technique}.",
        "logged_out": "Umebadilishwa kutoka.",
        "assessment_questions": PHQ9_QUESTIONS_SW,
    },
    "ki": {
        "dashboard": "Dashibodi",
        "assessment": "Tathmini",
        "mood_tracker": "Mũoyo wa ũmũthĩ",
        "recommendations": "Mataarĩro",
        "emergency": "Dharura",
        "logout": "Uma",
        "login": "Ingia",
        "register": "Jisajili",
        "good_day": "Mũthenya mwega",
        "todays_wellness": "Ũgima wa ũmũthĩ",
        "last_assessment": "Tathmini ya mũthia",
        "todays_mood": "Mũoyo wa ũmũthĩ",
        "current_streak": "Mĩthenya ya kuandika mũoyo",
        "days_of_checkins": "mĩthenya ya check-in",
        "no_assessment": "Gũtirĩ tathmini rĩu.",
        "record_feelings": "Andika ũrĩa wĩĩhĩtĩte kwa mũoyo.",
        "mood_label": "mũoyo",
        "stress_label": "stress",
        "weekly_mood_chart": "Chati ya mũoyo ya wiki",
        "assessment_title": "Tathmini ya ũgima wa mũoyo",
        "mood_tracker_title": "Ũhoro wa mũoyo wa mũthenya",
        "mood_score": "Alama ya mũoyo",
        "stress_level": "Kĩero kya stress",
        "journal_note": "Ndeto cia kuandika (ũrĩa wendete)",
        "save_mood": "Hifadhi mũoyo",
        "kikuyu": "Gĩkũyũ",
        "kalenjin": "Kalenjin",
        "english": "Gĩthũngũ",
        "kiswahili": "Kiswahili",
        "language": "Rũthiomi",
        "no_chart_data": "Gũtirĩ check-in cia mũoyo. Andika mũoyo waku nĩguo ũone ũrĩa ũgũthii.",
        "single_chart_entry": "Check-in imwe nĩyo ĩrĩ ho. Andika ĩngĩ nĩguo ũone ũrĩa ũgũthii.",
        "personalized_recommendations": "Mataarĩro kũringana na tathmini yaku",
        "based_on_latest_assessment": "Kũringana na macookio maku ma tathmini ya mũthia:",
        "recommendation_disclaimer": "Mataarĩro maya nĩ ma gũteithia; matirĩ ũtambuzi kana gũcookereria ũteithio wa mũrũgamĩrĩri.",
        "priority_support": "Ũteithio wa mbere",
        "kenya_emergency_numbers": "Thĩinĩ wa Kenya, itana 999 kana 112 nĩguo ũpate ũteithio wa haraka.",
        "no_personalized_recommendations": "Thĩnia tathmini nĩguo ũpate mataarĩro kũringana na macookio maku.",
        "check_mental_health": "Thĩnia tathmini ya haraka ya ũgima wa mũoyo.",
        "share_mood": "Ũganĩre mũoyo, stress na ũhoro waku.",
        "get_support_tips": "Pata mawoni ma ũteithio kũringana na alama yaku.",
        "access_support": "Pata namba cia ũteithio wa haraka thĩinĩ wa Kenya.",
        "features": "Ũrĩa AngazaCare ĩteithagia",
        "daily_checkins_description": "Andika mũoyo, stress na ũrĩa wĩĩhĩtĩte handũ hamwe.",
        "insightful_charts_description": "Ona ũrĩa mĩthenya yaku ya wiki ĩrĩ na ũmenye kĩrĩa gĩkũgĩa.",
        "guided_recommendations_description": "Pata mawoni ma ũteithio kũringana na alama ya tathmini yaku.",
        "emergency_resources_description": "Pata namba cia ũteithio wa haraka thĩinĩ wa Kenya rĩrĩa ũcaria.",
        "box_description": "Gũtaha rũhiũ rwa ndigiri kũrutwo na gũcookererwo gũteithagia mwĩrĩ waku gũtulia.",
        "four_seven_eight_description": "Gũtaha 4-7-8 gũteithagia mwĩrĩ gũtulia na kũhũthĩra.",
        "deep_description": "Gũtaha na hinya mũnene gũongerera oxygen na gũtua stress na kĩeha.",
        "ai_assistant": "AngazaCare AI",
        "type_message": "Andika ũhoro...",
        "send": "Tũma",
        "ai_resting": "AngazaCare AI ĩrĩ kĩeha; ũringie rĩngĩ.",
        "footer": "AngazaCare © 2026 — ũteithio wa ũgima wa mũoyo wa mũndũ.",
        "positive_mood": "Ũteithio wa mũoyo mwega",
        "mild_support": "Ũteithio mũnini",
        "moderate_support": "Ũteithio wa gatagati",
        "severe_support": "Ũteithio wa hinya",
        "email": "Barua pepe",
        "password": "Nyũmba ya hitho",
        "name": "Rĩtwa",
        "submit": "Tũma",
        "create_account": "Gĩa account",
        "sign_in": "Ingia",
        "account_created": "Account ĩgĩtwarwo wega. Tafadhali ingia.",
        "email_registered": "Barua pepe ĩrĩ na account.",
        "invalid_credentials": "Barua pepe kana nyũmba ya hitho nĩ njega.",
        "today_mood_recorded": "Mũoyo waku wa ũmũthĩ nĩũgĩtwarwo.",
        "breathing_saved": "Gũtaha nĩgũtwarwo! Ndagĩka {duration} cia {technique}.",
        "logged_out": "Nĩũmũrĩte.",
        "welcome_title": "Ũgĩe AngazaCare",
        "welcome_headline": "Rora mũoyo waku, tua stress, na ũpate ũteithio wa gũtulia.",
        "welcome_description": "AngazaCare ĩgũteithagia kwĩmenya ũgima wa mũoyo na tathmini, kwandika mũoyo wa mũthenya na mawoni ma ũteithio.",
        "get_started": "Tandika",
        "feature_secure": "Ingia na hinya na ũone ũrĩa ũgũthii",
        "feature_assessment": "Tathmini ya ũgima wa mũoyo ya mũthemba wa PHQ-9",
        "feature_mood_logging": "Andika mũoyo na stress ya mũthenya na chati",
        "feature_support": "Mawoni ma ũteithio na namba cia haraka",
        "daily_checkins": "Kuandika mũoyo wa mũthenya",
        "insightful_charts": "Chati cia kwĩmenya",
        "guided_recommendations": "Mawoni ma ũteithio",
        "emergency_resources": "Namba cia ũteithio wa haraka",
        "login_title": "Ingia",
        "login_button": "Ingia",
        "need_account": "Ũrĩ na haja ya account?",
        "register_here": "Jisajili haha",
        "register_title": "Jisajili",
        "full_name": "Rĩtwa rĩothe",
        "already_have_account": "Ũrĩ na account rĩu?",
        "login_here": "Ingia haha",
        "assessment_description": "Cookia ciugo ici kũringana na ũrĩa wĩhĩtĩte mĩthenya ĩrĩa ĩrĩ mbere.",
        "not_at_all": "Gũtirĩ na kahinda",
        "several_days": "Mĩthenya mĩingĩ",
        "more_than_half": "Mĩthenya ĩrĩa ĩngĩ",
        "nearly_every_day": "O mũthenya kana hakuhĩ",
        "submit_assessment": "Tũma tathmini",
        "note_placeholder": "Ũrĩĩhĩtĩte atĩa ũmũthĩ?",
        "weekly_trend": "Ũrĩa wiki ĩgũthii",
        "recommendations_title": "Mawoni ma ũgima",
        "last_assessment_score": "Alama yaku ya tathmini ya mũthia nĩ",
        "no_assessment_recorded": "Gũtirĩ tathmini ĩandĩkĩtwo.",
        "complete_assessment": "Thĩnia",
        "assessment_link": "tathmini",
        "to_receive_tips": "nĩguo ũpate mawoni.",
        "emergency_support": "Ũteithio wa haraka",
        "emergency_context": "Angĩkorwo ũcaria ũteithio wa haraka, itana namba ĩmwe ya haha rĩu.",
        "phone": "Ũhoro wa thimũ",
        "email_label": "Barua pepe",
        "call_now": "Itana rĩu",
        "breathing_title": "Gũtaha na kũtongoria",
        "breathing_subtitle": "Tua mũoyo na njira cia gũtaha",
        "choose_technique": "Hũthũra njira:",
        "start_session": "Tandika",
        "pause": "Tiga kahinda",
        "resume": "Cookeria",
        "reset": "Tandika rĩngĩ",
        "save_session": "Hifadhi gũtaha",
        "tips_breathing": "Mawoni ma gũtaha wega",
        "tip_position": "Ikara kana ũkome handũ hauga",
        "tip_nose": "Taha na ihu angĩkorwo nĩũgũkĩrĩra",
        "tip_natural": "Ndũkahinyirie gũtaha; tiga gucooka na ũrĩa wĩrĩ",
        "tip_consistency": "Hũthũra njira ĩno rĩngĩ na rĩngĩ",
        "tip_daily": "Hũthũra mũthenya ũcio nĩguo ũtue stress",
        "phase_in": "Taha thĩinĩ",
        "phase_hold": "Gĩrĩria",
        "phase_out": "Taha nja",
        "assessment_questions": [
            "Gũtirĩ kĩyo kana gĩkeno gĩa gwĩka maũndũ.",
            "Kwĩigua ũrĩ na kĩeha kana ũtarĩ na mwĩhoko.",
            "Kũũra kana kũraha mũno.",
            "Kwĩigua ũrĩ mũrũgi kana ũtarĩ na hinya.",
            "Gũthĩnja kana kũrĩa mũno.",
            "Kwĩigua ũrĩ mũũru kana ũtigĩrĩtwo nĩwe kana andũ aku.",
            "Gũtinya gũtirĩ kũhota gũthikĩria, ta gũthoma kana gũtazama.",
            "Kũenda kana kũaria na ũhoro mũnini mũno, kana gũtũũra na gũthĩnjika.",
            "Maaro ma gũkua kana gwĩtũma ũũru.",
            "Mathĩna maya nĩmamenyithagia atĩa wĩra-inĩ, mũciĩ kana na andũ angĩ?",
        ],
        "severity_minimal": "Nĩ mũnini mũno",
        "severity_mild": "Nĩ mũnini",
        "severity_moderate": "Nĩ gatagati",
        "severity_severe": "Nĩ mũnene",
        "severity_minimal_message": "Ũrĩ wega. Endelea kwĩmenyerera na mĩtugo mĩega ya ũgima wa mũoyo.",
        "severity_mild_message": "Ũrĩ na stress mũnini. Kwĩmenyerera na gwĩciria nĩgũteithagia.",
        "severity_moderate_message": "Ũgĩrĩirwo nĩ kũganĩra ũrĩa wĩĩhĩtĩte na mũndũ ũrĩ na mwĩhoko kana mũteithia.",
        "severity_severe_message": "Ũteithio wa haraka nĩũgĩrĩirwo. Ũganĩre na mũteithia wa ũgima wa mũoyo.",
        "translation_review_notice": "Ũhoro ũyũ nĩ wa kũringĩrĩra; ũhũthĩre ũteithio wa mũndũ ũmenyete Gĩkũyũ wega angĩkorwo ũhoro nĩ mũnyitĩ.",
        "forgot_password": "Wĩrĩgĩrwo nĩ password?",
        "forgot_password_description": "Andika barua pepe ya account yaku nĩguo ũpokee njira ya gũcookereria password.",
        "request_reset_link": "Caria njira ya gũcookereria",
        "back_to_login": "Cookeria kũingĩra",
        "choose_new_password": "Hũthũra password njerũ",
        "new_password": "Password njerũ",
        "confirm_new_password": "Hũthũra rĩngĩ password njerũ",
        "reset_password": "Cookereria password",
        "password_policy_hint": "Hũthũra ciugo ikũmi na igĩrĩ kana nyingĩ, cia ndeto nene na nini, namba na kimenyithia.",
        "remember_me": "Ndĩrĩgĩrĩre",
        "show_password": "Onyesha password",
        "hide_password": "Hitha password",
        "session_duration": "Kahinda ka kikao (ndagĩka):",
        "box_breathing": "Gũtaha kwa box (4-4-4-4)",
        "four_seven_eight_breathing": "Gũtaha kwa 4-7-8",
        "deep_breathing": "Gũtaha na hinya (4-2-6)",
        "breathe_in": "Taha thĩinĩ",
        "breathe_hold": "Gĩrĩria",
        "breathe_out": "Taha nja",
        "hospital_recommendation": "Mawoni ma hau wa ũgima",
        "find_nearby_hospitals": "Rora hau ha ũgima hakuhĩ kana cārĩa na itũũra.",
        "search_town_city": "Caria itũũra kana itũũra-inĩ nene",
        "search_location": "Caria handũ (itũũra kana itũũra-inĩ nene)",
        "search": "Caria",
        "use_my_location": "Hũthũra handũ ndĩ ho",
        "requesting_location": "Nĩtũcaria handũ ũrĩ ho…",
        "search_hospitals_maps": "Caria hau ha ũgima Google Maps",
        "emergency_map_note": "Angĩkorwo nĩ dharura, itana namba ya haraka ya gĩcigo gĩaku. Thĩinĩ wa Kenya, itana",
        "emergency_contacts_link": "namba cia dharura",
        "clinic": "Hau ha ũgima",
        "hospital": "Hũspitali",
        "get_directions": "Ona njira",
        "directions_to_nearest": "Ona njira ya hau hakuhĩ",
        "away": "kũraihu",
        "facility_data_credit": "Ũhoro wa hau ha ũgima",
        "voice_chat_button": "Ũhoro na ngui",
        "voice_input_start": "Thĩnia gĩcoko na ũaria.",
        "voice_input_stop": "Tiga gũthikĩria",
        "voice_input_unsupported": "Gũaria na ngui gũtihũthĩrĩtwo nĩ browser ĩno.",
        "voice_input_listening": "Nĩtũthikĩria...",
        "voice_input_unavailable": "Browser yaku ndĩhota gũhũthĩra gũaria na ngui rĩu.",
        "voice_input_error": "Nĩgwĩkĩte mathĩna na gũthikĩria ngui. Ringia rĩngĩ.",
        "type_message_empty": "Andika ũhoro wa gũtũma.",
        "chat_crisis_message": "Nĩngũigua na nĩndĩ na ũrũrĩri. Ta ũmũndũ ũrĩ na mwĩhoko kana ũteithio wa dharura rĩu.",
        "chat_stress": "Stress nĩĩhota kũgũtũma wĩigue ũrĩ na hinya mũnene. Taha na ũtuli na ũganĩre na mũndũ ũrĩ na mwĩhoko.",
        "chat_sleep": "Rĩrĩra na ũrarĩre ihinda rĩmwe o mũthenya, ũtue ũhoro mbere ya kũraha.",
        "chat_sad": "Kwĩigua na kĩeha nĩ kũũru. Ũganĩre na mũndũ ũrĩ na mwĩhoko na ũhũthũre kĩndũ kĩnini gĩgũteithagia.",
        "chat_motivation": "Tandika na gĩtĩĩ kĩnini na ũmenye mĩthia yaku; ndũkenie kwĩthomera.",
        "chat_support": "Caria ũteithio nĩ kĩndũ kĩa hinya. Ũganĩre na mũndũ ũrĩ na mwĩhoko ũrĩa wĩĩhĩtĩte.",
        "chat_default_1": "Nĩngũigua na nĩndĩ haha nĩguo ngũteithie. Ũganĩre ũrĩa wĩĩhĩtĩte ũmũthĩ.",
        "chat_default_2": "Ndũrĩ weka. Tũhote kuona gĩtĩĩ kĩnini gĩa gũthii mbere hamwe.",
        "chat_default_3": "Mĩthia mĩnini nĩĩhota gũteithia. Tandika na gũtaha na kĩndũ kĩmwe kĩnini.",
        "contact_red_cross": "Thimũ ya free ihinda rĩothe",
        "contact_befrienders": "Kũhũthĩra na ũteithio wa andũ arĩa marĩ na mathĩna ma kwĩthũra; thimũ na WhatsApp",
        "contact_childline": "Ana na thiritũ; thimũ ya free",
        "contact_nacada": "Kũhũthĩra maũndũ ma ũndũ na gũtũũra na ũhũthĩri",
        "contact_emergency": "Ũteithio wa haraka",
        "hours_24_7": "Ihinda rĩothe",
        "hours_unspecified": "Gũtirĩ ũhoro",
        "available_hours": "Ihinda rĩa kũhũthĩra",
        "daily_quotes": [
            "Kĩndũ kĩnini gĩa mũthenya nĩgĩhotaga gũtua mabadiliko mega.",
            "Kũhurũka nĩ gĩcunjĩ kĩa gũthii mbere, ti kĩndũ kĩa kwĩtigĩra.",
            "Ũrĩ na hinya mũnene gũkĩra ũrĩa ũkũrora.",
            "Rora maũndũ mega o na mĩthenya ĩrĩa ĩrĩ mĩũru.",
            "Taha na ũtuli. Ũgĩrĩirwo nĩ gũtulia.",
            "Kwĩiguĩra tha nĩ kĩndũ kĩa hinya.",
            "Ũmũthĩ nĩ ihinda rĩa gũteithia ũgima waku.",
            "Ihinda rĩothe nĩ rĩa kũtandika rĩngĩ na gũthii mbere.",
            "Hũthũra ũmenyo waku na ũhonokie ũrĩa wĩĩhĩtĩte.",
            "Mĩtugo mĩega itandika na kĩrĩa kĩmwe kĩa kũmenyerera.",
        ],
        "score": "alama",
        "finding_facilities": "Nĩtũcaria hau ha ũgima na clinic hakuhĩ…",
        "nearby_facilities_missing": "Gũtirĩ hau ha ũgima kana clinic hakuhĩ thĩinĩ wa kilometer 20.",
        "nearby_facilities_suggestion": "Ringia na itũũra rĩngĩ kana cārĩa hau ha ũgima na Google Maps.",
        "nearby_facilities_heading": "Hau ha ũgima na clinic hakuhĩ",
        "nearby_facility_found": "hau ha ũgima hakuhĩ nĩho honekete.",
        "nearby_facilities_found": "hau ha ũgima hakuhĩ nĩho hondekete.",
        "nearby_facility_error": "Gũcaria hau ha ũgima ndigũtũmĩka rĩu.",
        "nearby_facility_failed": "Gũcaria hau ha ũgima nĩgũkĩte mathĩna.",
        "location_finding": "Nĩtũcaria handũ hau…",
        "location_failed": "Gũcaria handũ nĩgũkĩte mathĩna.",
        "location_unavailable": "Browser ĩno ndĩhota kũmenya handũ ũrĩ ho. Andika itũũra nĩguo ũcārĩe.",
        "location_permission_failed": "Gũtũma handũ ũrĩ ho ndigũkĩte. Andika itũũra nĩguo ũcārĩe ũteithio wa hakuhĩ.",
        "location_manual_suggestion": "Andika itũũra, rora Google Maps, kana hũthũra namba cia dharura iria thĩinĩ.",
        "location_check_suggestion": "Rora ũrĩa wandĩkĩte kana andika itũũra rĩa hakuhĩ. Ũhota kũrora Google Maps kana namba cia dharura.",
        "location_query_required": "Andika itũũra nĩguo ũcārĩe.",
        "clinic_distance": "km kũraihu",
        "facility_result_details": "{type} · {distance} km kũraihu",
        "close_chat": "Hinga ũhoro",
        "admin": "Mũrũgamĩrĩri",
        "open_ai_chat": "Hungũra ũhoro na AngazaCare AI",
        "password_too_short": "Password ĩgĩrĩirwo nĩ ciugo ikũmi na igĩrĩ kana nyingĩ.",
        "password_too_long": "Password ndĩgĩrĩrwo gũkĩra ciugo 128.",
        "password_complexity": "Hũthũra ndeto nene na nini, namba na kimenyithia.",
        "password_valid": "Password yaku nĩ njega.",
        "password_mismatch": "Password ici itigana.",
        "or": "kana",
        "or_view": "kana rora",
        "mood_axis_label": "Mũoyo (1-5)",
        "stress_axis_label": "Stress (1-10)",
        "admin_dashboard": "Dashibodi ya mũrũgamĩrĩri",
        "assigned_patients_list": "Andũ arĩa matũmĩrĩtwo:",
        "patient": "Mũndũ",
        "consent": "Ũtũmĩrĩri",
        "actions": "Maũndũ ma gwĩka",
        "yes": "Ĩĩ",
        "no": "Aca",
        "view": "Rora",
        "system_admin": "Mũrũgamĩrĩri wa system",
        "engagement_overview": "Ũrĩa andũ meehokete account cia arwari.",
        "active_users_30_days": "Andũ arĩa matũmĩrĩte (mĩthenya 30)",
        "assessments_taken": "Tathmini iria ciathĩniirwo",
        "chatbot_interactions": "Ũhoro wa chatbot",
        "recent_chatbot_activity": "Ũhoro wa chatbot wa mũthia",
        "scope_consent_notice": "Handũ: {scope}. Ũhoro ũrĩa ũtũmĩtwo ũonekaga angĩkorwo nĩũtũmĩrĩtwo.",
        "timestamp": "Ihinda",
        "user_session": "Mũndũ / session",
        "prompt": "Ũhoro wa mbere",
        "prompt_withheld": "Ũhoro ũhithĩtwo; gũtirĩ ũtũmĩrĩri wa mũrũgamĩrĩri.",
        "no_chat_activity": "Gũtirĩ ũhoro wa chatbot ũandĩkĩtwo.",
        "recent_assessment_results": "Macookio ma tathmini ma mũthia",
        "assessment_consent_notice": "Macookio na alama nĩ ciĩonekaga angĩkorwo nĩũtũmĩrĩtwo nĩ mũndũ.",
        "user_id": "ID ya mũndũ",
        "clinical_summary_responses": "Ũhoro wa ũgima / macookio",
        "view_responses": "Rora macookio",
        "score_responses_withheld": "Alama na macookio nĩmahithĩtwo; gũtirĩ ũtũmĩrĩri.",
        "no_assessments": "Gũtirĩ tathmini ĩandĩkĩtwo.",
        "name_label": "Rĩtwa:",
        "email_label": "Barua pepe:",
        "consent_review_label": "Ũtũmĩrĩri wa gũrora nĩ mũrũgamĩrĩri:",
        "ai_summary_withheld": "Ũhoro wa AI nĩũhithĩtwo; gũtirĩ ũtũmĩrĩri.",
        "record_access_denied": "Gũtũmĩrĩra rekodi ciothe gũtiganĩtwo nĩ mwĩhoko wa mũndũ.",
        "ai_chat_history": "Ũhoro wa AngazaCare AI",
        "date_time": "Mũthenya na ihinda",
        "patient_question": "Ũria wa mũndũ",
        "ai_response": "Macookio ma AI",
        "response_withheld": "Macookio nĩmahithĩtwo; gũtirĩ ũtũmĩrĩri.",
        "no_chat_history": "Gũtirĩ ũhoro wa chatbot.",
        "assessments_phq9": "Tathmini (PHQ-9)",
        "question": "Ũria",
        "answer": "Macookio",
        "assessment_card": "Tathmini - {date} | Alama: {score} ({severity})",
        "no_assessments_available": "Gũtirĩ tathmini.",
        "recent_mood_entries": "Maũndũ ma mũoyo ma mũthia",
        "date": "Mũthenya",
        "note": "Ũhoro",
        "not_available": "Gũtirĩ",
        "ai_summary": "Ũhoro wa AI",
        "summary_withheld": "Ũhoro ũhithĩtwo; gũtirĩ ũtũmĩrĩri wa mũrũgamĩrĩri.",
        "scope_all_patients": "Account cia andũ arĩa marĩ na mathĩna",
        "scope_assigned_patients": "Andũ arĩa matũmĩrĩtwo",
        "app_title": "Ũteithio wa ũgima wa mũoyo",
        "chat_crisis_response": "Nĩngũigua na nĩndĩ na ũrũrĩri. Itana Befrienders Kenya: {phone} (ihinda rĩothe, free). Ũganĩre na mũndũ ũrĩ na mwĩhoko rĩu.",
        "chat_empty_message": "Andika ũhoro mbere ya gũtũma.",
        "api_enter_town_city": "Andika itũũra kana itũũra-inĩ nene.",
        "api_location_unavailable": "Gũcaria handũ ndigũtũmĩka rĩu. Ringia rĩngĩ.",
        "api_no_matching_location": "Gũtirĩ itũũra rĩoneka. Ringia kũcaria rĩngĩ.",
        "api_invalid_location": "Hũthũra handũ harĩ ma.",
        "api_facility_unavailable": "Gũcaria hau ha ũgima hakuhĩ ndigũtũmĩka rĩu.",
        "api_facility_fallback": "Caria hau ha ũgima na Google Maps kana hũthũra namba cia dharura.",
    },
    "kln": {
        "dashboard": "Dashboard",
        "assessment": "Assessment",
        "mood_tracker": "Mood Tracker",
        "recommendations": "Recommendations",
        "emergency": "Emergency",
        "logout": "Logout",
        "login": "Login",
        "register": "Register",
        "good_day": "Chamgei",
        "todays_wellness": "Laleet",
        "last_assessment": "Last assessment",
        "todays_mood": "Today's mood",
        "current_streak": "Current streak",
        "days_of_checkins": "days of daily check-ins",
        "no_assessment": "No assessment yet.",
        "record_feelings": "Record your feelings in the mood tracker.",
        "mood_label": "mood",
        "stress_label": "stress",
        "weekly_mood_chart": "Weekly mood chart",
        "assessment_title": "Mental Health Assessment",
        "mood_tracker_title": "Daily Mood Tracker",
        "mood_score": "Mood Score",
        "stress_level": "Stress level",
        "journal_note": "Journal note (optional)",
        "save_mood": "Save Mood",
        "kikuyu": "Gĩkũyũ",
        "kalenjin": "Kalenjin",
        "english": "English",
        "kiswahili": "Kiswahili",
        "language": "Language",
        "no_chart_data": "No mood check-ins yet. Add one in Mood Tracker to see your trend.",
        "single_chart_entry": "One check-in is shown. Add another to see a trend line.",
        "personalized_recommendations": "Macheetab koigenyini agobo assessment",
        "based_on_latest_assessment": "Kogeei nebo assessment nebo tugul:",
        "recommendation_disclaimer": "Macheetab tugul ko tugul nebo konyit; ma ko diagnosis ak ma ko substitute nebo professional care.",
        "priority_support": "Konyit nebo tai",
        "kenya_emergency_numbers": "Eng Kenya, kole 999 nebo 112 agobo immediate emergency help.",
        "no_personalized_recommendations": "Complete assessment agobo macheetab koigenyini agobo answers eng.",
        "check_mental_health": "Check-in nebo mental health survey nebo tugul.",
        "share_mood": "Share mood, stress, ak reflections nebo inendet.",
        "get_support_tips": "Get support tips agobo score nebo assessment nebo tugul.",
        "access_support": "Access urgent Kenya-specific support contacts.",
        "features": "Features",
        "daily_checkins_description": "Log mood, stress, ak note feelings nebo inendet eng place nebo tugul.",
        "insightful_charts_description": "See weekly trends nebo tugul ak understand what affects wellbeing nebo tugul.",
        "guided_recommendations_description": "Receive supportive tips agobo assessment score nebo tugul.",
        "emergency_resources_description": "Access urgent Kenya-specific contacts nebo tugul when need it.",
        "box_description": "Box breathing konyi nervous system nebo tugul agobo steady rhythm.",
        "four_seven_eight_description": "4-7-8 breathing konyi parasympathetic nervous system nebo tugul agobo deep relaxation.",
        "deep_description": "Deep breathing increases oxygen ak reduces stress ak anxiety.",
        "ai_assistant": "AngazaCare AI",
        "type_message": "Type message...",
        "send": "Send",
        "ai_resting": "AngazaCare AI rest, try again",
        "footer": "AngazaCare © 2026 — mental wellness support nebo inendet.",
        "positive_mood": "Positive mood support",
        "mild_support": "Mild support",
        "moderate_support": "Moderate support",
        "severe_support": "Severe support",
        "email": "Email",
        "password": "Password",
        "name": "Name",
        "submit": "Submit",
        "create_account": "Create account",
        "sign_in": "Sign in",
        "account_created": "Account created successfully. Please log in.",
        "email_registered": "Email already registered.",
        "invalid_credentials": "Invalid email or password.",
        "today_mood_recorded": "Mood nebo tugul saved.",
        "breathing_saved": "Breathing session saved! {duration} minutes of {technique}.",
        "logged_out": "Logged out.",
        "welcome_title": "Welcome AngazaCare",
        "welcome_headline": "Track mood, manage stress, and find calm support.",
        "welcome_description": "AngazaCare helps understand emotional health with assessments, daily mood tracking, and wellness guidance.",
        "get_started": "Get started",
        "feature_secure": "Secure login and personal progress dashboard",
        "feature_assessment": "PHQ-9 style mental health assessment",
        "feature_mood_logging": "Daily mood and stress logging with charts",
        "feature_support": "Support recommendations and emergency contacts",
        "daily_checkins": "Daily check-ins",
        "insightful_charts": "Insightful charts",
        "guided_recommendations": "Guided recommendations",
        "emergency_resources": "Emergency resources",
        "login_title": "Login",
        "login_button": "Login",
        "need_account": "Need an account?",
        "register_here": "Register here",
        "register_title": "Register",
        "full_name": "Full name",
        "already_have_account": "Already have an account?",
        "login_here": "Login here",
        "assessment_description": "Answer each statement based on how you felt during the past two weeks.",
        "not_at_all": "Not at all",
        "several_days": "Several days",
        "more_than_half": "More than half the days",
        "nearly_every_day": "Nearly every day",
        "submit_assessment": "Submit assessment",
        "note_placeholder": "How are you feeling today?",
        "weekly_trend": "Weekly trend",
        "recommendations_title": "Wellness recommendations",
        "last_assessment_score": "Your last assessment score was",
        "no_assessment_recorded": "No assessment recorded yet.",
        "complete_assessment": "Complete",
        "assessment_link": "assessment",
        "to_receive_tips": "to receive tips.",
        "emergency_support": "Emergency support",
        "emergency_context": "If you need urgent help, contact one of these resources now.",
        "phone": "Phone",
        "email_label": "Email",
        "call_now": "Call now",
        "breathing_title": "Guided breathing exercise",
        "breathing_subtitle": "Find calm with guided breathing techniques",
        "choose_technique": "Choose a technique:",
        "start_session": "Start session",
        "pause": "Pause",
        "resume": "Resume",
        "reset": "Reset",
        "save_session": "Save session",
        "tips_breathing": "Tips for better breathing",
        "tip_position": "Find a comfortable position, sitting or lying down",
        "tip_nose": "Breathe through your nose if possible",
        "tip_natural": "Do not force your breath; let it flow naturally",
        "tip_consistency": "Practice consistently for best results",
        "tip_daily": "Use this daily for better stress management",
        "phase_in": "Breathe in",
        "phase_hold": "Hold",
        "phase_out": "Breathe out",
        "assessment_questions": [
            "Maat nebo konyit ak kogenyini nebo tugul eng ichek tugul.",
            "Komei chito tugul, chito nebo konyit ak komie.",
            "Maat nebo sleeping ak maat nebo sleeping eng tugul.",
            "Komei chito nebo konyit ak maat nebo strength.",
            "Maat nebo appetite ak maat nebo food eng tugul.",
            "Komei chito nebo konyit agobo inendet ak family nebo inendet.",
            "Maat nebo concentration agobo reading ak television.",
            "Komei chito tugul eng slow ak restless eng tugul.",
            "Thoughts agobo death ak hurting inendet.",
            "How difficult are these problems eng work, home ak people?",
        ],
        "severity_minimal": "Maat chito tugul",
        "severity_mild": "Maat chito eng little",
        "severity_moderate": "Maat chito eng middle",
        "severity_severe": "Maat chito eng many",
        "severity_minimal_message": "Inendet ko tugul. Continue healthy habits agobo mental wellness.",
        "severity_mild_message": "Stress agobo inendet. Self-care ak reflections konyi.",
        "severity_moderate_message": "Share feelings agobo trusted person ak professional.",
        "severity_severe_message": "Urgent support nebo inendet. Reach out agobo mental-health professional.",
        "translation_review_notice": "Draft translation: Nandi fluent-speaker and clinical review needed before relying on this wording.",
        "forgot_password": "Forget password?",
        "forgot_password_description": "Enter email nebo account agobo reset instructions.",
        "request_reset_link": "Request reset link",
        "back_to_login": "Back to login",
        "choose_new_password": "Choose new password",
        "new_password": "New password",
        "confirm_new_password": "Confirm new password",
        "reset_password": "Reset password",
        "password_policy_hint": "Use 12 or more characters with uppercase, lowercase, number, and special character.",
        "remember_me": "Remember me",
        "show_password": "Show password",
        "hide_password": "Hide password",
        "session_duration": "Session duration (minutes):",
        "box_breathing": "Box breathing (4-4-4-4)",
        "four_seven_eight_breathing": "4-7-8 breathing",
        "deep_breathing": "Deep breathing (4-2-6)",
        "breathe_in": "Breathe in",
        "breathe_hold": "Hold",
        "breathe_out": "Breathe out",
        "hospital_recommendation": "Hospital recommendation",
        "find_nearby_hospitals": "Find nearby hospitals and clinics, or search by town or city.",
        "search_town_city": "Search town or city",
        "search_location": "Search location (town or city)",
        "search": "Search",
        "use_my_location": "Use my location",
        "requesting_location": "Requesting your location…",
        "search_hospitals_maps": "Search hospitals on Google Maps",
        "emergency_map_note": "If emergency, call local emergency number. In Kenya call",
        "emergency_contacts_link": "emergency contacts",
        "clinic": "Clinic",
        "hospital": "Hospital",
        "get_directions": "Get directions",
        "directions_to_nearest": "Get directions to nearest",
        "away": "away",
        "facility_data_credit": "Facility data",
        "voice_chat_button": "Voice chat",
        "voice_input_start": "Tap mic and speak.",
        "voice_input_stop": "Stop listening",
        "voice_input_unsupported": "Voice input is not supported in this browser.",
        "voice_input_listening": "Listening...",
        "voice_input_unavailable": "Browser cannot use voice input at the moment.",
        "voice_input_error": "Voice recognition error. Please try again.",
        "type_message_empty": "Please type a message to send.",
        "chat_crisis_message": "I hear you and care. Contact a trusted person or emergency support now.",
        "chat_stress": "Stress can feel overwhelming. Try a short breathing break and talk to someone you trust.",
        "chat_sleep": "Keep a regular sleep routine and wind down quietly before bed.",
        "chat_sad": "Feeling down is hard. Reach out to someone you trust and try one activity that helps.",
        "chat_motivation": "Start with one small goal. Recognize progress, not perfection.",
        "chat_support": "Asking for support is a strong step. Tell someone you trust how you feel.",
        "chat_default_1": "I hear you and I am here to support you. Tell me how you feel today.",
        "chat_default_2": "You are not alone. We can find one small step forward together.",
        "chat_default_3": "Small steps help. Start with one breath and one simple action.",
        "contact_red_cross": "Toll-free nebo 24/7",
        "contact_befrienders": "Suicide prevention ak support; calls, SMS, ak WhatsApp",
        "contact_childline": "Youth ak adolescents; toll-free",
        "contact_nacada": "Substance use ak addiction",
        "contact_emergency": "Immediate emergency assistance",
        "hours_24_7": "24/7",
        "hours_unspecified": "Not specified",
        "available_hours": "Available",
        "daily_quotes": [
            "Step nebo tugul every day konyi change nebo meaning.",
            "Rest ko part nebo progress, ma ko luxury.",
            "Inendet ko resilient more than inendet think.",
            "Notice good things even eng days nebo many challenges.",
            "Breathe slowly. Inendet deserve calm.",
            "Kindness to self ko powerful act.",
            "Tugul ko opportunity nebo support wellbeing nebo inendet.",
            "Moment nebo tugul ko chance nebo reset ak move forward.",
            "Trust instincts nebo inendet ak honor feelings nebo inendet.",
            "Healthy habits start agobo mindful choice nebo tugul.",
        ],
        "score": "score",
        "finding_facilities": "Koyait hospitals ak clinics nebo koo…",
        "nearby_facilities_missing": "Maat hospitals ak clinics nebo koo eng km 20.",
        "nearby_facilities_suggestion": "Try town nebo ta nebo tugul, ak search hospitals eng Google Maps.",
        "nearby_facilities_heading": "Hospitals ak clinics nebo koo",
        "nearby_facility_found": "facility nebo koo koiten.",
        "nearby_facilities_found": "facilities nebo koo koiten.",
        "nearby_facility_error": "Facility search ma available.",
        "nearby_facility_failed": "Facility search fail.",
        "location_finding": "Koyait location…",
        "location_failed": "Location lookup fail.",
        "location_unavailable": "Location ma available eng browser. Enter town ak city agobo search.",
        "location_permission_failed": "Location access ma available. Enter town ak city agobo care nebo koo.",
        "location_manual_suggestion": "Enter town, open Google Maps, ak use emergency contacts.",
        "location_check_suggestion": "Check spelling ak enter nearby town. Also Google Maps ak emergency contacts.",
        "location_query_required": "Enter town ak city agobo search.",
        "clinic_distance": "km away",
        "facility_result_details": "{type} · {distance} km away",
        "close_chat": "Close chat",
        "admin": "Admin",
        "open_ai_chat": "Open AngazaCare AI chat",
        "password_too_short": "Password needs at least 12 characters.",
        "password_too_long": "Password must be no longer than 128 characters.",
        "password_complexity": "Use uppercase and lowercase, a number, and a special character.",
        "password_valid": "Password meets the requirements.",
        "password_mismatch": "The passwords do not match.",
        "or": "ak",
        "or_view": "ak view",
        "mood_axis_label": "Mood (1-5)",
        "stress_axis_label": "Stress (1-10)",
        "admin_dashboard": "Admin dashboard",
        "assigned_patients_list": "Assigned patients list:",
        "patient": "Patient",
        "consent": "Consent",
        "actions": "Actions",
        "yes": "Yes",
        "no": "No",
        "view": "View",
        "system_admin": "System admin",
        "engagement_overview": "Engagement overview across patient accounts.",
        "active_users_30_days": "Active users (30 days)",
        "assessments_taken": "Assessments taken",
        "chatbot_interactions": "Chatbot interactions",
        "recent_chatbot_activity": "Recent chatbot activity",
        "scope_consent_notice": "Scope: {scope}. Message content shown only with review consent.",
        "timestamp": "Timestamp",
        "user_session": "User / session",
        "prompt": "Prompt",
        "prompt_withheld": "Prompt withheld; no clinician-review consent.",
        "no_chat_activity": "No chatbot activity recorded.",
        "recent_assessment_results": "Recent assessment results",
        "assessment_consent_notice": "Responses and scores require clinician-review consent.",
        "user_id": "User ID",
        "clinical_summary_responses": "Clinical summary / responses",
        "view_responses": "View responses",
        "score_responses_withheld": "Score and responses withheld; no review consent.",
        "no_assessments": "No assessments recorded.",
        "name_label": "Name:",
        "email_label": "Email:",
        "consent_review_label": "Consent to clinician review:",
        "ai_summary_withheld": "AI summary withheld; no review consent.",
        "record_access_denied": "Full record access denied by patient consent.",
        "ai_chat_history": "AI chatbot history",
        "date_time": "Date & time",
        "patient_question": "Patient question",
        "ai_response": "AI response",
        "response_withheld": "Response withheld; no clinician-review consent.",
        "no_chat_history": "No chat history available.",
        "assessments_phq9": "Assessments (PHQ-9)",
        "question": "Question",
        "answer": "Answer",
        "assessment_card": "Assessment - {date} | Score: {score} ({severity})",
        "no_assessments_available": "No assessments available.",
        "recent_mood_entries": "Recent mood entries",
        "date": "Date",
        "note": "Note",
        "not_available": "Not available",
        "ai_summary": "AI summary",
        "summary_withheld": "Summary withheld; no review consent.",
        "scope_all_patients": "All patient accounts",
        "scope_assigned_patients": "Assigned patients",
        "app_title": "Mental health support",
        "chat_crisis_response": "I hear you and care. Call Befrienders Kenya: {phone} (24/7, free). Contact someone you trust now.",
        "chat_empty_message": "Please enter a message.",
        "api_enter_town_city": "Enter a town or city name.",
        "api_location_unavailable": "Location lookup is temporarily unavailable. Try again.",
        "api_no_matching_location": "No matching town or city found. Try another search.",
        "api_invalid_location": "A valid location is required.",
        "api_facility_unavailable": "Nearby facility search is temporarily unavailable.",
        "api_facility_fallback": "Search hospitals on Google Maps or use emergency support contacts.",
    },
}

def get_language():
    """Get the current language from session, default to English."""
    language = session.get("language", "en")
    return language if language in TRANSLATIONS else "en"


def get_translations():
    """Return the active language merged over English fallback strings."""
    translations = TRANSLATIONS["en"].copy()
    translations.update(TRANSLATIONS.get(get_language(), {}))
    return translations


def translate(key, default=None):
    """Get a string for the active language with an English fallback."""
    fallback = TRANSLATIONS["en"].get(key, default if default is not None else key)
    return TRANSLATIONS.get(get_language(), {}).get(key, fallback)

def get_text(key):
    """Get translated text for a key in the current language."""
    return translate(key)


@app.context_processor
def inject_language():
    lang = get_language()
    return {
        "current_lang": lang,
        "t": get_translations(),
        "translate": translate,
    }


def detect_language(text):
    """Detect if text is primarily Kiswahili or English."""
    sw_patterns = ["ni ", "na ", "wa ", "ku", "la ", "ta ", "za ", "ni\u0144", "jina"]
    sw_count = sum(text.lower().count(p) for p in sw_patterns)
    return "sw" if sw_count > 2 else "en"


def has_crisis_markers(text):
    """Check if message contains crisis indicators."""
    text_lower = text.lower()
    crisis_en = KIRAYA_KB["crisis_keywords_en"]
    crisis_sw = KIRAYA_KB["crisis_keywords_sw"]
    return any(kw in text_lower for kw in crisis_en + crisis_sw)


def store_chat_interaction(user_message, ai_response):
    try:
        user_id = current_user.id if current_user.is_authenticated else None
        session_hash = None
        if user_id is None:
            session_token = session.get("chat_activity_session")
            if not session_token:
                session_token = secrets.token_urlsafe(32)
                session["chat_activity_session"] = session_token
            session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()

        db.session.add(
            ChatInteraction(
                user_id=user_id,
                session_hash=session_hash,
                user_message=user_message,
                ai_response=ai_response,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not store chatbot interaction")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"reply": get_text("chat_empty_message")}), 400

    # Check for crisis markers first
    if has_crisis_markers(user_message):
        crisis_msg = get_text("chat_crisis_response").format(phone=KIRAYA_KB["befrienders"])
        store_chat_interaction(user_message, crisis_msg)
        return jsonify({"reply": crisis_msg}), 200

    if not genai or not os.getenv("GEMINI_API_KEY") or USE_FALLBACK_ONLY:
        ai_response = get_supportive_fallback(user_message, lang=get_language())
        store_chat_interaction(user_message, ai_response)
        return jsonify({"reply": ai_response}), 200

    # Get user's language preference
    user_lang = get_language()
    if user_lang == "en":
        lang_instruction = "Respond in English."
    else:
        lang_instruction = "Respond in Kiswahili."

    # Enhanced system prompt for better question answering
    system_prompt = (
        "You are AngazaCare AI, a compassionate and culturally aware mental health companion for Kenyans. "
        f"{lang_instruction} "
        "Provide responses that are warm, realistic, and easy to understand. "
        "Keep your tone supportive, grounded, and practical. "
        "Use general wellness advice and avoid medical diagnosis. "
        "Answer in complete sentences and do not end a response mid-sentence. "
        "Provide a comprehensive reply with at least 4 sentences and include a clear acknowledgement of the user's feelings, one or two observations, and at least one practical recommendation they can try. "
        "When the user asks for support, offer realistic next steps, encourage self-care, and remind them that professional help is available if needed. "
        "If you are unsure, say 'I am not sure, but here is a general suggestion' rather than inventing details. "
        "Always keep the response helpful, empathetic, and supportive."
    )

    # Add personalized context from user data
    user_context = []
    if current_user.is_authenticated:
        last_assessment = Assessment.query.filter_by(user_id=current_user.id).order_by(Assessment.created_at.desc()).first()
        today_entry = MoodEntry.query.filter_by(user_id=current_user.id, date=date.today()).first()
        mood_summary = get_recent_mood_summary(current_user)
        user_context.append(f"User: {current_user.name}")
        if today_entry:
            user_context.append(
                f"Today's mood: {today_entry.mood_score}/5, stress: {today_entry.stress_level}/10."
            )
            if today_entry.note:
                user_context.append(f"Today's note: {today_entry.note}")
        if last_assessment:
            user_context.append(
                f"Latest assessment score: {last_assessment.score} ({last_assessment.severity})."
            )
        if mood_summary:
            user_context.append(
                f"Recent 7-day average mood: {mood_summary['average_mood']}/5, stress: {mood_summary['average_stress']}/10."
            )

    if user_context:
        system_prompt = f"{system_prompt}\n\nUser Context:\n" + "\n".join(user_context)

    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            user_message,
            generation_config=genai.types.GenerationConfig(
                temperature=0.65,
                top_p=0.95,
                max_output_tokens=800,
            )
        )
        ai_response = response.text.strip() if response.text else get_supportive_fallback(lang=get_language())
    except Exception as e:
        app.logger.exception(f"AI chat failed: {e}")
        ai_response = get_supportive_fallback(lang=get_language())

    store_chat_interaction(user_message, ai_response)
    return jsonify({
        "reply": ai_response,
        "response": ai_response
    }), 200
    

@app.route("/api/mood-history")
@login_required
def mood_history():
    today = date.today()
    ninety_days_ago = today - timedelta(days=90)
    
    entries = MoodEntry.query.filter(
        MoodEntry.user_id == current_user.id,
        MoodEntry.date >= ninety_days_ago,
        MoodEntry.date <= today
    ).all()
    
    mood_dict = {}
    for entry in entries:
        mood_dict[entry.date.isoformat()] = {
            "mood": entry.mood_score,
            "stress": entry.stress_level
        }
    
    current = ninety_days_ago
    heatmap = []
    while current <= today:
        day_data = mood_dict.get(current.isoformat(), {"mood": 0, "stress": 0})
        heatmap.append({
            "date": current.isoformat(),
            "mood": day_data["mood"],
            "stress": day_data["stress"]
        })
        current += timedelta(days=1)
    
    return jsonify(heatmap)

@app.route("/api/weekly-report")
@login_required
def weekly_report():
    if not genai or not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "AI is resting, try again"}), 500
    
    today = date.today()
    week_ago = today - timedelta(days=7)
    
    mood_entries = MoodEntry.query.filter(
        MoodEntry.user_id == current_user.id,
        MoodEntry.date >= week_ago,
        MoodEntry.date <= today
    ).all()
    
    assessments = Assessment.query.filter(
        Assessment.user_id == current_user.id,
        Assessment.created_at >= datetime.combine(week_ago, datetime.min.time())
    ).all()
    
    mood_avg = sum(e.mood_score for e in mood_entries) / len(mood_entries) if mood_entries else 0
    stress_avg = sum(e.stress_level for e in mood_entries) / len(mood_entries) if mood_entries else 0
    notes = "\n".join([e.note for e in mood_entries if e.note])
    
    data_summary = f"""
    Last 7 Days Summary:
    - Average mood: {mood_avg:.1f}/5
    - Average stress: {stress_avg:.1f}/10
    - Mood entries: {len(mood_entries)}
    - Assessments: {len(assessments)}
    - Recent notes: {notes if notes else 'None'}
    """

    prompt = (
        "You are a wellness coach in AngazaCare. Respond in both English and Kiswahili using a warm, direct, and practical tone. "
        "Keep the language simple, culturally grounded, and supportive. "
        "Based on this user's 7-day data, describe one clear trend, one concern or support need, and offer three realistic, actionable recommendations. "
        "Use complete sentences and avoid stopping mid-thought. "
        "If the data is limited, say so gently and focus on encouraging small positive steps. "
        "Keep the response helpful, concise, and under 220 words."
    )

    prompt = prompt + f"\n\nUser data:\n{data_summary}"

    try:
        full_message = f"{prompt}\n\nPlease summarize the above user data with supportive recommendations."
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        response = model.generate_content(
            full_message,
            generation_config=genai.types.GenerationConfig(
                temperature=0.65,
                top_p=0.95,
                max_output_tokens=500,
            )
        )
        report = response.text.strip() if response.text else "Unable to generate report at this time."
        return jsonify({"report": report})
    except Exception as e:
        app.logger.exception("Weekly report generation failed")
        return jsonify({"report": get_supportive_fallback()}), 200


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
