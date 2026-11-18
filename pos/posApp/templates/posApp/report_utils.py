from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from .models import Sales, Product # type: ignore

def get_product_report_data(request):
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    search = request.GET.get("search", "")

    qs = Sales.objects.all()

    if start_date and end_date:
        qs = qs.filter(day_sold__range=[start_date, end_date])

    if search:
        qs = qs.filter(product__name__icontains=search) | qs.filter(product__category__name__icontains=search)

    # Sales by category
    sales_by_category = (
        qs.values("product__category__name", "day_sold")
        .annotate(
            total_sales=Sum(F("unit_price") * F("quantity")),
            total_cost=Sum(F("unit_cost") * F("quantity")),
        )
        .annotate(
            profit=F("total_sales") - F("total_cost"),
            profit_margin=ExpressionWrapper(
                (F("profit") / F("total_sales")) * 100,
                output_field=DecimalField()
            ),
            loss_margin=ExpressionWrapper(
                (F("total_cost") - F("total_sales")) * 100 / F("total_cost"),
                output_field=DecimalField()
            ),
        )
    )

    # Sales by product
    sales_by_product = (
        qs.values("product__category__name", "product__name", "day_sold", "unit_price", "unit_cost")
        .annotate(
            total_qty=Sum("quantity"),
            total_sales=Sum(F("unit_price") * F("quantity")),
            total_cost=Sum(F("unit_cost") * F("quantity")),
        )
        .annotate(
            profit=F("total_sales") - F("total_cost"),
            profit_margin=ExpressionWrapper(
                (F("profit") / F("total_sales")) * 100,
                output_field=DecimalField()
            ),
            loss_margin=ExpressionWrapper(
                (F("total_cost") - F("total_sales")) * 100 / F("total_cost"),
                output_field=DecimalField()
            ),
        )
    )

    context = {
        "sales_by_category": sales_by_category,
        "sales_by_product": sales_by_product,
        "start_date": start_date,
        "end_date": end_date,
        "search": search,
    }

    return context
