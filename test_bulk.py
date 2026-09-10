import requests
import json

contacts = [
    {"email": "racsingh@ea.com", "name": "Rachit Singh", "company": "Electronic Arts", "title": "Campus Recruitment Head"},
    {"email": "ntariyal@ea.com", "name": "Neha", "company": "Electronic Arts", "title": "Talent Acquisition"},
    {"email": "kchaganti@ea.com", "name": "Krishnakanth", "company": "Electronic Arts", "title": "Senior Engineering Manager"},
    {"email": "ngonig@ea.com", "name": "Navindra", "company": "Electronic Arts", "title": "Director of Engineering"},
    {"email": "magupta@ea.com", "name": "Manoj Gupta", "company": "Electronic Arts", "title": "Senior Director of Engineering"}
]

subject_0 = "Application: Engineering Opportunities at {company} / Keval Ambani"
body_0 = """Hi {name},

I recently submitted my application for an engineering role at {company} and wanted to reach out directly to introduce myself.

I'm Keval Ambani, a Computer Engineering student at Thapar Institute (2027). I'm reaching out because I've been building and shipping full-stack products with a strong focus on AI, and I'm very interested in the work your team is doing.

To give you a quick sense of my background:
- I built an AI business-automation agent running for 2 businesses.
- I built a timetable platform used by 10,000+ students.
- During my internship at Neural Network Labs, I built AI-powered presentation workflows.
- I've also worked as a freelance developer on a global B2B marketplace and production web projects.

I'm particularly interested in software engineering roles where I can work close to the product and build things end-to-end, whether that's an internship now or a full-time role after I graduate in 2027.

Would you be open to a brief conversation about any upcoming engineering opportunities at {company}?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a
GitHub: https://github.com/Keval4002"""

subject_1 = ""
body_1 = """Hi {name},

Just bumping this up in case it got buried. I know how busy things get!

I'm still very interested in contributing to {company}. As I mentioned in my application, my most recent project is an AI agent I built using LangGraph + FastMCP that's actively running for 2 businesses in India, cutting operational effort by 70%+.

I'm confident I can ship fast and deliver real impact from day one. Would you be open to discussing the opportunities?

Best,
Keval Ambani
+91-7439459385"""

subject_2 = ""
body_2 = """Hi {name},

Just following up on my application and previous notes.

I know things might be busy on your end, but I'm still very keen on exploring engineering roles at {company}. I've had the chance to work across the stack—from building AI/agent workflows and backend systems to shipping customer-facing web products.

I'd be glad to go through the standard interview process and demonstrate how my background aligns with your engineering needs.

Would it be possible to connect for an interview opportunity?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a"""

files = {
    'contacts_json': (None, json.dumps(contacts)),
    'start_date': (None, '2026-09-11T09:00'),
    'subject_0': (None, subject_0),
    'body_0': (None, body_0),
    'include_followups': (None, 'true'),
    'attach_resume': (None, 'true'),
    'subject_1': (None, subject_1),
    'body_1': (None, body_1),
    'subject_2': (None, subject_2),
    'body_2': (None, body_2)
}

response = requests.post('http://127.0.0.1:8000/jobs/bulk', files=files)
print("Status Code:", response.status_code)
print("Response:", response.text)
