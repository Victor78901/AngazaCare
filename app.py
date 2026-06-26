import json
import math
import os
import random
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
try:
    import google.generativeai as genai
except Exception:
    genai = None
from dotenv import load_dotenv

from flask import Flask, flash, redirect, render_template, request, url_for, jsonify, session
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
import bcrypt

from models import (
    Assessment,
    MoodEntry,
    Recommendation,
    User,
    db,
    BreathingSession,
    Assignment,
    ClinicianViewLog,
)

load_dotenv()

print("Loading environment variables...")
print(f"USE_FALLBACK_ONLY: {os.getenv('USE_FALLBACK_ONLY', 'not set')}")
print(f"GEMINI_API_KEY set: {bool(os.getenv('GEMINI_API_KEY'))}")

# Set to True to use fallback responses only (when API quota is exhausted)
USE_FALLBACK_ONLY = os.getenv("USE_FALLBACK_ONLY", "false").lower() == "true"

print(f"genai module loaded: {genai is not None}")

if genai is not None and not USE_FALLBACK_ONLY:
    try:
        api_key = os.getenv("GEMINI_API_KEY", "")
        print(f"Configuring genai with key: {'***' + api_key[-4:] if api_key else 'None'}")
        genai.configure(api_key=api_key)
        print("GenAI configured successfully")
    except Exception as e:
        print(f"GenAI configuration failed: {e}")
        genai = None

print("Creating Flask app...")
app = Flask(__name__)
app.config["SECRET_KEY"] = "angazacare_secret_key_2026"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///angazacare.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"

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
    (15, 19, "Moderately severe", "More support may be helpful. Reach out to someone you trust and keep monitoring your wellbeing.", "#ff7b4d"),
    (20, 27, "Severe", "Urgent support is recommended. Reach out to a mental health professional or emergency services.", "#ff6464"),
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
    {"name": "Befrienders Kenya", "role": "Crisis Support", "phone": "0800 723 253", "email": "support@befrienders.org"},
    {"name": "Mathare Hospital Mental Health", "role": "Counseling Unit", "phone": "020 2084040", "email": "info@matharehospital.co.ke"},
    {"name": "Kenyatta National Hospital", "role": "Psychiatric Services", "phone": "020 2726300", "email": "psychiatry@knh.or.ke"},
    {"name": "Emergency", "role": "Immediate Help", "phone": "999 / 112", "email": ""},
]


def get_daily_quote():
    today = date.today()
    index = (today.year + today.month + today.day) % len(DAILY_QUOTES)
    return DAILY_QUOTES[index]


def get_assessment_level(score):
    for minimum, maximum, label, message, color in ASSESSMENT_LEVELS:
        if minimum <= score <= maximum:
            return {"label": label, "message": message, "color": color}
    return {"label": ASSESSMENT_LEVELS[-1][2], "message": ASSESSMENT_LEVELS[-1][3], "color": ASSESSMENT_LEVELS[-1][4]}


def haversine_distance(lat1, lon1, lat2, lon2):
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def format_osm_address(tags):
    address_parts = []
    for key in ["addr:housenumber", "addr:street", "addr:suburb", "addr:city", "addr:state", "addr:postcode"]:
        value = tags.get(key)
        if value:
            address_parts.append(value)
    return ", ".join(address_parts) if address_parts else tags.get("name", "Address unavailable")


def query_overpass_hospitals(lat, lng):
    overpass_query = f"""
[out:json][timeout:25];
(
  node["amenity"="hospital"](around:10000,{lat},{lng});
  way["amenity"="hospital"](around:10000,{lat},{lng});
  relation["amenity"="hospital"](around:10000,{lat},{lng});
  node["healthcare"="clinic"](around:10000,{lat},{lng});
  way["healthcare"="clinic"](around:10000,{lat},{lng});
  relation["healthcare"="clinic"](around:10000,{lat},{lng});
);
out center tags;
"""
    payload = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
    request_obj = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "AngazaCare/1.0 (https://angazacare.example)"
        },
    )
    with urllib.request.urlopen(request_obj, timeout=25) as response:
        return json.loads(response.read())


def parse_overpass_elements(elements, user_lat, user_lng):
    facilities = []
    for element in elements:
        tags = element.get("tags", {})
        center = element.get("center") or {}
        lat = center.get("lat") if center else element.get("lat")
        lon = center.get("lon") if center else element.get("lon")
        if lat is None or lon is None:
            continue
        distance_km = haversine_distance(user_lat, user_lng, float(lat), float(lon))
        name = tags.get("name") or tags.get("healthcare") or tags.get("amenity") or "Unknown facility"
        facilities.append({
            "name": name,
            "address": format_osm_address(tags),
            "distance": round(distance_km, 1),
            "lat": float(lat),
            "lng": float(lon),
            "maps_url": f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}",
        })
    return sorted(facilities, key=lambda f: f["distance"])[:10]


def query_geocode(query, limit=5):
    params = {
        "q": query,
        "format": "json",
        "limit": str(limit),
        "addressdetails": "1",
    }
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    request_obj = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AngazaCare/1.0 (https://angazacare.example)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request_obj, timeout=20) as response:
        return json.loads(response.read())


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
    connection.commit()
    connection.close()


def init_db():
    with app.app_context():
        try:
            migrate_db()
            db_path = os.path.join(os.getcwd(), "angazacare.db")
            print(f"Database path: {db_path}")
            print(f"Database exists: {os.path.exists(db_path)}")
            
            if not os.path.exists(db_path):
                print("Creating database tables...")
                db.create_all()
                print("Seeding database...")
                seed_database()
                print("Database seeded successfully")
            else:
                print("Database exists, ensuring tables are created...")
                db.create_all()
                user_count = User.query.count()
                print(f"Current user count: {user_count}")
                if user_count == 0:
                    print("Seeding database with demo data...")
                    seed_database()
                    print("Database seeded successfully")
        except Exception as e:
            print(f"Database initialization error: {e}")
            import traceback
            traceback.print_exc()
            raise


def hash_password(password):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def check_password(password, hashed):
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


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
    for offset in reversed(range(7)):
        day = today - timedelta(days=offset)
        labels.append(day.strftime("%b %d"))
        entry = MoodEntry.query.filter_by(user_id=user.id, date=day).first()
        mood_values.append(entry.mood_score if entry else 0)
        stress_values.append(entry.stress_level if entry else 0)

    # If the last 7 days contain no actual records, fall back to the most recent available entries.
    if any(value != 0 for value in mood_values) or any(value != 0 for value in stress_values):
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
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        existing = User.query.filter_by(email=email).first()
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

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and check_password(password, user.password_hash):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash(get_text("invalid_credentials"), "danger")

    return render_template("login.html")


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
        if not current_user.is_authenticated or getattr(current_user, "role", None) != "psychiatrist":
            flash(get_text("invalid_credentials"), "danger")
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
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
        summary = get_patient_ai_summary(patient)
        patients.append({
            "id": patient.id,
            "masked_name": blur_text(patient.name, reveal_start=2, reveal_end=2),
            "masked_email": blur_email(patient.email),
            "consent_to_clinician_review": patient.consent_to_clinician_review,
            "ai_summary": summary,
        })

    return render_template("admin.html", patients=patients)


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
        }
        for a in assessments_query.all()
    ]

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

    ai_summary = get_patient_ai_summary(patient)
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
        mood_entries=mood_entries,
        full_access=full_access,
        ai_summary=ai_summary,
        masked_name=masked_name,
        masked_email=masked_email,
    )


@app.route("/set_language/<lang>")
def set_language(lang):
    """Set the user's language preference."""
    if lang in ["en", "sw"]:
        session["language"] = lang
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    last_assessment = Assessment.query.filter_by(user_id=current_user.id).order_by(Assessment.created_at.desc()).first()
    today_entry = MoodEntry.query.filter_by(user_id=current_user.id, date=date.today()).first()
    streak = get_streak(current_user)
    labels, mood_data, stress_data = get_mood_chart_data(current_user)
    crisis_detected = check_crisis_flag(current_user)
    
    return render_template(
        "dashboard.html",
        daily_quote=get_daily_quote(),
        last_assessment=last_assessment,
        today_entry=today_entry,
        streak=streak,
        chart_labels=json.dumps(labels),
        mood_chart=json.dumps(mood_data),
        stress_chart=json.dumps(stress_data),
        crisis_detected=crisis_detected,
        current_lang=get_language(),
        t=TRANSLATIONS[get_language()],
    )


@app.route("/assessment", methods=["GET", "POST"])
@login_required
def assessment():
    result = None
    radar_data = None
    if request.method == "POST":
        answers = []
        score = 0
        domains = {"mood": 0, "sleep": 0, "energy": 0, "concentration": 0, "appetite": 0, "self_worth": 0, "motivation": 0}
        
        domain_map = [
            ("mood", [0, 1]),
            ("sleep", [2]),
            ("energy", [3]),
            ("appetite", [4]),
            ("self_worth", [5]),
            ("concentration", [6]),
            ("motivation", [7, 8]),
        ]
        
        for i in range(10):
            value = int(request.form.get(f"q{i}", 0))
            answers.append(value)
            score += value
        
        for domain, indices in domain_map:
            for idx in indices:
                if idx < len(answers):
                    domains[domain] += answers[idx]
        
        severity_data = get_assessment_level(score)
        assessment = Assessment(
            user_id=current_user.id,
            score=score,
            severity=severity_data["label"],
            answers_json=json.dumps(answers),
        )
        db.session.add(assessment)
        db.session.commit()
        
        result = {
            "score": score,
            "label": severity_data["label"],
            "message": severity_data["message"],
            "color": severity_data["color"],
        }
        
        radar_data = {
            "labels": list(domains.keys()),
            "values": list(domains.values()),
        }
    
    questions = PHQ9_QUESTIONS_SW if get_language() == "sw" else PHQ9_QUESTIONS
    return render_template("assessment.html", questions=questions, result=result, radar_data=json.dumps(radar_data) if radar_data else None)


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
        return redirect(url_for("mood_tracker"))

    labels, mood_data, stress_data = get_mood_chart_data(current_user)
    return render_template(
        "mood_tracker.html",
        today_entry=today_entry,
        chart_labels=json.dumps(labels),
        mood_chart=json.dumps(mood_data),
        stress_chart=json.dumps(stress_data),
    )


@app.route("/recommendations")
@login_required
def recommendations():
    last_assessment = Assessment.query.filter_by(user_id=current_user.id).order_by(Assessment.created_at.desc()).first()
    if last_assessment:
        recommendation = Recommendation.query.filter(
            Recommendation.score_range_min <= last_assessment.score,
            Recommendation.score_range_max >= last_assessment.score,
        ).first()
        tips = json.loads(recommendation.tips) if recommendation else []
        severity_data = get_assessment_level(last_assessment.score)
    else:
        tips = []
        severity_data = None
    return render_template(
        "recommendations.html",
        tips=tips,
        assessment=last_assessment,
        severity_data=severity_data,
        current_lang=get_language(),
        t=TRANSLATIONS[get_language()],
    )


@app.route("/api/recommend", methods=["POST"])
@login_required
def api_recommend():
    data = request.get_json(silent=True) or {}
    score = data.get("score")
    lat = data.get("lat")
    lng = data.get("lng")

    try:
        score = int(score)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid score."}), 400

    if lat is None or lng is None:
        return jsonify({"error": "Location coordinates are required."}), 400

    try:
        user_lat = float(lat)
        user_lng = float(lng)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates."}), 400

    try:
        os_data = query_overpass_hospitals(user_lat, user_lng)
    except Exception as e:
        app.logger.exception(f"Overpass lookup failed: {e}")
        return jsonify({"error": "Facility lookup failed. Please try again later."}), 502

    elements = os_data.get("elements", [])
    facilities = parse_overpass_elements(elements, user_lat, user_lng)

    response = {
        "score": score,
        "severity": get_assessment_level(score)["label"],
        "message": get_assessment_level(score)["message"],
        "facilities": facilities,
    }

    if not facilities:
        response["fallback"] = {
            "helpline": KIRAYA_KB["befrienders"],
            "text": "No nearby hospitals or clinics were found within 10 km. Please use local emergency resources or view the Emergency section.",
        }

    return jsonify(response), 200


@app.route("/api/geocode", methods=["POST"])
@login_required
def api_geocode():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Location query is required."}), 400

    try:
        results = query_geocode(query, limit=5)
        formatted_results = [
            {
                "display_name": item.get("display_name"),
                "lat": item.get("lat"),
                "lon": item.get("lon"),
            }
            for item in results
            if item.get("lat") and item.get("lon")
        ]
        return jsonify({"results": formatted_results}), 200
    except Exception as e:
        app.logger.exception(f"Geocode lookup failed: {e}")
        return jsonify({"error": "Location lookup failed. Please try again later."}), 502


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
        "dashboard": "Dashboard",
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
        "good_day": "Good day",
        "todays_wellness": "Today's wellness snapshot",
        "last_assessment": "Last assessment",
        "todays_mood": "Today's mood",
        "current_streak": "Current streak",
        "days_of_checkins": "days of daily check-ins",
        "no_assessment": "No assessment yet.",
        "record_feelings": "Record your feelings in the mood tracker.",
        "mood_label": "mood",
        "stress_label": "stress",
        "weekly_mood_chart": "Weekly mood chart",
        "check_mental_health": "Check in with a quick mental health survey.",
        "share_mood": "Share your mood, stress, and reflections.",
        "get_support_tips": "Get support tips based on your latest score.",
        "access_support": "Access urgent Kenya-specific support contacts.",
        "recommendations_title": "Wellness Recommendations",
        "last_assessment_score": "Your last assessment score was",
        "no_assessment_recorded": "No assessment recorded yet.",
        "complete_assessment": "Complete an",
        "assessment_link": "assessment",
        "to_receive_tips": "to receive tailored tips.",
        "severity_label": "Severity",
        "urgent_recommendation": "Please seek urgent help and use the Emergency section if needed.",
        "emergency_link_text": "View Emergency resources",
        "hospital_recommendation_title": "Hospital Recommendation",
        "diagnostic_disclaimer": "This is not a diagnostic tool. It provides nearby care suggestions only.",
        "hospital_recommendation_description": "Get nearby hospitals and clinics based on your location, or enter a town if location access is denied.",
        "use_my_location": "Use my location",
        "enter_town_manually": "Enter town manually",
        "manual_location_placeholder": "Town, city or neighborhood",
        "search_location": "Search location",
        "no_facilities_found": "No nearby facilities were found. Please check the Emergency section or call your local helpline.",
        "helpline_label": "Helpline",
        "directions_link_text": "Get directions",
        "searching_nearby": "Searching for nearby facilities...",
        "facility_query_failed": "Facility search failed. Please try again later.",
        "recommendations_found": "Nearby facilities found:",
        "manual_enter_prompt": "Enter your town or city to find nearby locations.",
        "geolocation_unsupported": "Geolocation is not supported by your browser.",
        "locating": "Locating...",
        "location_denied": "Location access denied. You can enter your town manually below.",
        "enter_location_query": "Please enter a location query.",
        "geocoding": "Looking up location...",
        "geocode_failed": "Location lookup failed. Please try a different town or city.",
        "use_this_location": "Use this location",
        "select_location": "Select the best match for your location.",
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
        "voice_chat_button": "Voice Chat",
        "voice_input_start": "Start voice input",
        "voice_input_stop": "Stop listening",
        "voice_input_unsupported": "Voice input is not supported in this browser.",
        "voice_input_listening": "Listening...",
        "voice_input_unavailable": "Your browser cannot use voice input at the moment.",
        "voice_input_error": "There was an error with voice recognition. Please try again.",
        "type_message_empty": "Please type a message to send.",
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
    },
    "sw": {
        "dashboard": "Dashibodi",
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
        "good_day": "Habari",
        "todays_wellness": "Picha ya afya yako leo",
        "last_assessment": "Tathmini ya mwisho",
        "todays_mood": "Hali ya jini leo",
        "current_streak": "Kamba ya sasa",
        "days_of_checkins": "siku za ukaguzi wa kila siku",
        "no_assessment": "Hakuna tathmini bado.",
        "record_feelings": "Rekodi hisia zako katika kufuatilia hali ya jini.",
        "mood_label": "hali ya jini",
        "stress_label": "msongo wa mawazo",
        "weekly_mood_chart": "Chati ya hali ya jini ya kila wiki",
        "check_mental_health": "Jaribu tathmini ya haraka ya afya ya akili.",
        "share_mood": "Shiriki hali yako, msongo wa mawazo, na mawazo yako.",
        "get_support_tips": "Pata vidokezo vya msaada kulingana na alama yako ya mwisho.",
        "access_support": "Fikirini rasilimali za dharura za Kenya.",
        "ai_assistant": "AI ya AngazaCare",
        "type_message": "Andika ujumbe...",
        "send": "Tuma",
        "ai_resting": "AI ya AngazaCare inapumzika, tafadhali jaribu tena",
        "type_message_empty": "Tafadhali andika ujumbe kabla ya kutuma.",
        "voice_input_start": "Anza kuzungumza kwa sauti",
        "voice_input_stop": "Acha kusikiliza",
        "voice_input_unsupported": "Utambuzi wa sauti hauendani na kivinjari hiki.",
        "voice_input_listening": "Inasikiliza...",
        "voice_input_unavailable": "Kivinjari chako hakiwezi kutumia sauti kwa sasa.",
        "voice_input_error": "Kuna tatizo na utambuzi wa sauti. Tafadhali jaribu tena.",
        "voice_chat_button": "Sauti Chat",
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
        "severity_label": "Ukadiriaji",
        "urgent_recommendation": "Tafadhali tafuta msaada wa haraka na tumia sehemu ya Dharura ikiwa unahitaji.",
        "emergency_link_text": "Tazama rasilimali za Dharura",
        "hospital_recommendation_title": "Mapendekezo ya Hospitali",
        "diagnostic_disclaimer": "Hii sio chombo cha utambuzi. Inatoa mapendekezo ya huduma tu.",
        "hospital_recommendation_description": "Pata hospitali na kliniki za karibu kulingana na eneo lako, au ingiza mji ikiwa ufikiaji wa eneo umekwisha katishwa.",
        "use_my_location": "Tumia eneo langu",
        "enter_town_manually": "Weka mji kwa mkono",
        "manual_location_placeholder": "Mji, mtaa, au eneo",
        "search_location": "Tafuta eneo",
        "no_facilities_found": "Hakuna vituo vya karibu vilivyopatikana. Tafadhali angalia sehemu ya Dharura au piga huduma ya msaada ya eneo lako.",
        "helpline_label": "Msaada",
        "directions_link_text": "Pata maelekezo",
        "searching_nearby": "Kutatua vituo vya karibu...",
        "facility_query_failed": "Utafutaji wa vituo ulishindikana. Tafadhali jaribu tena baadaye.",
        "recommendations_found": "Vituo vya karibu vimepatikana:",
        "manual_enter_prompt": "Weka mji au jiji lako kupata maeneo ya karibu.",
        "geolocation_unsupported": "Geolocation haitegemezwi na kivinjari chako.",
        "locating": "Kutatua eneo...",
        "location_denied": "Ufikiaji wa eneo umekatishwa. Unaweza kuingiza mji kwa mkono hapa chini.",
        "enter_location_query": "Tafadhali ingiza swali la eneo.",
        "geocoding": "Inatafuta eneo...",
        "geocode_failed": "Utafutaji wa eneo ulishindikana. Tafadhali jaribu mji au jiji tofauti.",
        "use_this_location": "Tumia eneo hili",
        "select_location": "Chagua matokeo yanayofaa zaidi kwa eneo lako.",
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
    },
}

def get_language():
    """Get the current language from session, default to English."""
    return session.get("language", "en")

def get_text(key):
    """Get translated text for a key in the current language."""
    lang = get_language()
    return TRANSLATIONS.get(lang, {}).get(key, TRANSLATIONS["en"].get(key, key))


@app.context_processor
def inject_language():
    lang = get_language()
    return {
        "current_lang": lang,
        "t": TRANSLATIONS.get(lang, TRANSLATIONS["en"]),
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


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"reply": "Please enter a message."}), 400

    # Check for crisis markers first
    if has_crisis_markers(user_message):
        crisis_msg = (
            f"I hear you and I care. Please reach Befrienders Kenya: {KIRAYA_KB['befrienders']} (24/7, free)\\n\\n"
            f"Nakuona na nakujali. Tafadhali wasiliana Befrienders Kenya: {KIRAYA_KB['befrienders']} (24/7, bure)"
        )
        return jsonify({"reply": crisis_msg}), 200

    if not genai or not os.getenv("GEMINI_API_KEY") or USE_FALLBACK_ONLY:
        return jsonify({"reply": get_supportive_fallback(user_message, lang=get_language())}), 200

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


# Initialize database on startup
with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
