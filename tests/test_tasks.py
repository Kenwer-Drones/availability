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
                                 security_answer='blue')
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(client.get('/api/auth/session').json['user'])
            self.assertEqual(self.send(client, '/login', initials=initials, password='a-long-password').status_code, 200)

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
        self.assertEqual(set(self.alice.get('/api/tasks').json['users']), {'AA', 'BB', 'CR'})

    def test_dedicated_auth_page_and_signup_uses_shared_flow(self):
        response = self.visitor.get('/auth?mode=signup&next=/tasks')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Create account', response.data)
        response = self.send(self.visitor, '/register', initials='DD', name='Diana', password='a-long-password',
                             security_answer='nickname')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.visitor.get('/api/auth/security-question?initials=DD').json['question'],
                         'What is your nickname?')

    def test_legacy_recovery_question_is_not_relabelled(self):
        conn = self.get_db()
        conn.execute("UPDATE task_accounts SET security_question='Favorite color?' WHERE initials='BB'")
        conn.commit()
        conn.close()
        self.assertEqual(self.visitor.get('/api/auth/security-question?initials=BB').json['question'], 'Favorite color?')

    def test_signup_preserves_existing_profile_identity_and_requires_login(self):
        self.save_profile('DD', 'Old name', 'America/Phoenix')
        response = self.send(self.visitor, '/register', initials='DD', name='Updated name', password='long-password', security_answer='nickname', timezone='Asia/Kolkata')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.visitor.get('/api/auth/session').json['user'])
        self.assertEqual(self.directory()['DD']['name'], 'Updated name')
        self.assertEqual(self.directory()['DD']['timezone'], 'Asia/Kolkata')

    def test_hidden_participant_stays_removed_after_edit(self):
        task = self.create(participants=['AA'])
        self.send(self.alice, f"/{task['id']}/membership", 'DELETE', version=1)
        self.send(self.bob, f"/{task['id']}", 'PATCH', version=1, title='Edited', assignee='BB', deadline='2026-09-15T08:00')
        self.assertNotIn(task['id'], [t['id'] for t in self.tasks()])

    def test_india_signup_aliases_and_login(self):
        for initials, zone in [('IA', 'Asia/Kolkata'), ('IB', 'Asia/Calcutta'), ('IC', 'Asia/Kolkatha')]:
            with self.subTest(zone=zone):
                client = self.app.test_client()
                response = self.send(client, '/register', initials=initials, name='India user', password='long-password', security_answer='nickname', timezone=zone)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertIsNone(client.get('/api/auth/session').json['user'])
                self.assertEqual(self.send(client, '/login', initials=initials, password='long-password').status_code, 200)
                self.assertEqual(client.get('/api/auth/session').json['profile']['timezone'], 'Asia/Kolkata')

    def test_country_timezone_catalog_and_missing_os_database(self):
        from country_timezones import timezone_options, bundled_zone, normalize_timezone
        import zoneinfo
        choices = timezone_options()
        india = next(option for option in choices if option['value'] == 'Asia/Kolkata')
        self.assertEqual(india['label'], 'India — Kolkata (IST) (UTC+05:30)')
        old_path = zoneinfo.TZPATH
        try:
            zoneinfo.reset_tzpath(())
            bundled_zone.cache_clear()
            for option in choices:
                self.assertEqual(normalize_timezone(option['value']), option['value'])
                self.assertIsNotNone(bundled_zone(option['value']))
        finally:
            zoneinfo.reset_tzpath(old_path)
        response = self.send(self.visitor, '/register', initials='IZ', name='Invalid', password='long-password', security_answer='nickname', timezone='Not/AZone')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('IZ', self.directory())

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
        self.assertEqual(response.json['question'], 'What is your nickname?')
        self.assertEqual(self.visitor.get('/api/tasks/security-question?initials=BB', headers=self.headers).status_code, 200)
        response = self.visitor.post('/api/auth/reset-password', headers=self.headers,
                                     json={'initials': 'BB', 'security_answer': 'BLUE', 'new_password': 'new-long-password'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.visitor.get('/api/auth/session').json['user'])
        self.send(self.visitor, '/logout')
        self.assertEqual(self.send(self.visitor, '/login', initials='BB', password='new-long-password').status_code, 200)
        self.assertEqual(self.send(self.visitor, '/login', initials='BB', password='a-long-password').status_code, 401)

    def test_shared_task_completion_and_personal_removal(self):
        task = self.create(assignee='BB', participants=['AA'])
        self.assertIn(task['id'], [item['id'] for item in self.tasks()])
        self.assertIn('AA', next(item for item in self.tasks() if item['id'] == task['id'])['participants'])
        self.assertEqual(self.send(self.alice, f"/{task['id']}/membership", 'DELETE', version=1).status_code, 200)
        self.assertNotIn(task['id'], [item['id'] for item in self.tasks()])
        self.assertIn(task['id'], [item['id'] for item in self.send(self.bob, '', 'GET').json['tasks']])
        shared = next(item for item in self.send(self.bob, '', 'GET').json['tasks'] if item['id'] == task['id'])
        self.assertEqual(self.send(self.bob, f"/{task['id']}/completion", version=shared['version'], completed=True).status_code, 200)

    def test_comments_persist_and_mentions_add_tagged_users(self):
        task = self.create()
        response = self.send(self.alice, f"/{task['id']}/comments", body='Please review this @aa and @CR.')
        self.assertEqual(response.status_code, 201, response.json)
        updated = next(item for item in self.tasks() if item['id'] == task['id'])
        self.assertEqual(set(updated['participants']), {'AA', 'BB', 'CR'})
        self.assertEqual(updated['comments'][0]['author'], 'AA')
        self.assertEqual(updated['comments'][0]['body'], 'Please review this @aa and @CR.')
        self.assertIsNotNone(updated['comments'][0]['created_at'])

    def test_comment_rejects_unknown_mentions_without_saving(self):
        task = self.create()
        response = self.send(self.alice, f"/{task['id']}/comments", body='Can you check this @ZZ?')
        self.assertEqual(response.status_code, 400)
        updated = next(item for item in self.tasks() if item['id'] == task['id'])
        self.assertEqual(updated['comments'], [])
        self.assertNotIn('ZZ', updated['participants'])

    def test_mention_restores_task_removed_from_tagged_users_list(self):
        task = self.create(participants=['AA'])
        self.assertEqual(self.send(self.alice, f"/{task['id']}/membership", 'DELETE', version=1).status_code, 200)
        self.assertNotIn(task['id'], [item['id'] for item in self.tasks()])
        self.assertEqual(self.send(self.bob, f"/{task['id']}/comments", body='Bringing this back for @AA').status_code, 201)
        restored = next(item for item in self.tasks() if item['id'] == task['id'])
        self.assertIn('AA', restored['participants'])

    def test_cr_is_admin_and_can_delete_people(self):
        admin = self.app.test_client()
        self.assertEqual(self.send(admin, '/register', initials='CR', name='Chiranjiva Rao', password='a-long-password',
                                    security_question='Question', security_answer='answer').status_code, 200)
        self.send(admin, '/login', initials='CR', password='a-long-password')
        self.assertTrue(self.send(admin, '', 'GET').json['admin'])
        self.assertEqual(admin.delete('/api/admin/users/AA', headers=self.headers, json={}).status_code, 200)
        self.assertNotIn('AA', admin.get('/api/tasks', headers=self.headers).json['users'])
        self.assertEqual(self.send(self.alice, title='No longer available').status_code, 401)

    def test_persistence_after_app_restart(self):
        self.create()
        self.save_profile('IN', 'India user', 'Asia/Kolkata')
        app2 = Flask('restarted')
        directory, _, _ = register_tasks(app2, Mock(), self.get_db)
        self.assertEqual(directory()['IN']['timezone'], 'Asia/Kolkata')
        self.assertEqual(app2.test_client().get('/api/tasks').status_code, 401)
        self.assertEqual(app2.test_client().post('/api/tasks/login', json={'initials': 'BB', 'password': 'a-long-password'}, headers=self.headers).status_code, 200)

    def test_delete_permissions(self):
        task = self.create(assignee='AA')
        self.assertEqual(self.send(self.bob, f"/{task['id']}", 'DELETE', version=1).status_code, 403)
        self.assertEqual(self.send(self.alice, f"/{task['id']}", 'DELETE', version=1).status_code, 200)
        self.assertEqual(self.tasks(), [])
        self.assertTrue(self.socket.emit.called)


if __name__ == '__main__':
    unittest.main()
