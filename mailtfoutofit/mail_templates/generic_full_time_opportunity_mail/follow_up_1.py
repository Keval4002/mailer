def render_follow_up_1(recipient_name: str, company: str, title: str = None) -> dict:
    name_str = recipient_name.split()[0] if recipient_name else "there"
    company_str = company if company else "your team"

    subject = f"Re: Exploring Engineering Opportunities at {company_str}"

    body_html = f"""
    <p>Hi {name_str},</p>

    <p>Just bumping this up in case it got buried. Totally understand how busy things get!</p>

    <p>I'm still very interested in contributing to <b>{company_str}</b>. To give you a quick sense of what I bring:
    my most recent project, <b>VyapaarSetu</b>, is an AI agent I built using <b>LangGraph + FastMCP</b> that's
    actively running for 2 businesses in India, automating their lead qualification, invoicing and scheduling
    end-to-end and cutting operational effort by 70%+.</p>

    <p>I'm confident I can ship fast and deliver real impact from day one.</p>

    <p><b>Would a 15-minute call this week work for you?</b></p>

    <p>Best,<br>
    Keval Ambani<br>
    +91-7439459385</p>
    """

    body_text = f"""Hi {name_str},

Just bumping this up in case it got buried. Totally understand how busy things get!

I'm still very interested in contributing to {company_str}. To give you a quick sense of what I bring:
my most recent project, VyapaarSetu, is an AI agent I built using LangGraph + FastMCP that's actively running for 2 businesses in India, automating their lead qualification, invoicing and scheduling end-to-end and cutting operational effort by 70%+.

I'm confident I can ship fast and deliver real impact from day one.

Would a 15-minute call this week work for you?

Best,
Keval Ambani
+91-7439459385
"""

    return {
        "subject": subject,
        "body_html": body_html.strip(),
        "body_text": body_text.strip()
    }
