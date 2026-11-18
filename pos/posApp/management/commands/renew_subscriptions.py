from django.core.management.base import BaseCommand
from django.utils import timezone
from posApp.models import Subscription


class Command(BaseCommand):
    help = "Auto-renew active subscriptions that are due for renewal"

    def handle(self, *args, **kwargs):
        today = timezone.now().date()
        subs = Subscription.objects.filter(
            auto_renew=True,
            next_billing_date__lte=today,
            is_active=True,
        )

        for sub in subs:
            success = sub.renew(auto=True)
            if success:
                self.stdout.write(self.style.SUCCESS(
                    f"✅ Renewed {sub.user.username} → {sub.plan.name}"
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"❌ Renewal failed for {sub.user.username}"
                ))
