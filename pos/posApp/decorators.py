from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect,render


from .permissions_registry import normalise, register_permission


class _AllPermissions:

    def __contains__(self, item):
        return True
    def __iter__(self):
        return iter(())

ALL = _AllPermissions()


def permissions_for_user(user):
    if not user.is_authenticated:
        return frozenset()
    if user.is_superuser:
        return ALL
    profile = getattr(user, "userprofile", None)
    if not profile:
        return frozenset()
    if profile.role_id and profile.role.name == "Administrator":
        return ALL
    perms = normalise(profile.permissions)
    if profile.role_id:
        perms |= normalise(profile.role.permissions)
    return frozenset(perms)


def get_user_permissions(request):
    """Same as above, but worked out once per request."""
    if not hasattr(request, "_perm_cache"):
        request._perm_cache = permissions_for_user(request.user)
    return request._perm_cache


def can_grant(user, codes):
    """A user may only hand out permissions they hold themselves."""
    perms = permissions_for_user(user)
    return perms is ALL or set(codes) <= perms


def _wants_json(request):
    return (request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("accept", ""))


def permission_required(*codes, any_of=False):
    wanted = normalise(codes)
    for code in wanted:
        register_permission(code)

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if _wants_json(request):
                    return JsonResponse({"status": "failed", "msg": "Please log in again."}, status=401)
                return redirect_to_login(request.get_full_path())

            perms = get_user_permissions(request)
            ok = (perms is ALL or
                  (any(c in perms for c in wanted) if any_of else all(c in perms for c in wanted)))
            if ok:
                return view_func(request, *args, **kwargs)

            return render (request,'posApp/403.html',status =403)
        return _wrapped
    return decorator


def get_active_subscription(user):
    """The user's own active subscription, else their shop owner's."""
    from .models import Subscription
    own = Subscription.objects.filter(user=user).last()
    if own and own.has_access():
        return own

    profile = getattr(user, "userprofile", None)
    shop = getattr(profile, "shop", None)
    owner_profile = getattr(shop, "owner", None)
    owner_user = getattr(owner_profile, "user", None)
    if owner_user and owner_user.pk != user.pk:
        owner_sub = Subscription.objects.filter(user=owner_user).last()
        if owner_sub and owner_sub.has_access():
            return owner_sub
    return None


def subscription_required(view_func):

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not get_active_subscription(request.user):
            if _wants_json(request):
                return JsonResponse({"status": "failed", "msg": "Your subscription has expired."}, status=402)
            messages.error(request, "❌ Your subscription has expired. Please renew to continue using this page.")
            return redirect("my-subscription")
        return view_func(request, *args, **kwargs)
    return wrapper

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect

from .permissions_registry import normalise, register_permission


# ---------------------------------------------------------------------------
# PLAN RULES: permissions each plan can NOT use (edit this to change a plan)
# ---------------------------------------------------------------------------
PLAN_BLOCKED = {
    "BASIC": {
        # reports
        "view_reports", "view_sales_report", "view_shift_report",
        "view_product_report", "view_stock_tracker", "view_stock_history",
        "stock_count_report",
        # transfers
        "transfer_product",
        # income statement
        "view_income_statement",
    },
    "PREMIUM": set(),
    "ANNUAL": set(),
}
DEFAULT_PLAN = "BASIC"      # used when the user has no active subscription


class _AllPermissions:
    """Superusers/Administrators: everything, except what their plan blocks."""
    def __init__(self, blocked=frozenset()):
        self.blocked = frozenset(blocked)

    def __contains__(self, item):
        return item not in self.blocked

    def __iter__(self):
        return iter(())


def get_plan_name(user):
    sub = get_active_subscription(user)
    plan = getattr(sub, "plan", None)
    name = (getattr(plan, "name", "") or "").upper().strip()
    return name if name in PLAN_BLOCKED else DEFAULT_PLAN


def plan_blocked_codes(user):
    if not user.is_authenticated:
        return frozenset()
    return frozenset(PLAN_BLOCKED.get(get_plan_name(user), set()))


def permissions_for_user(user):
    """Role + extra permissions, minus anything the user's plan blocks.
    Superuser and Administrator role = everything the plan allows."""
    if not user.is_authenticated:
        return frozenset()
    blocked = plan_blocked_codes(user)
    if user.is_superuser:
        return _AllPermissions(blocked)
    profile = getattr(user, "userprofile", None)
    if not profile:
        return frozenset()
    if profile.role_id and profile.role.name == "Administrator":
        return _AllPermissions(blocked)
    perms = normalise(profile.permissions)
    if profile.role_id:
        perms |= normalise(profile.role.permissions)
    return frozenset(perms - blocked)


def get_user_permissions(request):
    """Worked out once per request."""
    if not hasattr(request, "_perm_cache"):
        request._perm_cache = permissions_for_user(request.user)
    return request._perm_cache


def get_request_plan(request):
    if not hasattr(request, "_plan_cache"):
        request._plan_cache = get_plan_name(request.user) if request.user.is_authenticated else ""
    return request._plan_cache


def can_grant(user, codes):
    """A user may only hand out permissions they hold themselves."""
    perms = permissions_for_user(user)
    return isinstance(perms, _AllPermissions) or set(codes) <= perms


def _wants_json(request):
    return (request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("accept", ""))


def permission_required(*codes, any_of=False):
    """
    @permission_required("manage_stocks")
    @permission_required("view_reports", "view_sales_report", any_of=True)

    Codes are registered automatically for the permission picker.
    If the plan blocks the page, the user is sent to the plans page to upgrade.
    """
    wanted = normalise(codes)
    for code in wanted:
        register_permission(code)

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if _wants_json(request):
                    return JsonResponse({"status": "failed", "msg": "Please log in again."}, status=401)
                return redirect_to_login(request.get_full_path())

            perms = get_user_permissions(request)
            if isinstance(perms, _AllPermissions):
                has = (any(c in perms for c in wanted) if any_of
                       else all(c in perms for c in wanted))
            else:
                has = (any(c in perms for c in wanted) if any_of
                       else all(c in perms for c in wanted))
            if has:
                return view_func(request, *args, **kwargs)

            # Was it the plan that said no?
            blocked = plan_blocked_codes(request.user)
            plan_issue = bool(blocked) and ((wanted <= blocked) if any_of else bool(wanted & blocked))
            if plan_issue:
                plan = get_request_plan(request).title()
                msg = f"Your {plan} plan does not include this feature. Upgrade your plan to use it."
                if _wants_json(request):
                    return JsonResponse({"status": "failed", "msg": msg}, status=403)
                messages.warning(request, msg)
                return redirect("subscription-plans")

            if _wants_json(request):
                return JsonResponse(
                    {"status": "failed", "msg": "You don't have permission to do this."}, status=403)
            raise PermissionDenied
        return _wrapped
    return decorator


def get_active_subscription(user):
    """The user's own active subscription, else their shop owner's."""
    from .models import Subscription
    own = Subscription.objects.filter(user=user).last()
    if own and own.has_access():
        return own

    profile = getattr(user, "userprofile", None)
    shop = getattr(profile, "shop", None)
    owner_profile = getattr(shop, "owner", None)
    owner_user = getattr(owner_profile, "user", None)
    if owner_user and owner_user.pk != user.pk:
        owner_sub = Subscription.objects.filter(user=owner_user).last()
        if owner_sub and owner_sub.has_access():
            return owner_sub
    return None


def subscription_required(view_func):
    """Restrict access to users whose own or shop-owner subscription is active."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not get_active_subscription(request.user):
            if _wants_json(request):
                return JsonResponse({"status": "failed", "msg": "Your subscription has expired."}, status=402)
            messages.error(request, "❌ Your subscription has expired. Please renew to continue using this page.")
            return redirect("my-subscription")
        return view_func(request, *args, **kwargs)
    return wrapper

from django.conf import settings

def platform_admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.username in getattr(settings, "PLATFORM_ADMINS", []):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied
    return wrapper