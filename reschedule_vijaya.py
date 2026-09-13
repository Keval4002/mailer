import sqlite3

def reschedule():
    conn = sqlite3.connect('mailtfoutofit/data/mail_scheduler.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Find Vijayalaxmi
    cursor.execute("SELECT id, name, email FROM contacts WHERE name LIKE '%Vijayalaxmi%' OR email LIKE '%vijayalaxmi%'")
    rows = cursor.fetchall()
    if not rows:
        print("Vijayalaxmi not found")
        return
        
    for r in rows:
        print(f"Found Contact: {dict(r)}")
        contact_id = r['id']
        
        # Get pending jobs
        cursor.execute("SELECT id, subject, status, scheduled_at, requested_scheduled_at FROM mail_jobs WHERE contact_id = ? AND status IN ('pending', 'gmail_scheduled')", (contact_id,))
        jobs = cursor.fetchall()
        
        # Update the 13th Sept 9 am job to today 4 pm
        cursor.execute('''
            UPDATE mail_jobs 
            SET scheduled_at = '2026-09-12T10:30:00+00:00',
                requested_scheduled_at = '2026-09-12T16:00:00+05:30'
            WHERE contact_id = ? AND requested_scheduled_at = '2026-09-13T09:00:00+05:30'
        ''', (contact_id,))
        conn.commit()
        print(f"Updated {cursor.rowcount} job(s) for Vijayalaxmi to today 4 pm")

if __name__ == '__main__':
    reschedule()
