import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from mail_templates.generic_full_time_opportunity_mail.first_email import render_first_email
from mail_templates.generic_full_time_opportunity_mail.follow_up_1 import render_follow_up_1
from mail_templates.generic_full_time_opportunity_mail.follow_up_2 import render_follow_up_2


def test_templates():
    print("Testing First Email...")
    first = render_first_email("Jane Doe", "Stripe")
    assert "Stripe" in first["subject"]
    assert "Jane" in first["body_text"]
    assert "VyapaarSetu" in first["body_text"], "Must mention VyapaarSetu from resume"
    assert "LangGraph" in first["body_text"], "Must mention LangGraph"
    assert "Neural Network Labs" in first["body_text"], "Must mention real work experience"
    assert "https://github.com/Keval4002" in first["body_html"]
    assert "https://www.linkedin.com/in/keval-ambani-9ba99532a" in first["body_html"]
    assert "Keval Ambani" in first["body_text"]
    print("First Email passed.\n")

    print("Testing Follow Up 1...")
    fu1 = render_follow_up_1("John Smith", "Google")
    assert "Google" in fu1["subject"]
    assert "John" in fu1["body_text"]
    assert "VyapaarSetu" in fu1["body_text"], "Follow-up must still reference real project"
    assert "Keval Ambani" in fu1["body_text"]
    print("Follow Up 1 passed.\n")

    print("Testing Follow Up 2...")
    fu2 = render_follow_up_2("Alex", "OpenAI")
    assert "Alex" in fu2["body_text"]
    assert "https://github.com/Keval4002" in fu2["body_html"]
    assert "https://www.linkedin.com/in/keval-ambani-9ba99532a" in fu2["body_html"]
    print("Follow Up 2 passed.\n")

    print("Testing edge cases...")
    empty_name = render_first_email("", "Acme")
    assert "Hi there" in empty_name["body_text"], "Should default to 'there' for missing name"

    empty_company = render_first_email("Bob", "")
    assert "your team" in empty_company["body_text"], "Should default to 'your team' for missing company"
    print("Edge cases passed.\n")

    print("All templates built and verified successfully!")


if __name__ == "__main__":
    test_templates()
