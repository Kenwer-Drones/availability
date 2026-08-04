"""
Daily email report: sends total hours worked by each user for the day.
Triggered by Fly.io scheduled machine or cron.
"""
import sqlite3
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

# Config from environment variables
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
EMAIL_TO = os.environ.get('EMAIL_TO', 'time@kenwer.com')
EMAIL_FROM = os.environ.get('EMAIL_FROM', SMTP_USER)

DB_PATH = '/data/availability.db' if os.path.isdir('/data') else 'availability.db'


def get_todays_summary():
    """Get hours per user for today (UTC date)."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT initials, name, COUNT(*) as hours FROM slots WHERE date_utc = ? GROUP BY initials ORDER BY initials',
        (today,)
    ).fetchall()
    conn.close()
    return today, rows


def send_email(today, rows):
    """Send the daily summary email."""
    if not SMTP_USER or not SMTP_PASS:
        print('SMTP credentials not configured. Set SMTP_USER and SMTP_PASS env vars.')
        return

    # Build email body
    if not rows:
        body = f"No availability logged for {today}."
    else:
        lines = [f"Daily Availability Report - {today}\n", "=" * 40, ""]
        for row in rows:
            name = row['name'] or row['initials']
            lines.append(f"  {row['initials']} - {name}: {row['hours']} / 7 hours")
        lines.append("")
        lines.append("=" * 40)
        total_users = len(rows)
        total_hours = sum(row['hours'] for row in rows)
        lines.append(f"\nTotal: {total_users} users, {total_hours} hours logged")
        body = "\n".join(lines)

    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    msg['Subject'] = f'Team Availability Report - {today}'
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        print(f'Email sent to {EMAIL_TO}')
    except Exception as e:
        print(f'Failed to send email: {e}')


if __name__ == '__main__':
    today, rows = get_todays_summary()
    send_email(today, rows)
