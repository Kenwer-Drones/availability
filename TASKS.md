# Team Tasks

Availability remains at `/`. The **Tasks** navigation link opens `/tasks`.

- Each registered profile has a column, including people with no tasks.
- Any signed-in user can create tasks for themselves, another user, or Not assigned.
- Titles and deadlines can be edited by signed-in team members. Only the current
  assignee can reassign an assigned task, preventing a completion-permission bypass.
- Only the assignee can complete or reopen a task. Completed tasks are available
  with **Show completed** and must be reopened before editing.
- Deadlines are entered in `America/Phoenix` (UTC-7 year round), stored in UTC,
  and displayed in Arizona time. The editor also previews the signed-in user's
  timezone. Arizona 08:00 is India 20:30 on the same date.
- Earlier deadlines sort first; undated tasks come last. Up/down buttons change
  the order within the same assignee and exact deadline, including undated tasks.
- The creator or assignee may delete a task, with confirmation.

## Accounts

The existing availability page uses self-declared initials, not authentication.
Tasks therefore have separate password-protected accounts: **Sign in > Create
account**, using the person's availability initials. Passwords require at least
10 characters and are hashed with Werkzeug. Server-side sessions expire after
14 days. Completion never trusts initials supplied by the browser.

Initial account registration is self-service and first-claim, consistent with
the existing trusted-team profile setup. It does not verify a person's real-world
identity: coordinate initial registration with the team. Once claimed, that
initials account cannot be claimed again. This is not an invitation-only or
email-verified identity system. Password recovery is not yet provided; do not
share passwords. Availability itself remains unauthenticated.

## Persistence And Deployment

The existing PostgreSQL database on Render stores `team_users`, `task_accounts`,
`task_sessions`, and `tasks`; SQLite is used locally. Tables are added at startup
without altering or deleting existing slot data. Existing slot owners seed the
user directory. Profiles previously held only in memory with no saved slots need
to visit Availability once to register in the persistent directory.

No new Python dependencies or Render configuration are required. Deploy the new
commit using the existing service's normal deployment process. PostgreSQL-backed
production uses Secure, HttpOnly, SameSite cookies and requires HTTPS.

Board updates are broadcast over Socket.IO. A 30-second refresh provides fallback
when the connection or CDN is unavailable. Concurrent mutations are serialized
in the database; task versions reject stale edits. Sign-in attempts are throttled
per account and remote address in each worker; large multi-worker deployments
should use a shared rate limiter and a verified identity provider.

## Tests

```sh
python -m unittest discover -s tests -v
node --check static/tasks.js
```

With Playwright and Chromium installed:

```sh
node tests/tasks_browser.cjs
```

Browser tests start a temporary local server/database and clean them up on exit.
They do not modify production data. Screenshots are written to `/tmp`.
