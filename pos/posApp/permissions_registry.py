import re

PERMISSION_GROUPS = [
    ("Dashboard", [
        ("view_dashboard", "View dashboard"),
    ]),
    ("Products & Stock", [
        ("view_products", "View products"),
        ("manage_products", "Add / save products"),
        ("edit_product", "Edit products / import"),
        ("view_categories", "View & manage categories"),
        ("manage_stocks", "Update stock"),
        ("stock_count", "Do stock counts"),
        ("stock_adjustment", "Stock adjustments (breakage, loss)"),
        ("transfer_product", "Transfer products between branches"),
        ("delete", "Delete records"),
    ]),
    ("Purchasing", [
        ("view_suppliers", "View suppliers"),
        ("manage_purchase_orders", "Purchase orders"),
        ("view_credit_purchases", "Purchases on credit"),
    ]),
    ("Reports", [
        ("view_reports", "Open the Reports menu"),
        ("view_sales_report", "Product sold report"),
        ("view_shift_report", "Shift report"),
        ("view_product_report", "Product report"),
        ("view_stock_tracker", "Stock tracker"),
        ("view_stock_history", "Stock history"),
        ("stock_count_report", "Stock count reports & discrepancies"),
        ("saleslist", "Sales list"),
        ("view_income_statement", "Income statement"),
        ("view_strategy", "Strategy matrices (BCG & Ansoff)"),
    ]),
    ("Users & Settings", [
        ("manage_users", "Open the Settings menu / manage roles"),
        ("create_user", "Create users"),
        ("user_list", "View user list"),
        ("user_edit", "Edit users"),
        ("user_delete", "Delete users"),
        ("view_branch", "Branches"),
        ("view_shop", "View shops"),
        ("add_shop", "Add shops"),
        ("view_users", "View users"),
    ]),
    ("Subscriptions", [
        ("my-subscription", "My subscription"),
        ("subscription-plans", "Subscription plans & settings pages"),
        ("manage_subscriptions", "Manage all subscriptions"),
    ]),
    # also used by the navigation menu but missing before
    ("Sales", [
        ("make_sales", "Use the POS / make sales"),
    ]),
]

AUTO_GROUP_NAME = "Other (auto-detected)"

# old code -> canonical code (so permissions already saved on users keep working)
LEGACY_ALIASES = {"user-delete": "user_delete", "user-list": "user_list"}


def normalise(codes):
    return {LEGACY_ALIASES.get(c, c) for c in (codes or [])}


_DISCOVERED = set()         
_NAV_CACHE = {}              


_NAV_PATTERN = re.compile(r'''["']([\w\-]+)["']\s+in\s+user_perms''')


def register_permission(code):
    """Called by @permission_required for every code it protects a view with."""
    if code:
        _DISCOVERED.add(LEGACY_ALIASES.get(code, code))


def _navigation_codes():
    """Codes used in the navigation template(s). Re-read only when the file changes."""
    import os
    from django.conf import settings
    from django.template.loader import get_template

    names = getattr(settings, "NAVIGATION_TEMPLATES", ["posApp/navigation.html"])
    found = set()
    for name in names:
        try:
            path = get_template(name).origin.name
            mtime = os.path.getmtime(path)
            cached = _NAV_CACHE.get(path)
            if not cached or cached[0] != mtime:
                with open(path, encoding="utf-8") as fh:
                    cached = (mtime, set(_NAV_PATTERN.findall(fh.read())))
                _NAV_CACHE[path] = cached
            found |= cached[1]
        except Exception:
            continue          
    return {LEGACY_ALIASES.get(c, c) for c in found}


def _label(code):
    return code.replace("_", " ").replace("-", " ").strip().capitalize()


def _known_codes():
    return {c for _, items in PERMISSION_GROUPS for c, _l in items}


def auto_codes():
    """Codes found in views/navigation that are not listed in PERMISSION_GROUPS."""
    return sorted((_DISCOVERED | _navigation_codes()) - _known_codes())


def get_permission_groups():
    """PERMISSION_GROUPS + an 'Other (auto-detected)' group for new codes."""
    groups = [(g, list(items)) for g, items in PERMISSION_GROUPS]
    extra = auto_codes()
    if extra:
        groups.append((AUTO_GROUP_NAME, [(c, _label(c)) for c in extra]))
    return groups


def flat_permissions():
    """[(code, label), ...] for form choices (always up to date)."""
    return [(c, l) for _, items in get_permission_groups() for c, l in items]


def all_codes():
    return [c for c, _ in flat_permissions()]


def __getattr__(name):
  
    if name == "PERMISSIONS":
        return flat_permissions()
    if name == "ALL_CODES":
        return all_codes()
    raise AttributeError(name)


def _all_except(*groups):
    skip = set(groups)
    return [c for g, items in PERMISSION_GROUPS if g not in skip for c, _ in items]

DEFAULT_ROLES = {
    "Administrator": ("Full access", None),
    "Manager": ("Everything except users, settings and subscriptions",
                _all_except("Users & Settings", "Subscriptions")),
    "Cashier": ("Sales only", ["make_sales", "saleslist", "view_products"]),
    "Stock Keeper": ("Stock work only", [
        "view_products", "view_categories", "manage_stocks", "stock_count",
        "stock_adjustment", "transfer_product", "view_suppliers",
        "view_stock_tracker", "view_stock_history"]),
    "Accountant": ("Reports and purchasing", [
        "view_dashboard", "view_reports", "view_sales_report", "view_shift_report",
        "view_product_report", "view_stock_history", "stock_count_report",
        "view_credit_purchases", "saleslist"]),
}


def create_default_roles(shop):
    """Create the starter roles for a shop and keep Administrator complete.
    Returns {role_name: Role}."""
    from .models import Role
    roles = {}
    everything = all_codes()
    for name, (desc, codes) in DEFAULT_ROLES.items():
        codes = list(everything if codes is None else codes)
        role, created = Role.objects.get_or_create(
            shop=shop, name=name,
            defaults={"description": desc, "permissions": codes})
        if not created and name == "Administrator" and set(role.permissions or []) != set(everything):
            role.permissions = list(everything)
            role.save(update_fields=["permissions"])
        roles[name] = role
    return roles