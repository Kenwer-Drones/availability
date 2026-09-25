"""One-time, transaction-safe restore from a private Render environment value.

The snapshot contains database rows, including password hashes. Keep it out of
the repository and remove the environment variable after a successful restore.
"""

import json
import os

from psycopg2.extras import execute_values


TABLES = (
    ("team_users", ("initials", "name", "timezone")),
    ("task_accounts", ("initials", "password_hash", "security_question", "security_answer_hash")),
    ("slots", ("date_utc", "hour_utc", "initials", "name")),
    ("tasks", ("id", "title", "assignee", "created_by", "deadline", "position", "completed", "completed_at", "version")),
    ("task_participants", ("task_id", "initials", "hidden")),
    ("task_comments", ("id", "task_id", "author", "body", "created_at")),
    ("task_checklist", ("id", "task_id", "label", "completed", "position")),
    ("task_column_order", ("owner", "order_json")),
    ("task_dependencies", ("task_id", "prerequisite_id", "created_by", "source_side", "target_side")),
)


def restore_if_requested(get_db):
    raw = os.environ.get("AVAILABILITY_RESTORE_JSON")
    target = os.environ.get("AVAILABILITY_RESTORE_TARGET")
    if not raw or not target:
        return False

    snapshot = json.loads(raw)
    if snapshot.get("format") != "kenwer-availability-v1":
        raise ValueError("Unsupported availability snapshot format")
    data = snapshot.get("tables")
    if not isinstance(data, dict) or set(data) != {name for name, _ in TABLES}:
        raise ValueError("Availability snapshot has missing or unexpected tables")

    conn = get_db()
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                if cursor.fetchone()[0] != target:
                    return False
                cursor.execute("SELECT pg_advisory_xact_lock(73519024)")
                for table, columns in TABLES:
                    rows = data[table]
                    if not isinstance(rows, list) or any(
                        not isinstance(row, list) or len(row) != len(columns) for row in rows
                    ):
                        raise ValueError("Invalid availability snapshot rows for " + table)
                    if not rows:
                        continue
                    names = ", ".join('"' + column + '"' for column in columns)
                    statement = f'INSERT INTO public."{table}" ({names}) VALUES %s ON CONFLICT DO NOTHING'
                    execute_values(cursor, statement, rows, page_size=100)
        print("Availability snapshot restored into", target)
        return True
    finally:
        conn.close()
