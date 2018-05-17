from django.core.mail import send_mail
from django.conf import settings

import mistune


def send_email(message):
    html_message = mistune.markdown(message['content'], hard_wrap=True)
    send_mail(
        message['title'],
        message['content'],
        settings.EMAIL_HOST_USER,
        message['recipients'],
        fail_silently=True,
        html_message=html_message,
    )
