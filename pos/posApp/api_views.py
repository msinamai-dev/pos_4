# posApp/api_views.py
# pip install djangorestframework
from datetime import timedelta

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils.timezone import localtime
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from .models import Products, UserProfile, Shift, Transaction, SalesItem
from .models import Branch, Category, Products, SalesItem, Transaction
from .views import get_user_shop_and_branch  # adjust if it lives elsewhere


from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token

@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
def login_api(request):
    user = authenticate(
        username=request.data.get("username"),
        password=request.data.get("password"),
    )
    if not user:
        return Response({"error": "Invalid username or password"}, status=400)
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key})


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def dashboard_api(request):
    
    if not request.user.has_perm("posApp.view_dashboard"):
        return Response({"error": "You do not have permission to view the dashboard."}, status=403)

    today = localtime().date()
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")

    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return Response({"error": "Your account is missing business details."}, status=400)

    # Branch logic
    branch = user_branch
    if request.user.is_superuser:
        branch_id = request.query_params.get("branch_id")
        if branch_id:
            branch = Branch.objects.filter(id=branch_id, shop=shop).first()

    # Transactions
    tfilter = {"shop": shop}
    if branch:
        tfilter["branch"] = branch
    if start_date and end_date:
        tfilter["date_added__date__range"] = [start_date, end_date]
    transactions = Transaction.objects.filter(**tfilter)

    total_sales = transactions.aggregate(t=Sum("grand_total"))["t"] or 0
    items = SalesItem.objects.filter(transaction__in=transactions)
    total_qty = items.aggregate(t=Sum("qty"))["t"] or 0

    daily_sales = transactions.filter(date_added__date=today).aggregate(t=Sum("grand_total"))["t"] or 0
    yesterday = today - timedelta(days=1)
    yesterday_sales = transactions.filter(date_added__date=yesterday).aggregate(t=Sum("grand_total"))["t"] or 0

    sales_growth = 0
    if yesterday_sales > 0:
        sales_growth = ((daily_sales - yesterday_sales) / yesterday_sales) * 100
    elif daily_sales > 0:
        sales_growth = 100

    # Top product
    top = (
        items.values("product__name")
        .annotate(total_qty=Sum("qty"))
        .order_by("-total_qty")
        .first()
    )

    # Profit & loss
    actual_profit, total_loss = 0, 0
    product_profits = {}
    for item in items.select_related("product"):
        diff = (item.product.price - item.product.cprice) * item.qty
        if diff >= 0:
            actual_profit += diff
        else:
            total_loss += abs(diff)
        product_profits[item.product.name] = product_profits.get(item.product.name, 0) + diff

    profitable = {k: v for k, v in product_profits.items() if v > 0}
    top_profit_product = max(profitable, key=profitable.get) if profitable else None

    # Products / categories
    pfilter = {"shop": shop}
    if branch:
        pfilter["branch"] = branch
    all_products = Products.objects.filter(**pfilter)
    expected_profit = sum((p.price - p.cprice) * p.stock for p in all_products)
    category_count = Category.objects.filter(**pfilter).count()

    # Monthly sales (last 12 months, oldest first)
    monthly = list(
        transactions.annotate(month=TruncMonth("date_added"))
        .values("month")
        .annotate(total=Sum("grand_total"))
        .order_by("-month")[:12]
    )[::-1]

    # Low stock
    low_stock = all_products.filter(stock__lt=20).order_by("stock")

    return Response({
        "shop_name": shop.name,
        "branch_name": branch.name if branch else None,
        "today": str(today),
        "transaction_count": transactions.count(),
        "total_sales": float(total_sales),
        "quantity_sold": int(total_qty),
        "daily_sales": float(daily_sales),
        "yesterday_sales": float(yesterday_sales),
        "sales_growth": float(sales_growth),
        "top_product_name": top["product__name"] if top else "No Sales",
        "top_product_qty": int(top["total_qty"]) if top else 0,
        "top_profit_product": top_profit_product,
        "expected_profit": float(expected_profit),
        "actual_profit": float(actual_profit),
        "total_loss": float(total_loss),
        "product_count": all_products.count(),
        "category_count": category_count,
        "monthly": [
            {"label": m["month"].strftime("%b %Y"), "total": float(m["total"])} for m in monthly
        ],
        "low_stock": [
            {"id": p.id, "name": p.name, "stock": p.stock} for p in low_stock
        ],
    })


from rest_framework.authtoken.models import Token

@api_view(['POST'])

def mobile_login(request):
    from django.contrib.auth import authenticate
    user = authenticate(
        username=request.data.get('username'),
        password=request.data.get('password'),
    )
    if not user:
        return Response({'error': 'Invalid username or password'}, status=400)
    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key})



from django.db.models import Count
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Branch, Category
# from .<wherever> import get_profile


def _err(message, status=400):
    # The mobile app reads the "error" key
    return Response({'error': message}, status=status)

from types import SimpleNamespace
from .views import get_user_shop_and_branch  # adjust if it lives elsewhere

def get_profile(request):
    shop, branch = get_user_shop_and_branch(request)
    return SimpleNamespace(shop=shop, branch=branch) if shop else None

@api_view(['GET', 'POST'])

def categories(request):
    profile = get_profile(request)
    if not profile or not profile.shop:
        return _err('No shop associated with this user.')

    is_super = request.user.is_superuser
    shop_branches = Branch.objects.filter(shop=profile.shop)

    # ---------------- VIEW ----------------
    if request.method == 'GET':
        branch_id = request.GET.get('branch_id')

        if is_super:
            # Superusers can look at all branches or any single branch of their shop
            if branch_id in (None, '', 'all'):
                selected = None
            elif branch_id.isdigit() and shop_branches.filter(id=branch_id).exists():
                selected = int(branch_id)
            else:
                return _err('Branch not found.')
        else:
            # Staff are locked to their own branch, whatever branch_id they send
            selected = profile.branch.id if profile.branch else None

        qs = Category.objects.filter(shop=profile.shop)
        if selected:
            qs = qs.filter(branch_id=selected)

        cats = list(
            qs.annotate(product_count=Count('products'))
            .values('id', 'name', 'description', 'branch_id', 'branch__name', 'product_count')
            .order_by('name')
        )
        for c in cats:
            c['branch_name'] = c.pop('branch__name')
            c['description'] = c['description'] or ''

        branch_name = (
            shop_branches.filter(id=selected).values_list('name', flat=True).first()
            if selected else None
        )

        return Response({
            'categories': cats,
            'branches': list(shop_branches.values('id', 'name')) if is_super else [],
            'can_choose_branch': is_super,
            'can_add': True,
            'can_delete': is_super,
            'selected_branch_id': selected,
            'branch_name': branch_name,
        })

    # ---------------- ADD ----------------
    name = (request.data.get('name') or '').strip()
    description = (request.data.get('description') or '').strip()
    if not name:
        return _err('Category name is required.')

    if is_super:
        raw = request.data.get('branch_id')
        if raw:
            branch = shop_branches.filter(id=raw).first() if str(raw).isdigit() else None
            if not branch:
                return _err('Branch not found.')
        else:
            branch = profile.branch
        if branch is None and shop_branches.exists():
            return _err('Select a branch for this category.')
    else:
        branch = profile.branch  # staff can only add to their own branch

    if Category.objects.filter(shop=profile.shop, branch=branch, name__iexact=name).exists():
        return _err('A category with this name already exists in this branch.')

    cat = Category.objects.create(
        shop=profile.shop, branch=branch, name=name, description=description
    )
    return Response({'id': cat.id, 'name': cat.name}, status=201)


@api_view(['DELETE'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def category_detail(request, pk):
    profile = get_profile(request)
    if not profile or not profile.shop:
        return _err('No shop associated with this user.')

    # Matches can_delete in the GET response
    if not request.user.is_superuser:
        return _err("You don't have permission to delete categories.", 403)

    cat = (
        Category.objects.filter(pk=pk, shop=profile.shop)
        .annotate(n=Count('products'))
        .first()
    )
    if not cat:
        return _err('Category not found.', 404)
    if cat.n:
        return _err(f'This category has {cat.n} product(s). Move or delete them first.')

    cat.delete()
    return Response({'deleted': True})