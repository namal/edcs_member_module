import logging

from django.conf import settings
from django.contrib import messages
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def notify_user_created(request, user):
    """Send the welcome message only after the new account has committed."""
    email = (user.email or "").strip()
    if not email:
        return
    body = render_to_string(
        "administration/emails/user_created.txt",
        {
            "name": user.get_full_name() or user.get_username(),
            "username": user.get_username(),
            "login_url": request.build_absolute_uri(reverse("login")),
            "is_active": user.is_active,
        },
    )

    def send_welcome():
        try:
            sent = send_mail(
                subject="Your EDCS account has been created",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            # Delivery must not turn a successfully saved account into an error page.
            logger.exception("Welcome email delivery failed for user %s", user.pk)
            sent = 0
        if sent:
            messages.success(request, "A welcome email has been sent to the new user.")
        else:
            messages.warning(
                request,
                "The user was saved, but the welcome email could not be sent. "
                "Check the email settings.",
            )

    transaction.on_commit(send_welcome)
