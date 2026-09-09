from .first_email import render_first_email
from .follow_up_1 import render_follow_up_1
from .follow_up_2 import render_follow_up_2

def build_email(step: str, recipient_name: str, company: str, title: str = None) -> dict:
    if step == "first_email":
        return render_first_email(recipient_name, company, title)
    elif step == "follow_up_1":
        return render_follow_up_1(recipient_name, company, title)
    elif step == "follow_up_2":
        return render_follow_up_2(recipient_name, company, title)
    else:
        raise ValueError(f"Unknown step: {step}")
