COUNTRY_CODES = {
    "Angola": "ao", "Botswana": "bw", "Comoros": "km",
    "Democratic Republic of the Congo": "cd", "Eswatini": "sz",
    "Lesotho": "ls", "Madagascar": "mg", "Malawi": "mw",
    "Mauritius": "mu", "Mozambique": "mz", "Namibia": "na",
    "Seychelles": "sc", "South Africa": "za", "Tanzania": "tz",
    "Zambia": "zm", "Zimbabwe": "zw",
    "Nigeria": "ng", "Kenya": "ke", "Ghana": "gh", "Egypt": "eg",
    "Morocco": "ma", "Ethiopia": "et", "Uganda": "ug", "Rwanda": "rw",
    "Senegal": "sn", "Tunisia": "tn", "Algeria": "dz",
    "Ivory Coast": "ci", "Cameroon": "cm",
    "United States": "us", "Canada": "ca", "Mexico": "mx",
    "Brazil": "br", "Argentina": "ar", "Chile": "cl", "Colombia": "co",
    "Peru": "pe", "Venezuela": "ve", "Ecuador": "ec", "Bolivia": "bo",
    "Uruguay": "uy", "Paraguay": "py", "Cuba": "cu", "Jamaica": "jm",
    "Panama": "pa", "Costa Rica": "cr",
}

def user_country_flag(request):
    country_code = ""
    if request.user.is_authenticated:
        profile = getattr(request.user, "userprofile", None)
        if profile and profile.country:
            country_code = COUNTRY_CODES.get(profile.country, "")
    return {"country_code": country_code}



PO_DUE_DAYS = 30
PO_ALERT_DAYS = 7


def get_po_due_alerts(user):
    """Return a list of dicts for unpaid credit purchase orders due within PO_ALERT_DAYS."""
    from datetime import timedelta
    from django.utils import timezone
    from posApp.models import CreditPayment, UserProfile

    profile = UserProfile.objects.filter(user=user).select_related("shop", "branch").first()
    if not profile or not profile.shop_id:
        return []

    today = timezone.localdate()
    # purchase orders created between (today - 30) and (today - 23) days ago are due in 0..7 days
    oldest = today - timedelta(days=PO_DUE_DAYS)
    newest = today - timedelta(days=PO_DUE_DAYS - PO_ALERT_DAYS)

    credits = (
        CreditPayment.objects
        .filter(purchase_order__shop_id=profile.shop_id, status__in=["pending", "partial"])
        .filter(purchase_order__date__date__gte=oldest, purchase_order__date__date__lte=newest)
        .select_related("purchase_order", "purchase_order__supplier", "purchase_order__product")
    )
    if not user.is_superuser and profile.branch_id:
        credits = credits.filter(branch_id=profile.branch_id)

    alerts = []
    for credit in credits:
        order = credit.purchase_order
        remaining = credit.total_amount - credit.paid_amount
        if remaining <= 0:
            continue
        due_date = timezone.localtime(order.date).date() + timedelta(days=PO_DUE_DAYS)
        days_left = (due_date - today).days
        if days_left < 0 or days_left > PO_ALERT_DAYS:
            continue
        alerts.append({
            "order_id": order.id,
            "supplier": order.supplier.name if order.supplier else "No supplier",
            "product": order.product.name if order.product else "",
            "remaining": remaining,
            "due_date": due_date,
            "days_left": days_left,
        })

    alerts.sort(key=lambda a: a["days_left"])
    return alerts


def po_due_alerts(request):
    """Template context: po_alerts (list), po_alert_count, po_alert_show_banner."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    try:
        alerts = get_po_due_alerts(user)
    except Exception:
        # an alert problem must never break a page
        return {}

    from django.utils import timezone
    dismissed_on = request.session.get("po_alert_dismissed_on")
    show_banner = bool(alerts) and dismissed_on != timezone.localdate().isoformat()
    return {
        "po_alerts": alerts,
        "po_alert_count": len(alerts),
        "po_alert_show_banner": show_banner,
    }



from .decorators import get_request_plan, get_user_permissions

def user_permissions(request):
    return {
        "user_perms": get_user_permissions(request),
        "plan_name": get_request_plan(request)  
    }
    



