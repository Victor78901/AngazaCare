import sqlite3
from datetime import datetime

conn = sqlite3.connect('angazacare.db')
cursor = conn.cursor()

# Check existing assignments
print('Current assignments:')
cursor.execute('SELECT id, psychiatrist_id, patient_id, active FROM assignment')
assignments = cursor.fetchall()
for a in assignments:
    print(f'  {a}: psychiatrist={a[1]}, patient={a[2]}, active={a[3]}')

# Get admin user (should be user 3 or 4)
cursor.execute("SELECT id FROM user WHERE role='psychiatrist' LIMIT 1")
admin_user = cursor.fetchone()
if admin_user:
    admin_id = admin_user[0]
    print(f'\nAdmin psychiatrist ID: {admin_id}')
    
    # Add assignment for KyGo (user 3) to admin if not exists
    cursor.execute('SELECT id FROM assignment WHERE psychiatrist_id=? AND patient_id=3', (admin_id,))
    existing = cursor.fetchone()
    if not existing:
        cursor.execute('''INSERT INTO assignment (psychiatrist_id, patient_id, assigned_at, active) 
                         VALUES (?, 3, ?, 1)''', (admin_id, datetime.utcnow()))
        conn.commit()
        print(f'Added assignment: psychiatrist {admin_id} -> patient 3')
    else:
        print(f'Assignment already exists')

conn.close()
