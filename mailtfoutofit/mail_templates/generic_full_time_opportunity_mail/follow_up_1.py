def render_follow_up_1(recipient_name: str, company: str, title: str = None) -> dict:
    name_str = recipient_name.split()[0] if recipient_name else "there"
    company_str = company if company else "your company"

    subject = ""  # Empty — stays in the same thread as the first email

    body_html = f"""<p>Hi {name_str},</p>

<p>Just bumping this up in case it got buried. I know how busy things get!</p>

<p>I'm still very interested in contributing to <b>{company_str}</b>. As I mentioned in my application, my most recent project is an AI agent I built using LangGraph + FastMCP that's actively running for 2 businesses in India, cutting operational effort by 70%+.</p>

<p>I'm confident I can ship fast and deliver real impact from day one. Would you be open to discussing the opportunities?</p>

<p>Best,<br>
Keval Ambani<br>
+91-7439459385<br>
<a href="https://www.linkedin.com/in/keval-ambani-9ba99532a">LinkedIn</a></p>"""

    body_text = f"""Hi {name_str},

Just bumping this up in case it got buried. I know how busy things get!

I'm still very interested in contributing to {company_str}. As I mentioned in my application, my most recent project is an AI agent I built using LangGraph + FastMCP that's actively running for 2 businesses in India, cutting operational effort by 70%+.

I'm confident I can ship fast and deliver real impact from day one. Would you be open to discussing the opportunities?

Best,
Keval Ambani
+91-7439459385
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a"""

    return {
        "subject": subject,
        "body_html": body_html.strip(),
        "body_text": body_text.strip(),
    }
