def render_first_email(recipient_name: str, company: str, title: str = None) -> dict:
    name_str = recipient_name.split()[0] if recipient_name else "there"
    company_str = company if company else "your company"

    subject = f"Application: Engineering Opportunities at {company_str} / Keval Ambani"

    body_html = f"""<p>Hi {name_str},</p>

<p>I recently submitted my application for an engineering role at <b>{company_str}</b> and wanted to reach out directly to introduce myself.</p>

<p>I'm Keval Ambani, a Computer Engineering student at Thapar Institute (2027). I'm reaching out because I've been building and shipping full-stack products with a strong focus on AI, and I'm very interested in the work your team is doing.</p>

<p>To give you a quick sense of my background:</p>
<ul>
  <li>I built an AI business-automation agent running for 2 businesses.</li>
  <li>I built a timetable platform used by 10,000+ students.</li>
  <li>During my internship at Neural Network Labs, I built AI-powered presentation workflows.</li>
  <li>I've also worked as a freelance developer on a global B2B marketplace and production web projects.</li>
</ul>

<p>I'm particularly interested in software engineering roles where I can work close to the product and build things end-to-end, whether that's an internship now or a full-time role after I graduate in 2027.</p>

<p><b>Would you be open to a brief conversation about any upcoming engineering opportunities at {company_str}?</b></p>

<p>Best,<br>
Keval Ambani<br>
+91-7439459385<br>
<a href="https://www.linkedin.com/in/keval-ambani-9ba99532a">LinkedIn</a> |
<a href="https://github.com/Keval4002">GitHub</a></p>"""

    body_text = f"""Hi {name_str},

I recently submitted my application for an engineering role at {company_str} and wanted to reach out directly to introduce myself.

I'm Keval Ambani, a Computer Engineering student at Thapar Institute (2027). I'm reaching out because I've been building and shipping full-stack products with a strong focus on AI, and I'm very interested in the work your team is doing.

To give you a quick sense of my background:
- I built an AI business-automation agent running for 2 businesses.
- I built a timetable platform used by 10,000+ students.
- During my internship at Neural Network Labs, I built AI-powered presentation workflows.
- I've also worked as a freelance developer on a global B2B marketplace and production web projects.

I'm particularly interested in software engineering roles where I can work close to the product and build things end-to-end, whether that's an internship now or a full-time role after I graduate in 2027.

Would you be open to a brief conversation about any upcoming engineering opportunities at {company_str}?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a
GitHub: https://github.com/Keval4002"""

    return {
        "subject": subject,
        "body_html": body_html.strip(),
        "body_text": body_text.strip(),
    }
