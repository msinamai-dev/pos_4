# posApp/category_api.py
from django.core.exceptions import FieldError
from django.db.models import Count
from django.db.models.deletion import ProtectedError
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Branch, Category, Products
from .views import get_user_shop_and_branch  # adjust if it lives elsewhere

# ADJUST to match how your web `permission_required` decorator works.
# Superusers always pass.
PERMS = {
    "view": "posApp.view_categories",
    "add": "posApp.add_category",
    "delete": "posApp.delete_category",
}


def _allowed(user, key):
    return user.is_superuser or user.has_perm(PERMS[key])


def _err(message, status=400):
    return Response({"error": message}, status=status)


def _counts(shop, qs):
    """Products per category (skipped quietly if the FK isn't called `category`)."""
    try:
        rows = (
            Products.objects.filter(shop=shop, category__in=qs)
            .values("category")
            .annotate(n=Count("id"))
        )
        return {r["category"]: r["n"] for r in rows}
    except FieldError:
        return {}


def _serialize(c, counts):
    return {
        "id": c.id,
        "name": c.name,
        "description": getattr(c, "description", "") or "",
        "branch_id": c.branch_id,
        "branch_name": c.branch.name if c.branch_id else None,
        "product_count": counts.get(c.id, 0),
    }


@api_view(["GET", "POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def categories_api(request):
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return _err("No shop associated with this user.")

    is_admin = request.user.is_superuser
    # Only superusers can see / pick other branches
    branches = Branch.objects.filter(shop=shop) if is_admin else Branch.objects.none()

    # ---------------- ADD ----------------
    if request.method == "POST":
        if not _allowed(request.user, "add"):
            return _err("You don't have permission to add categories.", 403)

        name = (request.data.get("name") or "").strip()
        if not name:
            return _err("Category name is required.")

        if is_admin:
            branch_id = request.data.get("branch_id")
            if branch_id:
                branch = branches.filter(id=branch_id).first() if str(branch_id).isdigit() else None
                if not branch:
                    return _err("Branch not found.")
            else:
                branch = user_branch
            if branch is None and branches.exists():
                return _err("Select a branch for this category.")
        else:
            branch = user_branch  # staff are locked to their own branch

        if Category.objects.filter(shop=shop, branch=branch, name__iexact=name).exists():
            return _err("A category with this name already exists in this branch.")

        data = {"shop": shop, "branch": branch, "name": name}
        if "description" in {f.name for f in Category._meta.get_fields()}:
            data["description"] = (request.data.get("description") or "").strip()

        cat = Category.objects.create(**data)
        return Response(_serialize(cat, {}), status=201)

    # ---------------- VIEW ----------------
    if not _allowed(request.user, "view"):
        return _err("You don't have permission to view categories.", 403)

    branch = user_branch  # same default as the web view
    if is_admin:
        branch_id = request.query_params.get("branch_id")
        if branch_id == "all":
            branch = None
        elif branch_id:
            branch = branches.filter(id=branch_id).first() if branch_id.isdigit() else None
            if branch is None:
                return _err("Branch not found.")

    qs = Category.objects.filter(shop=shop).select_related("branch").order_by("name")
    if branch:
        qs = qs.filter(branch=branch)

    counts = _counts(shop, qs)
    return Response({
        "categories": [_serialize(c, counts) for c in qs],
        "branches": [{"id": b.id, "name": b.name} for b in branches],
        "can_choose_branch": is_admin,
        "selected_branch_id": branch.id if branch else None,
        "branch_name": branch.name if branch else None,
        "can_add": _allowed(request.user, "add"),
        "can_delete": _allowed(request.user, "delete"),
    })


@api_view(["DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def category_detail_api(request, pk):
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return _err("No shop associated with this user.")
    if not _allowed(request.user, "delete"):
        return _err("You don't have permission to delete categories.", 403)

    qs = Category.objects.filter(id=pk, shop=shop)
    if not request.user.is_superuser and user_branch:
        qs = qs.filter(branch=user_branch)  # staff can only touch their own branch
    category = qs.first()
    if not category:
        return _err("Category not found.", 404)

    n = _counts(shop, Category.objects.filter(id=category.id)).get(category.id, 0)
    if n:
        return _err(f"This category has {n} product(s). Move or delete them first.")

    try:
        category.delete()
    except ProtectedError:
        return _err("This category is in use and can't be deleted.")
    return Response({"deleted": True})
