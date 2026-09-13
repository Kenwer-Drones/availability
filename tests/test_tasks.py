import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from flask import Flask

from tasks import register_tasks


class TaskBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, 'test.db')
        def get_db():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        self.get_db = get_db
        conn = get_db()
        conn.execute('CREATE TABLE slots (initials TEXT, name TEXT)')
        conn.execute("INSERT INTO slots VALUES ('CR', 'Chiranjiva Rao')")
        conn.commit()
        conn.close()
        self.app = Flask(__name__, template_folder='../templates')
        self.app.testing = True
        self.socket = Mock()
        self.directory, self.save_profile, _ = register_tasks(self.app, self.socket, get_db)
        self.alice = self.app.test_client()
        self.bob = self.app.test_client()
        self.visitor = self.app.test_client()
        self.headers = {'X-Task-Request': '1'}
        for client, initials in ((self.alice, 'AA'), (self.bob, 'BB')):
            response = self.send(client, '/register', initials=initials, name=initials, password='a-long-password',
                                 security_question='What is your favorite color?', security_answer='blue')
            self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, client, path='', method='POST', **data):
        return client.open('/api/tasks' + path, method=method, json=data, headers=self.headers)

    def tasks(self):
        return self.alice.get('/api/tasks').json['tasks']

    def create(self, **overrides):
        data = {'title': 'Review flight plan', 'assignee': 'BB', 'deadline': '2026-09-15T08:00'}
        data.update(overrides)
        response = self.send(self.alice, **data)
        self.assertEqual(response.status_code, 201, response.json)
        return next(t for t in self.tasks() if t['title'] == data['title'])

    def test_board_page_and_empty_users(self):
        self.assertEqual(self.visitor.get('/tasks').status_code, 200)
        self.assertEqual(self.tasks(), [])
        self.assertEqual(set(self.visitor.get('/api/tasks').json['users']), {'AA', 'BB', 'CR'})

    def test_arizona_deadline_to_utc_and_india(self):
        task = self.create()
        self.assertEqual(task['deadline'], '2026-09-15T15:00:00+00:00')
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.assertEqual(datetime.fromisoformat(task['deadline']).astimezone(ZoneInfo('Asia/Kolkata')).strftime('%H:%M'), '20:30')
        winter = self.create(title='Winter', deadline='2026-12-15T08:00')
        self.assertEqual(winter['deadline'], '2026-12-15T15:00:00+00:00')

    def test_only_assignee_can_complete_or_reopen_even_with_forged_initials(self):
        task = self.create()
        path = f"/{task['id']}/completion"
        self.assertEqual(self.send(self.visitor, path, version=1, completed=True, initials='BB').status_code, 401)
        self.assertEqual(self.send(self.alice, path, version=1, completed=True, initials='BB').status_code, 403)
        self.assertEqual(self.send(self.bob, path, version=1, completed=True).status_code, 200)
        self.assertEqual(self.tasks()[0]['completed'], 1)
        self.assertEqual(self.send(self.alice, path, version=2, completed=False).status_code, 403)
        self.assertEqual(self.send(self.bob, path, version=2, completed=False).status_code, 200)
        self.assertIsNone(self.tasks()[0]['completed_at'])

    def test_cannot_reassign_to_bypass_completion(self):
        task = self.create()
        self.assertEqual(self.send(self.alice, f"/{task['id']}", 'PATCH', version=1, title=task['title'], assignee='AA').status_code, 403)
        self.assertEqual(self.send(self.bob, f"/{task['id']}", 'PATCH', version=1, title=task['title'], assignee='AA').status_code, 200)
        self.assertEqual(self.tasks()[0]['assignee'], 'AA')

    def test_any_user_can_assign_task_to_another_or_self(self):
        self.create(assignee='AA')
        self.assertEqual(self.tasks()[0]['assignee'], 'AA')

    def test_unassigned_requires_assignment_before_completion(self):
        task = self.create(assignee=None, deadline=None)
        self.assertEqual(self.send(self.alice, f"/{task['id']}/completion", version=1, completed=True).status_code, 403)
        self.assertEqual(self.send(self.bob, f"/{task['id']}", 'PATCH', version=1, title=task['title'], assignee='BB').status_code, 200)

    def test_priority_moves_only_same_deadline_and_assignee(self):
        first = self.create(title='First')
        second = self.create(title='Second')
        self.create(title='Tomorrow', deadline='2026-09-16T08:00')
        self.create(title='Another person', assignee='AA')
        self.assertEqual(self.send(self.bob, f"/{second['id']}/move", version=1, direction='up').status_code, 200)
        same = [t['title'] for t in self.tasks() if t['assignee'] == 'BB']
        self.assertEqual(same, ['Second', 'First', 'Tomorrow'])
        self.assertEqual(self.send(self.bob, f"/{second['id']}/move", version=2, direction='up').status_code, 409)
        self.assertEqual(self.send(self.bob, f"/{first['id']}/move", version=1, direction='up').status_code, 409)

    def test_drag_target_can_place_task_at_arbitrary_position(self):
        first = self.create(title='First')
        middle = self.create(title='Middle')
        last = self.create(title='Last')
        response = self.send(self.bob, f"/{last['id']}/move", target_id=first['id'], after=False, version=1)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([t['title'] for t in self.tasks() if t['assignee'] == 'BB'], ['Last', 'First', 'Middle'])
        latest = next(t for t in self.tasks() if t['id'] == first['id'])
        response = self.send(self.bob, f"/{latest['id']}/move", target_id=middle['id'], after=True, version=latest['version'])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([t['title'] for t in self.tasks() if t['assignee'] == 'BB'], ['Last', 'Middle', 'First'])

    def test_no_deadline_priority_and_dates_sort_first(self):
        self.create(title='No date A', deadline=None)
        b = self.create(title='No date B', deadline=None)
        self.create(title='Dated')
        self.send(self.alice, f"/{b['id']}/move", version=1, direction='up')
        self.assertEqual([t['title'] for t in self.tasks()], ['Dated', 'No date B', 'No date A'])

    def test_stale_update_rejected(self):
        task = self.create()
        self.assertEqual(self.send(self.alice, f"/{task['id']}", 'PATCH', title='Updated', deadline=None, assignee='BB', version=1).status_code, 200)
        self.assertEqual(self.send(self.bob, f"/{task['id']}", 'PATCH', title='Stale', deadline=None, assignee='BB', version=1).status_code, 409)
        self.assertEqual(self.tasks()[0]['title'], 'Updated')

    def test_completion_requires_boolean(self):
        task = self.create()
        self.assertEqual(self.send(self.bob, f"/{task['id']}/completion", version=1, completed='false').status_code, 400)

    def test_completed_task_not_editable_or_reorderable(self):
        task = self.create()
        self.send(self.bob, f"/{task['id']}/completion", version=1, completed=True)
        self.assertEqual(self.send(self.bob, f"/{task['id']}/move", version=2, direction='up').status_code, 409)
        self.assertEqual(self.send(self.bob, f"/{task['id']}", 'PATCH', version=2, title='New').status_code, 409)

    def test_validation(self):
        for values in ({'title': ''}, {'title': 'a' * 301}, {'title': ['oops']}, {'assignee': 'ZZ'},
                       {'deadline': '2026-02-30T10:00'}, {'deadline': '2026-09-15T08:00Z'}, {'deadline': 123}):
            data = {'title': 'Task', 'assignee': 'BB'}
            data.update(values)
            self.assertEqual(self.send(self.alice, **data).status_code, 400, values)
        self.assertEqual(self.alice.post('/api/tasks', json=[], headers=self.headers).status_code, 400)
        self.assertEqual(self.tasks(), [])

    def test_csrf_and_anonymous_writes_blocked(self):
        self.assertEqual(self.alice.post('/api/tasks', json={'title': 'Task'}).status_code, 403)
        self.assertEqual(self.send(self.visitor, title='Task').status_code, 401)

    def test_password_login_logout_and_no_second_claim(self):
        self.assertEqual(self.send(self.visitor, '/login', initials='BB', password='incorrect').status_code, 401)
        self.assertEqual(self.send(self.visitor, '/register', initials='BB', name='Imposter', password='long-password',
                                   security_question='Question', security_answer='answer').status_code, 409)
        response = self.send(self.visitor, '/login', initials='BB', password='a-long-password')
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', response.headers['Set-Cookie'])
        self.assertEqual(self.visitor.get('/api/tasks/session').json['user'], 'BB')
        self.assertEqual(self.visitor.get('/api/auth/session').json['user'], 'BB')
        self.assertIn('Path=/', response.headers['Set-Cookie'])
        self.send(self.visitor, '/logout')
        self.assertIsNone(self.visitor.get('/api/tasks/session').json['user'])
        self.assertIsNone(self.visitor.get('/api/auth/session').json['user'])

    def test_session_remains_active_until_logout(self):
        conn = self.get_db()
        conn.execute("UPDATE task_sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        conn.commit()
        conn.close()
        self.assertEqual(self.send(self.alice, title='Task').status_code, 201)

    def test_security_question_password_reset(self):
        response = self.visitor.get('/api/auth/security-question?initials=BB', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['question'], 'What is your favorite color?')
        response = self.visitor.post('/api/auth/reset-password', headers=self.headers,
                                     json={'initials': 'BB', 'security_answer': 'BLUE', 'new_password': 'new-long-password'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.visitor.get('/api/auth/session').json['user'], 'BB')
        self.send(self.visitor, '/logout')
        self.assertEqual(self.send(self.visitor, '/login', initials='BB', password='new-long-password').status_code, 200)
        self.assertEqual(self.send(self.visitor, '/login', initials='BB', password='a-long-password').status_code, 401)

    def test_persistence_after_app_restart(self):
        self.create()
        self.save_profile('IN', 'India user', 'Asia/Kolkata')
        app2 = Flask('restarted')
        directory, _, _ = register_tasks(app2, Mock(), self.get_db)
        self.assertEqual(directory()['IN']['timezone'], 'Asia/Kolkata')
        self.assertEqual(len(app2.test_client().get('/api/tasks').json['tasks']), 1)
        self.assertEqual(app2.test_client().post('/api/tasks/login', json={'initials': 'BB', 'password': 'a-long-password'}, headers=self.headers).status_code, 200)

    def test_delete_permissions(self):
        task = self.create(assignee='AA')
        self.assertEqual(self.send(self.bob, f"/{task['id']}", 'DELETE', version=1).status_code, 403)
        self.assertEqual(self.send(self.alice, f"/{task['id']}", 'DELETE', version=1).status_code, 200)
        self.assertEqual(self.tasks(), [])
        self.assertTrue(self.socket.emit.called)


if __name__ == '__main__':
    unittest.main()
