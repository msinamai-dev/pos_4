from decimal import Decimal

from django.core.management.base import BaseCommand

from posApp.models import ExchangeRate, ExchangeRateAudit, ExchangeRateHistory, Expense
from posApp.utils import rate_at


class Command(BaseCommand):
    help = "Rebuild the exchange rate history and give old expenses a saved rate and USD value."

    def handle(self, *args, **options):
        def latest_before(shop_id, branch_id, currency, when=None):
            qs = ExchangeRateHistory.objects.filter(shop_id=shop_id, branch_id=branch_id, currency=currency)
            if when:
                qs = qs.filter(effective_from__lte=when)
            return qs.order_by("-effective_from").first()

        made = 0

        # 1) past changes from the audit log
        for a in ExchangeRateAudit.objects.filter(new_rate__gt=0).order_by("created_at"):
            last = latest_before(a.shop_id, a.branch_id, a.currency, a.created_at)
            if last is None or last.rate != a.new_rate:
                ExchangeRateHistory.objects.create(
                    shop_id=a.shop_id, branch_id=a.branch_id, currency=a.currency,
                    rate=a.new_rate, effective_from=a.created_at,
                )
                made += 1

        # 2) the rates that are live now
        for er in ExchangeRate.objects.all():
            last = latest_before(er.shop_id, er.branch_id, er.currency)
            if last is None or last.rate != er.rate:
                ExchangeRateHistory.objects.create(
                    shop_id=er.shop_id, branch_id=er.branch_id, currency=er.currency,
                    rate=er.rate, effective_from=er.updated_at,
                )
                made += 1

        # 3) expenses without a saved rate
        fixed = 0
        for e in Expense.objects.filter(amount_usd=0, amount__gt=0):
            rate = rate_at(e.shop, e.branch, e.currency, e.created_at) or Decimal("1")
            Expense.objects.filter(pk=e.pk).update(
                exchange_rate=rate,
                amount_usd=(Decimal(str(e.amount)) / rate).quantize(Decimal("0.01")),
            )
            fixed += 1

        self.stdout.write(self.style.SUCCESS(f"{made} history rows created, {fixed} expenses updated."))