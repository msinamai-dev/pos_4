from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.utils.timezone import now, timedelta
from .models import ExchangeRateHistory, UserProfile

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from .models import UserActivity
from django.utils import timezone

@receiver(user_logged_in)
def log_user_login(sender, user, request, **kwargs):
    UserActivity.objects.create(
        user=user,
        activity_type='login',
        ip_address=get_client_ip(request),
    )

@receiver(user_logged_out)
def log_user_logout(sender, user, request, **kwargs):
    # Get last login activity for session duration
    last_login = UserActivity.objects.filter(user=user, activity_type='login').last()
    duration = None
    if last_login:
        duration = timezone.now() - last_login.timestamp

    UserActivity.objects.create(
        user=user,
        activity_type='logout',
        ip_address=get_client_ip(request),
        session_duration=duration
    )

# Helper to get client IP
def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ExchangeRate, Shift
from .utils import sync_shift_currencies

logger = logging.getLogger(__name__)


@receiver(post_save, sender=ExchangeRate)
def add_zero_line_to_open_shifts(sender, instance, **kwargs):
    """When a rate is added or reactivated, open shifts get a zero-opening line for it."""
    if not instance.is_active:
        return

    def run():
        try:
            shifts = Shift.objects.filter(
                shop=instance.shop, branch=instance.branch, is_closed=False
            )
            for shift in shifts:
                sync_shift_currencies(shift)
        except Exception:
            logger.exception("Could not sync shift currencies")

    # wait until the rate is committed so shop_currencies() can see it
    transaction.on_commit(run)

@receiver(post_save, sender=ExchangeRate)
def log_rate_history(sender, instance, **kwargs):
    last = (ExchangeRateHistory.objects
            .filter(shop_id=instance.shop_id, branch_id=instance.branch_id, currency=instance.currency)
            .order_by("-effective_from").first())
    if last is None or last.rate != instance.rate:
        ExchangeRateHistory.objects.create(
            shop_id=instance.shop_id, branch_id=instance.branch_id,
            currency=instance.currency, rate=instance.rate,
        )

