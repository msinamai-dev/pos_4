from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal

from django.db.models import Sum, F, Case, When, ExpressionWrapper, DecimalField
from django.utils import timezone

from .models import (
    Branch, Expense, IncomeEntry, PurchaseOrder, SalesItem,
    StockAdjustment, StockHistory, StockSnapshot, StockUpdate, Products,
)


PURCHASE_SOURCE = "stockhistory"

MONEY = DecimalField(max_digits=18, decimal_places=2)


def _d(v):
    return Decimal(str(v or 0))


def _q(v):
    return _d(v).quantize(Decimal("0.01"))


def _label(text):
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else "Other"


def _reconstruct_opening(shop, branch, start_dt):
    """Estimate the stock value at start_dt = stock now - added since + gone out since.

    Only used when no stock snapshot exists at all. It uses today's cost prices.
    """
    prod = Products.objects.filter(shop=shop)
    if branch:
        prod = prod.filter(branch=branch)
    now_value = _d(prod.aggregate(
        s=Sum(ExpressionWrapper(F("stock") * F("cprice"), output_field=MONEY)))["s"])

    unit_cost = Case(
        When(new_cprice__gt=0, then=F("new_cprice")),
        default=F("product__cprice"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    hq = StockHistory.objects.filter(product__shop=shop, quantity_added__gt=0, added_on__gte=start_dt)
    if branch:
        hq = hq.filter(product__branch=branch)
    added = _d(hq.aggregate(
        s=Sum(ExpressionWrapper(F("quantity_added") * unit_cost, output_field=MONEY)))["s"])

    cost = ExpressionWrapper(F("qty") * F("product__cprice"), output_field=MONEY)
    si = SalesItem.objects.filter(transaction__shop=shop, transaction__date_added__gte=start_dt)
    if branch:
        si = si.filter(transaction__branch=branch)
    out = (_d(si.filter(type__in=["sale", "breakage"]).aggregate(s=Sum(cost))["s"])
           - _d(si.filter(type="return").aggregate(s=Sum(cost))["s"]))

    adj = StockAdjustment.objects.filter(product__shop=shop, date__gte=start_dt)
    if branch:
        adj = adj.filter(product__branch=branch)
    out += _d(adj.aggregate(
        s=Sum(ExpressionWrapper(F("quantity") * F("product__cprice"), output_field=MONEY)))["s"])

    return _q(max(now_value - added + out, Decimal("0")))


def build_income_statement(shop, branch, start, end, today):
    """All figures in USD for shop (or one branch) between start and end, inclusive.

    Cost of sales = opening stock + purchases made AFTER the opening stock was taken - closing stock.
    It is then split into: goods sold (at cost), breakage, shortage, and any other stock difference.
    Every foreign-currency figure uses the USD value saved when it was recorded.
    """
    warnings = set()
    branches = [branch] if branch else (list(Branch.objects.filter(shop=shop)) or [None])

    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.max))

    # ---------- sales ----------
    items = SalesItem.objects.filter(
        transaction__shop=shop, transaction__date_added__date__range=(start, end)
    )
    if branch:
        items = items.filter(transaction__branch=branch)

    cost_expr = ExpressionWrapper(F("qty") * F("product__cprice"), output_field=MONEY)

    # SalesItem.total is saved in USD at the rate of the moment of sale.
    gross_sales = _q(items.filter(type="sale").aggregate(s=Sum("total"))["s"])
    returns_pos = _q(items.filter(type="return").aggregate(s=Sum("total"))["s"])
    sold_cost = _q(items.filter(type="sale").aggregate(s=Sum(cost_expr))["s"])
    returned_cost = _q(items.filter(type="return").aggregate(s=Sum(cost_expr))["s"])
    pos_breakage_cost = _q(items.filter(type="breakage").aggregate(s=Sum(cost_expr))["s"])

    # ---------- breakage and shortage entered in the system (stock adjustments) ----------
    adj = StockAdjustment.objects.filter(product__shop=shop, date__date__range=(start, end))
    if branch:
        adj = adj.filter(product__branch=branch)
    adj_cost_expr = ExpressionWrapper(F("quantity") * F("product__cprice"), output_field=MONEY)

    def adjustment_cost(reason):
        return _q(adj.filter(reason=reason).aggregate(s=Sum(adj_cost_expr))["s"])

    breakage_total = pos_breakage_cost + adjustment_cost("breakage")
    shortage_total = adjustment_cost("shortage")

    # ---------- entries typed in by the user ----------
    entries = IncomeEntry.objects.filter(shop=shop, date__range=(start, end))
    if branch:
        entries = entries.filter(branch=branch)

    def entry_total(entry_type):
        return _q(entries.filter(entry_type=entry_type).aggregate(s=Sum("amount_usd"))["s"])

    manual_returns = entry_total("sales_return")
    manual_purchases = entry_total("purchase")

    total_returns = returns_pos + manual_returns
    net_sales = gross_sales - total_returns

    # ---------- opening stock ----------
    # Order of preference, per branch:
    #   1. a manual opening entry greater than 0 (your override)
    #   2. the last snapshot before the period (the stock that was already there)
    #   3. the first opening snapshot inside the period
    #   4. rebuilt from current stock and movements (estimate)
    # opening_from: per branch, the moment from which purchases still have to be counted.
    opening_from = {}
    manual_open = entries.filter(entry_type="opening_stock").aggregate(s=Sum("amount_usd"))["s"]

    if manual_open is not None and _d(manual_open) > 0:
        opening_stock = _q(manual_open)
        opening_source = "entered by you"
        for b in branches:
            opening_from[b.id if b else None] = start_dt
    else:
        opening_stock = Decimal("0.00")
        used = set()
        for b in branches:
            key = b.id if b else None
            base = StockSnapshot.objects.filter(shop=shop, branch=b)

            snap = base.filter(taken_at__lt=start_dt).order_by("-taken_at").first()
            source = "carried over from the previous closing"
            if not snap:
                snap = (base.filter(kind="opening", taken_at__range=(start_dt, end_dt))
                        .order_by("taken_at").first())
                source = "taken when the shift opened"

            if snap:
                opening_stock += _q(snap.cost_value)
                opening_from[key] = snap.taken_at
                used.add(source)
            else:
                opening_stock += _reconstruct_opening(shop, b, start_dt)
                opening_from[key] = start_dt
                used.add("rebuilt from current stock and movements")
                warnings.add("Opening stock for a branch was rebuilt from current stock because no "
                             "snapshot exists. It uses today's cost prices, so treat it as an estimate.")
        opening_source = "; ".join(sorted(used))

    # ---------- purchases (only those made after the opening stock was taken) ----------
    if PURCHASE_SOURCE == "stockhistory":
        unit_cost = Case(
            When(new_cprice__gt=0, then=F("new_cprice")),
            default=F("product__cprice"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        purchases_system = Decimal("0.00")
        for b in branches:
            since = opening_from.get(b.id if b else None, start_dt)
            hq = StockHistory.objects.filter(
                product__shop=shop, quantity_added__gt=0,
                added_on__gte=since, added_on__lte=end_dt,
            )
            if b:
                hq = hq.filter(product__branch=b)
            purchases_system += _q(hq.aggregate(
                s=Sum(ExpressionWrapper(F("quantity_added") * unit_cost, output_field=MONEY))
            )["s"])
    elif PURCHASE_SOURCE == "purchaseorder":
        pq = PurchaseOrder.objects.filter(shop=shop, date__date__range=(start, end))
        if branch:
            pq = pq.filter(branch=branch)
        purchases_system = _q(pq.aggregate(s=Sum("total_amount"))["s"])
    else:
        pq = StockUpdate.objects.filter(shop=shop, date_added__date__range=(start, end))
        if branch:
            pq = pq.filter(branch=branch)
        purchases_system = _q(pq.aggregate(s=Sum("total_cost"))["s"])
    purchases_total = purchases_system + manual_purchases

    # ---------- closing stock ----------
    money_expr = ExpressionWrapper(F("stock") * F("cprice"), output_field=MONEY)
    live_qs = Products.objects.filter(shop=shop)
    if branch:
        live_qs = live_qs.filter(branch=branch)
    live_stock = _q(live_qs.aggregate(s=Sum(money_expr))["s"])

    if end >= today:
        closing_stock = live_stock
        closing_source = "current stock at cost"
    else:
        closing_stock = Decimal("0.00")
        found = False
        for b in branches:
            snap = (StockSnapshot.objects
                    .filter(shop=shop, branch=b, taken_at__date__lte=end)
                    .order_by("-taken_at").first())
            if snap:
                found = True
                closing_stock += _q(snap.cost_value)
        if found:
            closing_source = "taken when the last shift closed"
        else:
            closing_stock = live_stock
            closing_source = "current stock at cost (no record for that date)"
            warnings.add("No closing stock was recorded for this period, so today's stock value was used.")

    # ---------- cost of sales and its parts ----------
    cogs = opening_stock + purchases_total - closing_stock          # all the stock that left the shop
    goods_sold_cost = sold_cost - returned_cost                     # what the sales themselves cost
    other_difference = cogs - goods_sold_cost - breakage_total - shortage_total

    if other_difference >= Decimal("0.01"):
        warnings.add(
            f"${other_difference:,.2f} of stock left the shop with no sale, breakage or shortage on record "
            "(for example a deleted sale, edited stock or a transfer). It is included in cost of sales."
        )
    elif other_difference <= Decimal("-0.01"):
        warnings.add(
            f"Recorded sales, breakage and shortage add up to ${-other_difference:,.2f} more stock than actually "
            "left the shop. Check the opening stock and purchases."
        )
    if cogs < 0:
        warnings.add("Cost of goods sold is negative. Check the opening stock and purchases.")

    gross_profit = net_sales - cogs
    gross_margin = (gross_profit / net_sales * 100) if net_sales else Decimal("0")

    # ---------- variable costs: shift expenses + entered variable costs ----------
    var_map = defaultdict(lambda: Decimal("0.00"))

    exp = Expense.objects.filter(shop=shop, created_at__date__range=(start, end))
    if branch:
        exp = exp.filter(branch=branch)

    if exp.filter(amount_usd=0, amount__gt=0).exists():
        warnings.add("Some older expenses have no saved exchange rate and are counted as 0. "
                     "Run: python manage.py backfill_rate_history")

    for row in exp.values("reason").annotate(total=Sum("amount_usd")):
        var_map[(row["reason"] or "Shift expenses").strip().lower()] += _q(row["total"])

    for row in entries.filter(entry_type="variable").values("name").annotate(t=Sum("amount_usd")):
        var_map[row["name"].strip().lower()] += _q(row["t"])

    variable_lines = sorted(((_label(k), v) for k, v in var_map.items()), key=lambda x: -x[1])
    variable_total = sum((v for _, v in variable_lines), Decimal("0.00"))

    # ---------- fixed costs: entered by the user ----------
    fixed_map = defaultdict(lambda: Decimal("0.00"))
    for row in entries.filter(entry_type="fixed").values("name").annotate(t=Sum("amount_usd")):
        fixed_map[row["name"].strip().lower()] += _q(row["t"])
    fixed_lines = sorted(((_label(k), v) for k, v in fixed_map.items()), key=lambda x: -x[1])
    fixed_total = sum((v for _, v in fixed_lines), Decimal("0.00"))

    # ---------- results ----------
    net_profit = gross_profit - variable_total - fixed_total

    # Contribution margin: what each sale leaves after the cost of the goods sold and variable expenses
    contribution = net_sales - goods_sold_cost - variable_total
    cm_ratio = (contribution / net_sales) if net_sales else Decimal("0")

    # Break-even: sales needed to cover the fixed costs AND the stock lost to breakage, shortage and
    # unrecorded differences
    stock_losses_total = breakage_total + shortage_total + max(other_difference, Decimal("0.00"))
    to_cover = fixed_total + stock_losses_total

    break_even = None
    break_even_note = ""
    if to_cover <= 0:
        break_even_note = "No fixed costs or stock losses to cover yet. Enter fixed costs in the form below."
    elif cm_ratio <= 0:
        break_even_note = ("Cannot be reached yet: after the cost of goods and variable expenses, "
                           "sales leave no margin to cover fixed costs and stock losses.")
    else:
        break_even = (to_cover / cm_ratio).quantize(Decimal("0.01"))

    period_days = (end - start).days + 1
    break_even_daily = (break_even / period_days).quantize(Decimal("0.01")) if break_even else None
    break_even_weekly = (break_even_daily * 7).quantize(Decimal("0.01")) if break_even_daily else None

    return {
        "gross_sales": gross_sales,
        "returns_pos": returns_pos,
        "manual_returns": manual_returns,
        "total_returns": total_returns,
        "net_sales": net_sales,
        "opening_stock": opening_stock,
        "opening_source": opening_source,
        "purchases_system": purchases_system,
        "manual_purchases": manual_purchases,
        "purchases_total": purchases_total,
        "goods_available": opening_stock + purchases_total,
        "closing_stock": closing_stock,
        "closing_source": closing_source,
        "cogs": cogs,
        "goods_sold_cost": goods_sold_cost,
        "breakage_total": breakage_total,
        "shortage_total": shortage_total,
        "other_difference": other_difference,
        "stock_losses_total": stock_losses_total,
        "breakage_cost": breakage_total,      # kept for older templates
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "variable_lines": variable_lines,
        "variable_total": variable_total,
        "fixed_lines": fixed_lines,
        "fixed_total": fixed_total,
        "expenses_total": fixed_total + variable_total,
        "contribution": contribution,
        "cm_ratio": cm_ratio * 100,
        "net_profit": net_profit,
        "break_even": break_even,
        "break_even_note": break_even_note,
        "warnings": sorted(warnings),
        "break_even_daily": break_even_daily,
        "break_even_weekly": break_even_weekly,
        "period_days": period_days,
    }
