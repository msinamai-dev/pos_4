
# views.py
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count


from .models import ReceiptSequence, UserActivity

SHORT_SESSION_THRESHOLD = timedelta(minutes=1)  # threshold for short sessions

def suspicious_short_sessions_view(request):
    """
    List users with very short sessions (login -> logout duration below threshold)
    """
    users = UserActivity.objects.filter(
        activity_type='logout',
        session_duration__lte=SHORT_SESSION_THRESHOLD
    ).order_by('-timestamp')
    
    return render(request, 'posApp/suspicious_short_sessions.html', {'users': users})


def suspicious_multiple_ips_view(request):
    """
    List users logging in from multiple IPs within last 24 hours
    """
    one_day_ago = timezone.now() - timedelta(days=1)
    
    users = (
        UserActivity.objects.filter(timestamp__gte=one_day_ago)
        .values('user__username')
        .annotate(ip_count=Count('ip_address', distinct=True))
        .filter(ip_count__gt=1)
    )
    
    return render(request, 'posApp/suspicious_multiple_ips.html', {'users': users})

from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError

def transfer_product_logic(product, destination_branch, quantity):
    """
    Safely transfer `quantity` of `product` to `destination_branch`.
    Only works for branches in the same shop.
    """
    if quantity <= 0:
        raise ValidationError("Quantity must be a positive integer.")

    if product.branch.shop.id != destination_branch.shop.id:
        raise ValidationError("Cannot transfer between branches of different shops.")

    with transaction.atomic():
        # Lock source product row
        src = product.__class__.objects.select_for_update().get(pk=product.pk)

        if src.stock < quantity:
            raise ValidationError(f"Insufficient stock. Available: {src.stock}")

        # Try to get destination product
        try:
            dest = product.__class__.objects.select_for_update().get(
                name=src.name,
                branch=destination_branch,
                shop=src.shop
            )
            dest.stock += quantity
            dest.save()
        except product.__class__.DoesNotExist:
            # Destination does not exist: create it
            try:
                dest = product.__class__.objects.create(
                    shop=src.shop,
                    branch=destination_branch,
                    name=src.name,
                    category=src.category,
                    price=src.price,
                    cprice=src.cprice,
                    code=src.code,
                    stock=quantity,
                    description=src.description,
                    inclusive_tax=src.inclusive_tax,
                    exclusive_tax=src.exclusive_tax,
                    status=src.status,
                )
            except IntegrityError:
                # If concurrent create happened, fetch and update
                dest = product.__class__.objects.select_for_update().get(
                    name=src.name,
                    branch=destination_branch,
                    shop=src.shop
                )
                dest.stock += quantity
                dest.save()

        # Decrement source stock
        src.stock -= quantity
        src.save()

        return dest

# utils/currency.py
from decimal import Decimal, ROUND_HALF_UP, getcontext
from django.core.cache import cache
from django.utils import timezone
from .models import ExchangeRate

BASE_CURRENCY = "USD"
CACHE_KEY = "exchange_rates_v1"
CACHE_TIMEOUT = 60 * 60  # 1 hour

getcontext().prec = 28

def get_rate(currency):
    """Return decimal rate relative to base. Rate for base returns Decimal('1')."""
    if not currency:
        return Decimal('1')
    if currency.upper() == BASE_CURRENCY:
        return Decimal('1')
    # try cache first
    rates = cache.get(CACHE_KEY)
    if rates is None:
        # refresh cache
        rates = {er.currency: er.rate for er in ExchangeRate.objects.all()}
        cache.set(CACHE_KEY, rates, CACHE_TIMEOUT)
    rate = rates.get(currency.upper())
    if rate is None:
        # fallback: fetch fresh and update cache
        try:
            er = ExchangeRate.objects.get(currency=currency.upper())
            rate = er.rate
            rates[currency.upper()] = rate
            cache.set(CACHE_KEY, rates, CACHE_TIMEOUT)
        except ExchangeRate.DoesNotExist:
            rate = Decimal('1')  # fallback -- but prefer admin to set proper rate
    return Decimal(rate)

def convert_amount(amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
    """Convert amount from `from_currency` to `to_currency`. Uses BASE_CURRENCY as intermediate.
    amount is a Decimal."""
    amount = Decimal(amount)
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return amount
    # convert from -> base, then base -> to
    if from_currency == BASE_CURRENCY:
        base_amount = amount
    else:
        from_rate = get_rate(from_currency)
        # amount_in_base = amount / from_rate  (because amount * (1/from_rate) => base)
        base_amount = (amount / from_rate)
    if to_currency == BASE_CURRENCY:
        return base_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    to_rate = get_rate(to_currency)
    # target = base_amount * to_rate
    target = (base_amount * to_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return target

# utils/codes.py
import random
import string
from datetime import datetime

def generate_unique_code(prefix="TXN", length=6):
    """
    Generate a unique transaction code like TXN-20251011-AB12CD
    """
    date_str = datetime.now().strftime("%Y%m%d")
    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
    return f"{prefix}-{date_str}-{random_str}"


from decimal import Decimal, ROUND_HALF_UP

def round_2dp(value):
    return Decimal(str(value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

def is_shift_open(shop=None, branch=None):
    from posApp.models import Shift

    qs = Shift.objects.filter(is_open=True)

    if shop:
        qs = qs.filter(shop=shop)

    if branch:
        qs = qs.filter(branch=branch)

    return qs.exists()
    
# utils.py
from django.db import transaction as db_transaction

def generate_receipt_number(shop, branch):
    """
    Atomically increments a per-(shop, branch) counter and returns
    a receipt number unique across shop+branch combinations.
    e.g. SHP1-BR2-000042
    """
    with db_transaction.atomic():
        seq, _ = ReceiptSequence.objects.select_for_update().get_or_create(
            shop=shop, branch=branch
        )
        seq.last_number += 1
        seq.save(update_fields=["last_number"])
        next_number = seq.last_number

    shop_code = getattr(shop, "code", None) or f"SHP{shop.id}"
    branch_code = getattr(branch, "code", None) or f"BR{branch.id}"

    return f"{shop_code}-{branch_code}-{next_number:06d}"


from decimal import Decimal
from .models import ShiftCurrency
# import shop_currencies from wherever it lives now (utils/helpers), NOT from views
#from .utils import shop_currencies

def shop_currencies(shop, branch):
    """USD first, then every currency the shop has an exchange rate for."""
    others = list(
        ExchangeRate.objects.filter(shop=shop, branch=branch,is_active=True)
        .exclude(currency__iexact="USD")
        .order_by("currency")
        .values_list("currency", flat=True)
        .distinct()
    )
    return ["USD"] + others


def sync_shift_currencies(shift):
    """Give an open shift a zero-opening line for every currency the shop has now."""
    from .models import ShiftCurrency

    existing = set(shift.currency_lines.values_list("currency", flat=True))
    for cur in shop_currencies(shift.shop, shift.branch):
        if cur not in existing:
            ShiftCurrency.objects.get_or_create(
                shift=shift,
                currency=cur,
                defaults={"opening_amount": Decimal("0.00")},
            )


def shift_close_currencies(shift):
    """Currencies that must be counted when closing this shift."""
    from .models import Transaction, Expense

    active = set(shop_currencies(shift.shop, shift.branch))
    lines = {l.currency: l for l in shift.currency_lines.all()}

    used = set(Transaction.objects.filter(shift=shift).values_list("currency", flat=True))
    used |= set(
        Expense.objects.filter(
            shop=shift.shop, branch=shift.branch, created_by=shift.user,
            created_at__gte=shift.opened_at,
        ).values_list("currency", flat=True)
    )

    required = {"USD"}
    for cur, line in lines.items():
        # keep it if the currency is still active, or cash was put in at opening
        if cur in active or (line.opening_amount or 0) != 0:
            required.add(cur)
    required |= used          # any sale or expense in it means it must be counted
    return sorted(required, key=lambda c: (c != "USD", c))


import logging
from django.db.models import Sum, F, ExpressionWrapper, DecimalField

logger = logging.getLogger(__name__)


def take_stock_snapshot(shift, kind):
    """Record the value of the branch's stock (at cost and at selling price)."""
    from .models import Products, StockSnapshot

    money = DecimalField(max_digits=18, decimal_places=2)
    agg = Products.objects.filter(shop=shift.shop, branch=shift.branch).aggregate(
        cost=Sum(ExpressionWrapper(F("stock") * F("cprice"), output_field=money)),
        retail=Sum(ExpressionWrapper(F("stock") * F("price"), output_field=money)),
    )
    StockSnapshot.objects.create(
        shop=shift.shop, branch=shift.branch, shift=shift, kind=kind,
        cost_value=agg["cost"] or 0, retail_value=agg["retail"] or 0,
    )

from datetime import datetime, time
from decimal import Decimal
from django.utils import timezone


def end_of_day(d):
    """A date -> the last moment of that day (timezone aware)."""
    return timezone.make_aware(datetime.combine(d, time.max))


def rate_at(shop, branch, currency, when=None):

    from .models import ExchangeRate, ExchangeRateHistory

    cur = (currency or "USD").upper().strip()
    if cur == "USD":
        return Decimal("1")
    when = when or timezone.now()

    qs = ExchangeRateHistory.objects.filter(shop=shop, currency=cur)
    if branch:
        qs = qs.filter(branch=branch)
    row = (qs.filter(effective_from__lte=when).order_by("-effective_from").first()
           or qs.order_by("effective_from").first())      # before the first record: oldest known rate
    if row:
        return Decimal(str(row.rate))

    cq = ExchangeRate.objects.filter(shop=shop, currency=cur)
    if branch:
        cq = cq.filter(branch=branch)
    cur_rate = cq.first()
    return Decimal(str(cur_rate.rate)) if cur_rate and cur_rate.rate else None