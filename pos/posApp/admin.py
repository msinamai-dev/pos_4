from django.contrib import admin
from posApp.models import Category, Products, SalesItem,Shop

# Register your models here.
admin.site.register(Category)
admin.site.register(Products)

admin.site.register(SalesItem)
# admin.site.register(Employees)

@admin.action(description="Transfer selected products to another shop")

def transfer_to_shop(modeladmin, request, queryset):
    to_shop_id = request.POST.get("to_shop_id")  # you'd need a form for this
    to_shop = Shop.objects.get(id=to_shop_id)

    for product in queryset:
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

from django.contrib import admin
from .models import UserProfile, Shop, Branch

admin.site.register(UserProfile)
admin.site.register(Shop)
admin.site.register(Branch)

from .models import ERPShopLinkRequest, ERPShopLinkAudit

@admin.register(ERPShopLinkRequest)
class ERPShopLinkRequestAdmin(admin.ModelAdmin):
    list_display = ("company_name", "shop", "status", "requested_by", "owner_user", "created_at", "expires_at")
    list_filter = ("status", "created_at")
    search_fields = ("company_name", "shop__name", "requested_by", "owner_user__username")
    readonly_fields = ("request_id", "company_id", "company_name", "shop", "branch_id", "requested_by", "status", "token_nonce", "expires_at", "owner_user", "created_at", "decided_at")

@admin.register(ERPShopLinkAudit)
class ERPShopLinkAuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event", "company_name", "shop_id", "actor_label")
    list_filter = ("event", "created_at")
    search_fields = ("company_name", "actor_label")
    readonly_fields = tuple(field.name for field in ERPShopLinkAudit._meta.fields)
