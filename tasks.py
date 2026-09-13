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
    admin_initials = 'CR'
    recovery_question = 'What is your nickname?'

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
                password_hash TEXT NOT NULL,
                security_question TEXT,
                security_answer_hash TEXT)''')
            if postgres:
                run('ALTER TABLE task_accounts ADD COLUMN IF NOT EXISTS security_question TEXT')
                run('ALTER TABLE task_accounts ADD COLUMN IF NOT EXISTS security_answer_hash TEXT')
            else:
                columns = {row['name'] for row in run('PRAGMA table_info(task_accounts)').fetchall()}
                if 'security_question' not in columns:
                    run('ALTER TABLE task_accounts ADD COLUMN security_question TEXT')
                if 'security_answer_hash' not in columns:
                    run('ALTER TABLE task_accounts ADD COLUMN security_answer_hash TEXT')
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
            run('''CREATE TABLE IF NOT EXISTS task_participants (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                initials TEXT NOT NULL REFERENCES team_users(initials),
                hidden INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (task_id, initials))''')
            run('CREATE INDEX IF NOT EXISTS tasks_order ON tasks(assignee, completed, deadline, position)')
            rows = run('SELECT initials, MAX(name) AS name FROM slots GROUP BY initials').fetchall()
            for row in rows:
                run('''INSERT INTO team_users (initials, name) VALUES (?, ?)
                       ON CONFLICT(initials) DO NOTHING''', (row['initials'], row['name']))
            run('''INSERT INTO task_participants (task_id, initials)
                   SELECT id, assignee FROM tasks
                   WHERE assignee IS NOT NULL
                   ON CONFLICT(task_id, initials) DO NOTHING''')

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

    def current_user():
        token = request.cookies.get(cookie_name, '')
        if not token:
            return None
        with database() as run:
            row = run('SELECT initials FROM task_sessions WHERE token_hash=?', (digest(token),)).fetchone()
        return row['initials'] if row else None

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
        if not (request.path.startswith('/api/tasks') or request.path.startswith('/api/auth') or request.path.startswith('/api/admin')):
            return
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('X-Task-Request') != '1':
            abort(403, description='Missing task request header.')
        g.task_user = None
        g.task_user = current_user()

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
        if request.path.startswith('/api/tasks') or request.path.startswith('/api/auth') or request.path.startswith('/api/admin'):
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
        # The user explicitly stays signed in until signing out.
        expires = '9999-12-31T23:59:59+00:00'
        with database(write=True) as run:
            old = request.cookies.get(cookie_name)
            if old:
                run('DELETE FROM task_sessions WHERE token_hash=?', (digest(old),))
            run('INSERT INTO task_sessions (token_hash, initials, expires_at) VALUES (?, ?, ?)',
                (digest(token), initials, expires))
        response = jsonify(user=initials)
        response.set_cookie(cookie_name, token, httponly=True, secure=bool(postgres or request.is_secure),
                            samesite='Lax', max_age=10 * 365 * 86400, path='/')
        return response

    @bp.get('/tasks')
    def page():
        return render_template('tasks.html')

    @bp.get('/auth')
    def auth_page():
        return render_template('auth.html')

    @bp.get('/api/tasks/session')
    @bp.get('/api/auth/session')
    def session_info():
        users = directory()
        response = jsonify(user=g.task_user, profile=users.get(g.task_user) if g.task_user else None)
        # Migrate sessions created before the cookie became site-wide.
        legacy_token = request.cookies.get(cookie_name)
        if g.task_user and legacy_token:
            response.set_cookie(cookie_name, legacy_token, httponly=True,
                                secure=bool(postgres or request.is_secure), samesite='Lax',
                                max_age=10 * 365 * 86400, path='/')
        return response

    @bp.post('/api/tasks/register')
    @bp.post('/api/auth/register')
    def register():
        data = payload()
        initials = field(data, 'initials', 3).upper()
        if not re.fullmatch(r'[A-Z]{2,3}', initials):
            abort(400, description='Initials must be 2 or 3 letters.')
        name = field(data, 'name', 60)
        password = field(data, 'password', 256)
        if len(password) < 10:
            abort(400, description='Use a password of at least 10 characters.')
        security_answer = field(data, 'security_answer', 256)
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
            run('''INSERT INTO task_accounts
                   (initials, password_hash, security_question, security_answer_hash)
                   VALUES (?, ?, ?, ?)''',
                (initials, password_hash, recovery_question, generate_password_hash(security_answer.casefold())))
        socketio.emit('tz_update', directory())
        return session_response(initials)

    @bp.post('/api/tasks/login')
    @bp.post('/api/auth/login')
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

    @bp.get('/api/tasks/security-question')
    @bp.get('/api/auth/security-question')
    def security_question():
        initials = request.args.get('initials', '').strip().upper()
        if not re.fullmatch(r'[A-Z]{2,3}', initials):
            abort(400, description='Enter valid initials.')
        with database() as run:
            row = run('SELECT security_answer_hash FROM task_accounts WHERE initials=?', (initials,)).fetchone()
        if not row or not row['security_answer_hash']:
            abort(404, description='No password recovery question is set for this account.')
        return jsonify(question=recovery_question)

    @bp.post('/api/tasks/reset-password')
    @bp.post('/api/auth/reset-password')
    def reset_password():
        data = payload()
        initials = field(data, 'initials', 3).upper()
        answer = field(data, 'security_answer', 256)
        new_password = field(data, 'new_password', 256)
        if len(new_password) < 10:
            abort(400, description='Use a password of at least 10 characters.')
        throttle(initials)
        with database(write=True) as run:
            row = run('''SELECT security_answer_hash FROM task_accounts WHERE initials=?''', (initials,)).fetchone()
            if not row or not row['security_answer_hash'] or not check_password_hash(row['security_answer_hash'], answer.casefold()):
                abort(401, description='The security answer is incorrect.')
            run('DELETE FROM task_sessions WHERE initials=?', (initials,))
            run('UPDATE task_accounts SET password_hash=? WHERE initials=?',
                (generate_password_hash(new_password), initials))
        return session_response(initials)

    @bp.post('/api/tasks/logout')
    @bp.post('/api/auth/logout')
    def logout():
        with database(write=True) as run:
            run('DELETE FROM task_sessions WHERE token_hash=?', (digest(request.cookies.get(cookie_name, '')),))
        response = jsonify(status='ok')
        response.delete_cookie(cookie_name, path='/')
        response.delete_cookie(cookie_name, path='/api/tasks')
        return response

    @bp.get('/api/tasks')
    def list_tasks():
        with database() as run:
            rows = [dict(r) for r in run('''SELECT * FROM tasks ORDER BY
                completed, deadline IS NULL, deadline, position, id''').fetchall()]
            participant_rows = run('SELECT task_id, initials, hidden FROM task_participants').fetchall()
        participants = {}
        hidden = set()
        for row in participant_rows:
            participants.setdefault(row['task_id'], []).append(row['initials'])
            if g.task_user and row['initials'] == g.task_user and row['hidden']:
                hidden.add(row['task_id'])
        visible_rows = []
        for row in rows:
            if row['id'] in hidden:
                continue
            row['participants'] = participants.get(row['id'], [])
            visible_rows.append(row)
        return jsonify(tasks=visible_rows, users=directory(), user=g.task_user,
                       admin=g.task_user == admin_initials)

    def participant_values(run, values, assignee):
        if values is None:
            values = []
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            abort(400, description='Participants must be a list of registered initials.')
        values = list(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
        if assignee and assignee not in values:
            values.insert(0, assignee)
        for value in values:
            if not re.fullmatch(r'[A-Z]{2,3}', value) or not run('SELECT initials FROM team_users WHERE initials=?', (value,)).fetchone():
                abort(400, description='Choose only registered users as participants.')
        return values

    def save_participants(run, task_id, values):
        run('DELETE FROM task_participants WHERE task_id=?', (task_id,))
        for value in values:
            run('INSERT INTO task_participants (task_id, initials) VALUES (?, ?)', (task_id, value))

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
            task_id = secrets.token_hex(16)
            run('''INSERT INTO tasks (id, title, assignee, created_by, deadline, position)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (task_id, title, assignee, g.task_user, deadline, next_position(run)))
            save_participants(run, task_id, participant_values(run, data.get('participants'), assignee))
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
            save_participants(run, task_id, participant_values(run, data.get('participants'), assignee))
        return changed()

    @bp.post('/api/tasks/<task_id>/completion')
    @authenticated
    def complete(task_id):
        data = payload()
        if type(data.get('completed')) is not bool:
            abort(400, description='Completed must be true or false.')
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            membership = run('SELECT initials FROM task_participants WHERE task_id=? AND initials=? AND hidden=0',
                             (task_id, g.task_user)).fetchone()
            if not membership:
                abort(403, description='Only a visible participant can complete or reopen this task.')
            run('UPDATE tasks SET completed=?, completed_at=?, version=version+1 WHERE id=?',
                (int(data['completed']), now() if data['completed'] else None, task_id))
        return changed()

    @bp.post('/api/tasks/<task_id>/move')
    @authenticated
    def move(task_id):
        data = payload()
        direction = data.get('direction')
        target_id = data.get('target_id')
        if direction not in ('up', 'down') and (not isinstance(target_id, str) or not target_id):
            abort(400, description='Choose a task to place this task around.')
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            if task['completed']:
                abort(409, description='Completed tasks cannot be reordered.')
            rows = run('''SELECT id, assignee, deadline, position FROM tasks
                          WHERE completed=0 ORDER BY position, id''').fetchall()
            peers = [r for r in rows if r['assignee'] == task['assignee'] and r['deadline'] == task['deadline']]
            index = next(i for i, r in enumerate(peers) if r['id'] == task_id)
            if target_id:
                target = next((i for i, r in enumerate(peers) if r['id'] == target_id), None)
                if target is None or target_id == task_id:
                    abort(409, description='Drop tasks only within the same assignee and deadline group.')
                insert_at = target + (1 if data.get('after') is True else 0)
                moving = peers.pop(index)
                if index < insert_at:
                    insert_at -= 1
                peers.insert(max(0, min(insert_at, len(peers))), moving)
            else:
                target = index + (-1 if direction == 'up' else 1)
                if target < 0 or target >= len(peers):
                    abort(409, description='No task in that direction with the same deadline.')
                moving = peers.pop(index)
                peers.insert(target, moving)
            for priority, peer in enumerate(peers):
                run('UPDATE tasks SET position=?, version=version+1 WHERE id=?', ((priority + 1) * 10, peer['id']))
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

    @bp.delete('/api/tasks/<task_id>/membership')
    @authenticated
    def hide_task(task_id):
        data = payload()
        with database(write=True) as run:
            task = load_task(run, task_id, data)
            membership = run('SELECT initials FROM task_participants WHERE task_id=? AND initials=?',
                             (task_id, g.task_user)).fetchone()
            if not membership or task['assignee'] == g.task_user:
                abort(403, description='Only an added participant can remove this task from their list.')
            run('UPDATE task_participants SET hidden=1 WHERE task_id=? AND initials=?', (task_id, g.task_user))
        return changed()

    def admin_only(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if g.task_user != admin_initials:
                abort(403, description='Only the CR administrator can manage people.')
            return fn(*args, **kwargs)
        return wrapped

    @bp.delete('/api/admin/users/<initials>')
    @authenticated
    @admin_only
    def delete_user(initials):
        initials = initials.strip().upper()
        if initials == admin_initials:
            abort(400, description='The CR administrator cannot be deleted.')
        with database(write=True) as run:
            if not run('SELECT initials FROM team_users WHERE initials=?', (initials,)).fetchone():
                abort(404, description='User not found.')
            run('UPDATE tasks SET assignee=NULL WHERE assignee=?', (initials,))
            run('UPDATE tasks SET created_by=? WHERE created_by=?', (admin_initials, initials))
            run('DELETE FROM task_participants WHERE initials=?', (initials,))
            run('DELETE FROM task_sessions WHERE initials=?', (initials,))
            run('DELETE FROM task_accounts WHERE initials=?', (initials,))
            run('DELETE FROM team_users WHERE initials=?', (initials,))
            run('DELETE FROM slots WHERE initials=?', (initials,))
        socketio.emit('tz_update', directory())
        socketio.emit('task_update', {'refresh': True})
        return jsonify(status='ok')

    app.register_blueprint(bp)
    initialize()
    return directory, save_profile, current_user
