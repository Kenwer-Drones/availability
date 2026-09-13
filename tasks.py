"""Persistent task board with authenticated, assignee-only completion."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import re
import secrets
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, g, jsonify, render_template, request
from werkzeug.security import check_password_hash, generate_password_hash


def register_tasks(app, socketio, get_db, postgres=False):
    bp = Blueprint('tasks', __name__)
    attempts = {}
    attempts_lock = threading.Lock()
    cookie_name = 'task_session'

    @contextmanager
    def database(write=False):
        conn = get_db()
        if postgres:
            from psycopg2.extras import RealDictCursor
            cur = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cur = conn.cursor()
        try:
            if write:
                # Serialize board changes so simultaneous moves cannot lose priority.
                cur.execute('SELECT pg_advisory_xact_lock(73519024)' if postgres else 'BEGIN IMMEDIATE')
            def execute(sql, params=()):
                cur.execute(sql.replace('?', '%s') if postgres else sql, params)
                return cur
            yield execute
            if write:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def initialize():
        with database(write=True) as run:
            run('''CREATE TABLE IF NOT EXISTS team_users (
                initials TEXT PRIMARY KEY, name TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'America/Phoenix')''')
            run('''CREATE TABLE IF NOT EXISTS task_accounts (
                initials TEXT PRIMARY KEY REFERENCES team_users(initials),
                password_hash TEXT NOT NULL)''')
            run('''CREATE TABLE IF NOT EXISTS task_sessions (
                token_hash TEXT PRIMARY KEY,
                initials TEXT NOT NULL REFERENCES task_accounts(initials),
                expires_at TEXT NOT NULL)''')
            run('''CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                assignee TEXT REFERENCES team_users(initials),
                created_by TEXT NOT NULL REFERENCES task_accounts(initials),
                deadline TEXT, position INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0, completed_at TEXT,
                version INTEGER NOT NULL DEFAULT 1)''')
            run('CREATE INDEX IF NOT EXISTS tasks_order ON tasks(assignee, completed, deadline, position)')
            rows = run('SELECT initials, MAX(name) AS name FROM slots GROUP BY initials').fetchall()
            for row in rows:
                run('''INSERT INTO team_users (initials, name) VALUES (?, ?)
                       ON CONFLICT(initials) DO NOTHING''', (row['initials'], row['name']))

    def directory():
        with database() as run:
            return {r['initials']: {'name': r['name'], 'timezone': r['timezone']}
                    for r in run('SELECT * FROM team_users ORDER BY initials').fetchall()}

    def save_profile(initials, name, tz):
        with database(write=True) as run:
            run('''INSERT INTO team_users (initials, name, timezone) VALUES (?, ?, ?)
                   ON CONFLICT(initials) DO UPDATE SET name=excluded.name, timezone=excluded.timezone''',
                (initials, name, tz))

    def now():
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description='Expected a JSON object.')
        return data

    def field(data, key, maximum, required=True):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > maximum:
            abort(400, description=f'Invalid {key}.')
        value = value.strip()
        if required and not value:
            abort(400, description=f'{key.capitalize()} is required.')
        return value

    def deadline_value(value):
        if value in (None, ''):
            return None
        if not isinstance(value, str):
            abort(400, description='Invalid deadline.')
        try:
            # Deadline inputs are explicitly Arizona wall time, never browser-local.
            date = datetime.strptime(value, '%Y-%m-%dT%H:%M')
            return date.replace(tzinfo=ZoneInfo('America/Phoenix')).astimezone(timezone.utc).isoformat(timespec='seconds')
        except (ValueError, OverflowError):
            abort(400, description='Use a valid Arizona deadline date and time.')

    @bp.before_request
    def identity():
        if not request.path.startswith('/api/tasks'):
            return
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('X-Task-Request') != '1':
            abort(403, description='Missing task request header.')
        g.task_user = None
        token = request.cookies.get(cookie_name, '')
        if token:
            with database() as run:
                row = run('SELECT initials FROM task_sessions WHERE token_hash=? AND expires_at>?',
                          (digest(token), now())).fetchone()
                if row:
                    g.task_user = row['initials']

    @bp.errorhandler(400)
    @bp.errorhandler(401)
    @bp.errorhandler(403)
    @bp.errorhandler(404)
    @bp.errorhandler(409)
    @bp.errorhandler(429)
    def api_error(error):
        return jsonify(error=error.description), error.code

    @bp.after_request
    def prevent_stale_api_cache(response):
        if request.path.startswith('/api/tasks'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def authenticated(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.task_user:
                abort(401, description='Sign in to manage tasks.')
            return fn(*args, **kwargs)
        return wrapped

    def throttle(initials):
        stamp = time.monotonic()
        with attempts_lock:
            for key in list(attempts):
                attempts[key] = [t for t in attempts[key] if stamp - t < 60]
                if not attempts[key]:
                    del attempts[key]
            keys = [('ip', request.remote_addr), ('account', initials)]
            if any(len(attempts.get(key, [])) >= 10 for key in keys):
                abort(429, description='Too many attempts. Wait one minute and try again.')
            for key in keys:
                attempts.setdefault(key, []).append(stamp)

    def session_response(initials):
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(timespec='seconds')
        with database(write=True) as run:
            run('DELETE FROM task_sessions WHERE expires_at<=?', (now(),))
            old = request.cookies.get(cookie_name)
            if old:
                run('DELETE FROM task_sessions WHERE token_hash=?', (digest(old),))
            run('INSERT INTO task_sessions (token_hash, initials, expires_at) VALUES (?, ?, ?)',
                (digest(token), initials, expires))
        response = jsonify(user=initials)
        response.set_cookie(cookie_name, token, httponly=True, secure=bool(postgres or request.is_secure),
                            samesite='Lax', max_age=14 * 86400, path='/api/tasks')
        return response

    @bp.get('/tasks')
    def page():
        return render_template('tasks.html')

    @bp.get('/api/tasks/session')
    def session_info():
        return jsonify(user=g.task_user)

    @bp.post('/api/tasks/register')
    def register():
        data = payload()
        initials = field(data, 'initials', 3).upper()
        if not re.fullmatch(r'[A-Z]{2,3}', initials):
            abort(400, description='Initials must be 2 or 3 letters.')
        name = field(data, 'name', 60)
        password = field(data, 'password', 256)
        if len(password) < 10:
            abort(400, description='Use a password of at least 10 characters.')
        tz = field(data, 'timezone', 100, required=False) or 'America/Phoenix'
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            abort(400, description='Invalid timezone.')
        throttle(initials)
        password_hash = generate_password_hash(password)
        with database(write=True) as run:
            if run('SELECT initials FROM task_accounts WHERE initials=?', (initials,)).fetchone():
                abort(409, description='This profile already has a task account. Sign in instead.')
            run('''INSERT INTO team_users (initials, name, timezone) VALUES (?, ?, ?)
                   ON CONFLICT(initials) DO NOTHING''', (initials, name, tz))
            run('INSERT INTO task_accounts (initials, password_hash) VALUES (?, ?)', (initials, password_hash))
        socketio.emit('tz_update', directory())
        return session_response(initials)

    @bp.post('/api/tasks/login')
    def login():
        data = payload()
        initials = field(data, 'initials', 3).upper()
        password = field(data, 'password', 256)
        throttle(initials)
        with database() as run:
            row = run('SELECT password_hash FROM task_accounts WHERE initials=?', (initials,)).fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            abort(401, description='Incorrect initials or password.')
        return session_response(initials)

    @bp.post('/api/tasks/logout')
    def logout():
        with database(write=True) as run:
            run('DELETE FROM task_sessions WHERE token_hash=?', (digest(request.cookies.get(cookie_name, '')),))
        response = jsonify(status='ok')
        response.delete_cookie(cookie_name, path='/api/tasks')
        return response

    @bp.get('/api/tasks')
    def list_tasks():
        with database() as run:
            rows = [dict(r) for r in run('''SELECT * FROM tasks ORDER BY
                completed, deadline IS NULL, deadline, position, id''').fetchall()]
        return jsonify(tasks=rows, users=directory(), user=g.task_user)

    def assignee_value(run, value):
        if value in (None, ''):
            return None
        if not isinstance(value, str) or not run('SELECT initials FROM team_users WHERE initials=?', (value,)).fetchone():
            abort(400, description='Choose a registered user.')
        return value

    def next_position(run):
        return run('SELECT COALESCE(MAX(position), 0) + 1 AS position FROM tasks').fetchone()['position']

    def changed():
        socketio.emit('task_update', {'refresh': True})
        return jsonify(status='ok')

    @bp.post('/api/tasks')
    @authenticated
    def create():
        data = payload()
        title = field(data, 'title', 300)
        deadline = deadline_value(data.get('deadline'))
        with database(write=True) as run:
            assignee = assignee_value(run, data.get('assignee'))
            run('''INSERT INTO tasks (id, title, assignee, created_by, deadline, position)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (secrets.token_hex(16), title, assignee, g.task_user, deadline, next_position(run)))
        return changed(), 201

    def load_task(run, task_id, data):
        task = run('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            abort(404, description='Task no longer exists.')
        if type(data.get('version')) is not int or task['version'] != data['version']:
            abort(409, description='This task changed. Check the refreshed board; close and reopen any task editor before retrying.')
        return dict(task)

    @bp.patch('/api/tasks/<task_id>')
    @authenticated
    def update(task_id):
        data = payload()
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            if task['completed']:
                abort(409, description='Reopen the task before editing it.')
            title = field(data, 'title', 300)
            deadline = deadline_value(data.get('deadline'))
            assignee = assignee_value(run, data.get('assignee'))
            if assignee != task['assignee'] and task['assignee'] and g.task_user != task['assignee']:
                abort(403, description='Only the current assignee can reassign this task.')
            position = task['position']
            if deadline != task['deadline'] or assignee != task['assignee']:
                position = next_position(run)
            run('''UPDATE tasks SET title=?, deadline=?, assignee=?, position=?, version=version+1 WHERE id=?''',
                (title, deadline, assignee, position, task_id))
        return changed()

    @bp.post('/api/tasks/<task_id>/completion')
    @authenticated
    def complete(task_id):
        data = payload()
        if type(data.get('completed')) is not bool:
            abort(400, description='Completed must be true or false.')
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            if not task['assignee'] or task['assignee'] != g.task_user:
                abort(403, description='Only the assigned user can complete or reopen this task.')
            run('UPDATE tasks SET completed=?, completed_at=?, version=version+1 WHERE id=?',
                (int(data['completed']), now() if data['completed'] else None, task_id))
        return changed()

    @bp.post('/api/tasks/<task_id>/move')
    @authenticated
    def move(task_id):
        data = payload()
        if data.get('direction') not in ('up', 'down'):
            abort(400, description='Choose up or down.')
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            if task['completed']:
                abort(409, description='Completed tasks cannot be reordered.')
            rows = run('''SELECT id, assignee, deadline, position FROM tasks
                          WHERE completed=0 ORDER BY position, id''').fetchall()
            peers = [r for r in rows if r['assignee'] == task['assignee'] and r['deadline'] == task['deadline']]
            index = next(i for i, r in enumerate(peers) if r['id'] == task_id)
            target = index + (-1 if data['direction'] == 'up' else 1)
            if target < 0 or target >= len(peers):
                abort(409, description='No task in that direction with the same deadline.')
            other = peers[target]
            run('UPDATE tasks SET position=?, version=version+1 WHERE id=?', (other['position'], task_id))
            run('UPDATE tasks SET position=?, version=version+1 WHERE id=?', (task['position'], other['id']))
        return changed()

    @bp.delete('/api/tasks/<task_id>')
    @authenticated
    def delete(task_id):
        data = payload()
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            if g.task_user not in (task['created_by'], task['assignee']):
                abort(403, description='Only the creator or assignee can delete this task.')
            run('DELETE FROM tasks WHERE id=?', (task_id,))
        return changed()

    app.register_blueprint(bp)
    initialize()
    return directory, save_profile
