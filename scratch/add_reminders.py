import re
import sys

def add_service_methods():
    with open("d:\\Projects\\Mailer\\mailtfoutofit\\mail_scheduler\\service.py", "r", encoding="utf-8") as f:
        content = f.read()

    new_methods = """
    def list_reminders(self):
        with self.connect() as conn:
            cursor = conn.execute("SELECT * FROM reminders ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def create_reminder(self, title: str, content_text: str):
        import uuid
        from datetime import datetime
        now = to_storage_datetime(datetime.now(UTC))
        reminder_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reminders (id, title, content_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (reminder_id, title, content_text, now, now)
            )
            cursor = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
            return dict(cursor.fetchone())

    def delete_reminder(self, reminder_id: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

"""
    # Append methods at the end of the class. It ends at the end of the file.
    content += new_methods
    
    with open("d:\\Projects\\Mailer\\mailtfoutofit\\mail_scheduler\\service.py", "w", encoding="utf-8") as f:
        f.write(content)

def add_app_routes():
    with open("d:\\Projects\\Mailer\\mailtfoutofit\\mail_scheduler\\app.py", "r", encoding="utf-8") as f:
        content = f.read()

    new_routes = """
    class ReminderCreate(BaseModel):
        title: str
        content_text: str

    @app.get("/api/reminders")
    async def get_reminders():
        return {"reminders": service.list_reminders()}

    @app.post("/api/reminders")
    async def create_reminder(payload: ReminderCreate):
        return service.create_reminder(payload.title, payload.content_text)

    @app.delete("/api/reminders/{reminder_id}")
    async def delete_reminder(reminder_id: str):
        service.delete_reminder(reminder_id)
        return {"success": True}

    @app.post("/api/upload-image")
    async def upload_image(file: UploadFile = File(...)):
        import os
        import uuid
        upload_dir = Path(__file__).resolve().parent.parent.parent / "frontend" / "public" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_ext = os.path.splitext(file.filename)[1]
        unique_name = f"{uuid.uuid4().hex}{file_ext}"
        file_path = upload_dir / unique_name
        with open(file_path, "wb") as buffer:
            import shutil
            shutil.copyfileobj(file.file, buffer)
        # In dev mode, public folder is served by Vite. 
        # In prod, it is copied to dist/uploads. We return the relative URL.
        return {"url": f"/uploads/{unique_name}"}

    # Serve React Frontend
"""
    # Replace the # Serve React Frontend comment with the new routes.
    content = content.replace("    # Serve React Frontend\n", new_routes)

    with open("d:\\Projects\\Mailer\\mailtfoutofit\\mail_scheduler\\app.py", "w", encoding="utf-8") as f:
        f.write(content)

add_service_methods()
add_app_routes()
