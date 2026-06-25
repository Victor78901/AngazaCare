import os
from dotenv import load_dotenv
load_dotenv()
from app import app, User, MoodEntry, get_mood_chart_data
from datetime import date, timedelta

with app.app_context():
    user = User.query.filter_by(email='demo@angazacare.com').first()
    print('user', user.email if user else None, 'id', user.id if user else None)
    if not user:
        raise SystemExit('Demo user not found')
    labels, mood_values, stress_values = get_mood_chart_data(user)
    print('labels:', labels)
    print('mood:', mood_values)
    print('stress:', stress_values)
    entries = MoodEntry.query.filter(MoodEntry.user_id == user.id, MoodEntry.date >= date.today() - timedelta(days=7)).order_by(MoodEntry.date).all()
    print('entries:', [(e.date.isoformat(), e.mood_score, e.stress_level) for e in entries])
