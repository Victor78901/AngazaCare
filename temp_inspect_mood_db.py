import sqlite3
from pathlib import Path
path = Path('angazacare.db')
print('db exists', path.exists())
if not path.exists():
    raise SystemExit(1)
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute('SELECT id, email, name FROM user')
users = cur.fetchall()
print('users', users)
for u in users:
    cur.execute('SELECT COUNT(*), MIN(date), MAX(date) FROM mood_entry WHERE user_id=?', (u[0],))
    c, mn, mx = cur.fetchone()
    print('user', u[0], u[1], 'entries', c, 'min', mn, 'max', mx)
    cur.execute('SELECT date, mood_score, stress_level FROM mood_entry WHERE user_id=? ORDER BY date DESC LIMIT 7', (u[0],))
    rows = cur.fetchall()
    print('rows', rows)
conn.close()