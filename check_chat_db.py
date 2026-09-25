import sqlite3

conn = sqlite3.connect('angazacare.db')
cursor = conn.cursor()

# Check if chat_message table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_message'")
table_exists = cursor.fetchone()
print(f'chat_message table exists: {bool(table_exists)}')

if table_exists:
    # Get count of messages
    cursor.execute('SELECT COUNT(*) FROM chat_message')
    count = cursor.fetchone()[0]
    print(f'Total chat messages: {count}')
    
    # Get all messages
    cursor.execute('SELECT user_id, user_message, ai_response, created_at FROM chat_message ORDER BY created_at DESC LIMIT 5')
    messages = cursor.fetchall()
    if messages:
        print('Recent messages:')
        for msg in messages:
            print(f'  User {msg[0]}: {msg[1][:60]}... -> {msg[2][:60]}...')
    else:
        print('No messages found')
else:
    print('chat_message table does not exist')

# Also check user count to see which user might be KyGo
cursor.execute('SELECT id, name FROM user')
users = cursor.fetchall()
print('\nUsers in database:')
for user in users:
    print(f'  User {user[0]}: {user[1]}')

conn.close()
