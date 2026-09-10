def render_follow_up_2(recipient_name: str, company: str, title: str = None) -> dict:
    name_str = recipient_name.split()[0] if recipient_name else "there"
    company_str = company if company else "your company"

    subject = ""  # Empty — stays in the same thread

    body_html = f"""<p>Hi {name_str},</p>

<p>Just following up on my application and previous notes.</p>

<p>I know things might be busy on your end, but I'm still very keen on exploring engineering roles at <b>{company_str}</b>. I've had the chance to work across the stack—from building AI/agent workflows and backend systems to shipping customer-facing web products.</p>

<p>I'd be glad to go through the standard interview process and demonstrate how my background aligns with your engineering needs.</p>

<p>Would it be possible to connect for an interview opportunity?</p>

<p>Best,<br>
Keval Ambani<br>
+91-7439459385<br>
<a href="https://www.linkedin.com/in/keval-ambani-9ba99532a">LinkedIn</a></p>"""

    body_text = f"""Hi {name_str},

Just following up on my application and previous notes.

I know things might be busy on your end, but I'm still very keen on exploring engineering roles at {company_str}. I've had the chance to work across the stack—from building AI/agent workflows and backend systems to shipping customer-facing web products.

I'd be glad to go through the standard interview process and demonstrate how my background aligns with your engineering needs.

Would it be possible to connect for an interview opportunity?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a"""

    return {
        "subject": subject,
        "body_html": body_html.strip(),
        "body_text": body_text.strip(),
    }
