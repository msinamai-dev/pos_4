
from posApp.models import Category, Products, Sales, SalesItem,Shop

def transfer_product(product_id, to_shop_id):
    product = Products.objects.get(id=product_id)
    to_shop = Shop.objects.get(id=to_shop_id)

    Products.objects.create(
        shop=to_shop,
        name=product.name,
        category=product.category,
        price=product.price,
        cprice=product.cprice,
        code=f"{product.code}_copy",  # ensure uniqueness
        stock=product.stock,
        description=product.description,
        inclusive_tax=product.inclusive_tax,
        exclusive_tax=product.exclusive_tax,
        status=product.status
    )