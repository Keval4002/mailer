def render_first_email(recipient_name: str, company: str, title: str = None) -> dict:
    name_str = recipient_name.split()[0] if recipient_name else "there"
    company_str = company if company else "your team"

    subject = f"Exploring Engineering Opportunities at {company_str}"

    body_html = f"""
    <p>Hi {name_str},</p>

    <p>I hope you're doing well!</p>

    <p>I'm Keval Ambani, a Computer Engineering student at Thapar Institute of Engineering and Technology
    (Batch 2027), and I'm reaching out because I'm genuinely excited about the engineering work happening at
    <b>{company_str}</b>.</p>

    <p>Some highlights from my recent work:</p>
    <ul>
      <li>Built <b>VyapaarSetu</b>, an AI agent using <b>LangGraph deep agents + FastMCP</b> that automates
      lead qualification, invoicing, scheduling and order fulfillment for 2 active stores across India,
      <b>reducing repetitive operational effort by 70%+</b>. Integrated WhatsApp Business, Google Calendar
      and Razorpay through MCP-powered tool calling.</li>
      <li>At <b>Neural Network Labs</b> (Full Stack Intern), built AI-powered presentation generation workflows
      using Gemini and async workers, reducing creation time to under 120 seconds and contributing to
      <b>20%+ growth in trial adoption</b>.</li>
      <li>Freelanced at <b>GlassFactory</b> building a Tariff Calculator and implementing adaptive bitrate
      streaming via AWS MediaConvert for a global B2B marketplace.</li>
    </ul>

    <p>My stack: <b>React, Next.js, Node.js, FastAPI, LangGraph, Python, PostgreSQL, Docker</b>, and
    I'm actively building with agentic AI tools.</p>

    <p>You can explore my work here:
    <a href="https://github.com/Keval4002">GitHub</a> |
    <a href="https://www.linkedin.com/in/keval-ambani-9ba99532a">LinkedIn</a>
    </p>

    <p><b>Would you be open to a brief chat about any full-time or internship engineering roles on your team?</b></p>

    <p>Best regards,<br>
    Keval Ambani<br>
    +91-7439459385</p>
    """

    body_text = f"""Hi {name_str},

I hope you're doing well!

I'm Keval Ambani, a Computer Engineering student at Thapar Institute of Engineering and Technology (Batch 2027), and I'm reaching out because I'm genuinely excited about the engineering work happening at {company_str}.

Some highlights from my recent work:

- Built VyapaarSetu, an AI agent using LangGraph deep agents + FastMCP that automates lead qualification, invoicing, scheduling and order fulfillment for 2 active stores across India, reducing repetitive operational effort by 70%+. Integrated WhatsApp Business, Google Calendar and Razorpay through MCP-powered tool calling.
- At Neural Network Labs (Full Stack Intern), built AI-powered presentation generation workflows using Gemini and async workers, reducing creation time to under 120 seconds and contributing to 20%+ growth in trial adoption.
- Freelanced at GlassFactory building a Tariff Calculator and implementing adaptive bitrate streaming via AWS MediaConvert for a global B2B marketplace.

My stack: React, Next.js, Node.js, FastAPI, LangGraph, Python, PostgreSQL, Docker, and I'm actively building with agentic AI tools.

You can explore my work here:
- GitHub: https://github.com/Keval4002
- LinkedIn: https://www.linkedin.com/in/keval-ambani-9ba99532a

Would you be open to a brief chat about any full-time or internship engineering roles on your team?

Best regards,
Keval Ambani
+91-7439459385
"""

    return {
        "subject": subject,
        "body_html": body_html.strip(),
        "body_text": body_text.strip()
    }
