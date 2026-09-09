def render_follow_up_2(recipient_name: str, company: str, title: str = None) -> dict:
    name_str = recipient_name.split()[0] if recipient_name else "there"

    subject = ""  # Empty subject to stay in the same thread

    body_html = f"""
    <p>Hi {name_str},</p>

    <p>I'll keep this short. I figure now probably isn't the right time, and I don't want to clog your inbox.</p>

    <p>I'll stop following up here. But if you're ever looking for an engineer who can move fast, ship real
    products, and build intelligently with modern AI tools, feel free to reach back out. I'd love to be useful
    to <b>your team</b> when the timing is right.</p>

    <p>Thanks for your time.</p>

    <p>Keval Ambani<br>
    <a href="https://www.linkedin.com/in/keval-ambani-9ba99532a">LinkedIn</a> |
    <a href="https://github.com/Keval4002">GitHub</a></p>
    """

    body_text = f"""Hi {name_str},

I'll keep this short. I figure now probably isn't the right time, and I don't want to clog your inbox.

I'll stop following up here. But if you're ever looking for an engineer who can move fast, ship real products, and build intelligently with modern AI tools, feel free to reach back out. I'd love to be useful to your team when the timing is right.

Thanks for your time.

Keval Ambani
LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a | GitHub: https://github.com/Keval4002
"""

    return {
        "subject": subject,
        "body_html": body_html.strip(),
        "body_text": body_text.strip()
    }
