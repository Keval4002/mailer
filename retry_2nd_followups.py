import sqlite3
import datetime

conn = sqlite3.connect('mailtfoutofit/data/mail_scheduler.db')
cursor = conn.cursor()

cursor.execute('''
    SELECT id, root_job_id, scheduled_at, status 
    FROM mail_jobs 
    WHERE root_job_id IS NOT NULL 
    ORDER BY root_job_id, scheduled_at ASC
''')
rows = cursor.fetchall()

groups = {}
for r in rows:
    job_id, root_job_id, scheduled_at, status = r
    if root_job_id not in groups:
        groups[root_job_id] = []
    groups[root_job_id].append(r)

failed_second_followups = []
for root_job_id, jobs in groups.items():
    if len(jobs) >= 3:
        third_job = jobs[2]
        if third_job[3] == 'failed':
            failed_second_followups.append(third_job[0])

print(f"Found {len(failed_second_followups)} failed 2nd followups.")

now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
for j_id in failed_second_followups:
    cursor.execute("UPDATE mail_jobs SET status='pending', scheduled_at=? WHERE id=?", (now_iso, j_id))

conn.commit()
print("Updated successfully.")
