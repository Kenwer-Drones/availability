from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import os
import sqlite3
import threading
import time as time_module
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'availability-secret')

# Use PostgreSQL if DATABASE_URL is set (production), otherwise SQLite (local dev)
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    # Fix for Render/Railway which uses postgres:// but psycopg2 needs postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

    def get_db():
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn

    def init_db():
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS slots (
            id SERIAL PRIMARY KEY,
            date_utc TEXT NOT NULL,
            hour_utc INTEGER NOT NULL,
            initials TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            UNIQUE(date_utc, hour_utc, initials)
        )''')
        conn.commit()
        cur.close()
        conn.close()

    def query_all_slots():
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT date_utc, hour_utc, initials, name FROM slots ORDER BY date_utc, hour_utc')
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    def toggle_slot_db(date_utc, hour_utc, initials, name):
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            'SELECT id FROM slots WHERE date_utc = %s AND hour_utc = %s AND initials = %s',
            (date_utc, hour_utc, initials)
        )
        existing = cur.fetchone()

        if existing:
            cur.execute('DELETE FROM slots WHERE id = %s', (existing['id'],))
            action = 'removed'
        else:
            cur.execute(
                'INSERT INTO slots (date_utc, hour_utc, initials, name) VALUES (%s, %s, %s, %s)',
                (date_utc, hour_utc, initials, name)
            )
            action = 'added'

        conn.commit()
        cur.close()
        conn.close()
        return action

else:
    # SQLite for local development or Fly.io with persistent volume
    DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'availability.db'))
    # On Fly.io, use /data for persistence
    if os.path.isdir('/data'):
        DB_PATH = '/data/availability.db'

    def get_db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db():
        conn = get_db()
        conn.execute('''CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_utc TEXT NOT NULL,
            hour_utc INTEGER NOT NULL,
            initials TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            UNIQUE(date_utc, hour_utc, initials)
        )''')
        conn.commit()
        conn.close()

    def query_all_slots():
        conn = get_db()
        rows = conn.execute('SELECT date_utc, hour_utc, initials, name FROM slots ORDER BY date_utc, hour_utc').fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def toggle_slot_db(date_utc, hour_utc, initials, name):
        conn = get_db()
        existing = conn.execute(
            'SELECT id FROM slots WHERE date_utc = ? AND hour_utc = ? AND initials = ?',
            (date_utc, hour_utc, initials)
        ).fetchone()

        if existing:
            conn.execute('DELETE FROM slots WHERE id = ?', (existing['id'],))
            action = 'removed'
        else:
            conn.execute(
                'INSERT INTO slots (date_utc, hour_utc, initials, name) VALUES (?, ?, ?, ?)',
                (date_utc, hour_utc, initials, name)
            )
            action = 'added'

        conn.commit()
        conn.close()
        return action


# Determine async mode based on environment
async_mode = 'gevent' if DATABASE_URL else 'threading'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=async_mode)

try:
    init_db()
except Exception as _e:
    import traceback
    print("init_db() failed at startup, will retry lazily:", _e)
    traceback.print_exc()

# In-memory store for user timezones (persists via API)
user_timezones = {}  # { initials: timezone }


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/robots.txt')
def robots():
    return 'User-agent: *\nDisallow: /', 200, {'Content-Type': 'text/plain'}


@app.route('/api/timezones', methods=['GET'])
def get_timezones():
    """Get all registered user timezones."""
    return jsonify(user_timezones)


@app.route('/api/timezones', methods=['POST'])
def set_timezone():
    """Register a user's timezone."""
    data = request.get_json()
    initials = data.get('initials', '').strip().upper()
    timezone = data.get('timezone', '').strip()
    name = data.get('name', '').strip()

    if not initials or not timezone:
        return jsonify({'error': 'Missing initials or timezone'}), 400

    user_timezones[initials] = {'timezone': timezone, 'name': name}

    # Broadcast timezone update to all clients
    socketio.emit('tz_update', user_timezones)

    return jsonify({'status': 'ok'})


@app.route('/api/slots', methods=['GET'])
def get_slots():
    try:
        rows = query_all_slots()
    except Exception as e:
        # Table may not exist yet (e.g. fresh DB) — try to create it, then retry once
        import traceback
        traceback.print_exc()
        try:
            init_db()
            rows = query_all_slots()
        except Exception as e2:
            traceback.print_exc()
            return jsonify({'error': str(e2)}), 500

    slots = {}
    for row in rows:
        key = f"{row['date_utc']}_{row['hour_utc']}"
        if key not in slots:
            slots[key] = []
        slots[key].append({'initials': row['initials'], 'name': row['name']})
    return jsonify(slots)


@app.route('/api/slots', methods=['POST'])
def toggle_slot():
    data = request.get_json()
    date_utc = data.get('date_utc')
    hour_utc = data.get('hour_utc')
    initials = data.get('initials', '').strip().upper()
    name = data.get('name', '').strip()

    if not date_utc or hour_utc is None or not initials:
        return jsonify({'error': 'Missing date_utc, hour_utc, or initials'}), 400

    action = toggle_slot_db(date_utc, hour_utc, initials, name)

    socketio.emit('slot_update', {
        'date_utc': date_utc,
        'hour_utc': hour_utc,
        'initials': initials,
        'name': name,
        'action': action
    })

    return jsonify({'action': action, 'date_utc': date_utc, 'hour_utc': hour_utc, 'initials': initials, 'name': name})


@socketio.on('connect')
def handle_connect():
    pass


@socketio.on('disconnect')
def handle_disconnect():
    pass


# ===== Daily Email Scheduler =====
def run_daily_email():
    """Run daily_email.py at midnight Phoenix time (7:00 AM UTC)."""
    while True:
        now = datetime.now(timezone.utc)
        # Calculate next 7:00 AM UTC (midnight Phoenix)
        target = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        time_module.sleep(wait_seconds)
        # Run the email script
        try:
            from daily_email import get_todays_summary, send_email
            today, rows = get_todays_summary()
            send_email(today, rows)
        except Exception as e:
            print(f'Daily email error: {e}')


# Start scheduler thread if SMTP is configured
if os.environ.get('SMTP_USER'):
    email_thread = threading.Thread(target=run_daily_email, daemon=True)
    email_thread.start()


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
