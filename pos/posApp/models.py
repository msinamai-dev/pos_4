from datetime import datetime
from decimal import Decimal
from unicodedata import category
from django.db import models
from django.shortcuts import render
from django.utils import timezone
from django.utils.timezone import now, timedelta
from django.contrib.auth.models import AbstractUser
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

#python manage.py makemigrations python manage.py migrate
#python manage.py runserver

from django.db import models
from django.contrib.auth.models import User



class UserProfile(models.Model):
    BUSINESS_TYPES = [
        ("restaurant", "Restaurant"),
        ("coffee_shop", "Coffee Shop"),
        ("bar", "Bar"),
        ("food_truck", "Food Truck"),
        ("salon", "Salon & Personal Care"),
        ("grocery", "Grocery"),
        ("clothing", "Clothing & Textile"),
        ("electronics", "Electronics"),
        ("vehicle_service", "Vehicle Service Center"),
        ("pharmacy", "Pharmacy"),
        ("transportation", "Transportation"),
        ("jewelry", "Jewelry & Accessories"),
        ("leisure", "Leisure & Entertainment"),
        ("sports", "Sports Products"),
        ("other", "Other"),
    ]

    THEME_CHOICES = [
        ("light", "Light"),
        ("dark", "Dark"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True)
    access_rights = models.JSONField(default=dict, blank=True)   
    permissions = models.JSONField(default=list, blank=True)     
    is_frozen = models.BooleanField(default=False)
    business_type = models.CharField(max_length=50, choices=BUSINESS_TYPES, null=True, blank=True)
    business_name = models.CharField(max_length=255, null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    must_change_password = models.BooleanField(default=True)
    shop = models.ForeignKey('Shop', on_delete=models.SET_NULL, null=True, blank=True)
    role = models.ForeignKey("Role", null=True, blank=True,on_delete=models.SET_NULL, related_name="members")
    branch = models.ForeignKey('Branch', on_delete=models.SET_NULL, null=True, blank=True)
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="light")

    def has_permission(self, perm):
        if perm in self.permissions:
            return True
        return bool(self.access_rights.get(perm, False))

    def __str__(self):
        shop_name = self.shop.name if self.shop else "No Shop"
        return f"{self.user.username} - {shop_name}"
    

class Shop(models.Model):
    owner = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='shops')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    default_currency = models.CharField(max_length=3, default='USD')
    location = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        try:
            owner_name = getattr(self.owner, "business_name", None) or getattr(self.owner, "name", None)
        except UserProfile.DoesNotExist:
            owner_name = "No Owner"
        return f"{self.name} ({owner_name if owner_name else 'No Owner'})"

class Branch(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.shop.name})"    


class ERPShopLinkRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending owner approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    request_id = models.UUIDField(unique=True)
    company_id = models.PositiveBigIntegerField()
    company_name = models.CharField(max_length=255)
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="erp_link_requests")
    branch_id = models.PositiveBigIntegerField(null=True, blank=True)
    requested_by = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    token_nonce = models.CharField(max_length=100, unique=True)
    expires_at = models.DateTimeField()
    owner_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="erp_shop_link_decisions")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company_name} → {self.shop.name} ({self.status})"


class ERPShopLinkAudit(models.Model):
    link_request = models.ForeignKey(ERPShopLinkRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    request_id = models.UUIDField(db_index=True)
    company_id = models.PositiveBigIntegerField()
    company_name = models.CharField(max_length=255)
    shop_id = models.PositiveBigIntegerField()
    event = models.CharField(max_length=50)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    actor_label = models.CharField(max_length=150, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Products(models.Model):
    UNIT_CHOICES = [
        ('each', 'Each'),
        ('kg', 'Kilogram'),
        ('g', 'Gram'),
        ('ml', 'Millilitre'),
        ('l', 'Litre'),
        ('m', 'Metre'),
        ('cm', 'Centimetre'),
        ('pack', 'Pack'),
        ('box', 'Box'),
    ]

    TAX_TYPE_CHOICES = [
        ('inclusive', 'Inclusive'),
        ('exclusive', 'Exclusive'),
        ('none', 'No Tax'),
    ]

    shop = models.ForeignKey('Shop', on_delete=models.CASCADE)
    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=200)
    category = models.ForeignKey("Category", on_delete=models.SET_NULL, null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cprice = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    code = models.CharField(max_length=100, null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True, null=True)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True)
    tax_type = models.CharField(max_length=20, choices=TAX_TYPE_CHOICES, default='none')
    inclusive_tax = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    exclusive_tax = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, null=True, blank=True)
    status = models.BooleanField(default=True)
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ( 'branch', 'name')

    def __str__(self):
        return f"{self.name} ({self.code})"

    def effective_tax(self):
        if self.tax_type == 'inclusive':
            return self.inclusive_tax
        elif self.tax_type == 'exclusive':
            return self.exclusive_tax
        return 0

    @property
    def profit_or_loss(self):
        return self.price - self.cprice

    def generate_code(self):
        
        last_product = Products.objects.order_by("-id").first()
        if last_product and last_product.code and last_product.code.startswith("PRD-"):
            try:
                last_num = int(last_product.code.split("-")[1])
                return f"PRD-{last_num+1:03d}"
            except:
                return f"PRD-{self.id:03d}"
        return "PRD-001"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_code()
        super().save(*args, **kwargs)

    def transfer_to_branch(self, destination_branch, quantity):
        """Transfer quantity to another branch (same shop)."""
        if quantity <= 0:
            raise ValidationError("Quantity must be a positive integer.")

        with transaction.atomic():
            src = Products.objects.select_for_update().get(pk=self.pk)

            if src.stock < quantity:
                raise ValidationError(f"Insufficient stock in source branch. Available: {src.stock}")

            # Try to find destination product
            try:
                dest = Products.objects.select_for_update().get(
                    name=src.name,
                    branch=destination_branch,
                    shop=src.shop
                )
                dest.stock += quantity
                dest.save()

            except Products.DoesNotExist:
                # Destination product doesn’t exist — create a new one with new unique code
                try:
                    dest = Products.objects.create(
                        shop=src.shop,
                        branch=destination_branch,
                        name=src.name,
                        category=src.category,
                        price=src.price,
                        cprice=src.cprice,
                        code=src.generate_code(),  # ✅ Generate a new unique code
                        stock=quantity,
                        description=src.description,
                        inclusive_tax=src.inclusive_tax,
                        exclusive_tax=src.exclusive_tax,
                        status=src.status,
                    )
                except IntegrityError:
                    # If another thread created it meanwhile, update it
                    dest = Products.objects.select_for_update().get(
                        name=src.name,
                        branch=destination_branch,
                        shop=src.shop
                    )
                    dest.stock += quantity
                    dest.save()

            # Decrement source stock
            src.stock -= quantity
            if src.stock < 0:
                raise ValidationError("Transfer would make source stock negative.")
            src.save()

            return dest

from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError

class Category(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    name = models.TextField()
    description = models.TextField()
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    status = models.IntegerField(default=1)
   
    date_added = models.DateTimeField(default=timezone.now) 
    date_updated = models.DateTimeField(auto_now=True) 
    date_added = models.DateTimeField(auto_now_add=True)
    

    def __str__(self):
        return self.name

class ProductTransfer(models.Model):
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    from_branch = models.ForeignKey('Branch', related_name='transfers_out', on_delete=models.CASCADE)
    to_branch = models.ForeignKey('Branch', related_name='transfers_in', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.quantity}x {self.product.name}: {self.from_branch} → {self.to_branch}"
 
from django.utils.timezone import now
from django.db import models
from django.contrib.auth.models import User

class StockHistory(models.Model):
    product = models.ForeignKey(
        "Products",
        on_delete=models.CASCADE,
        related_name="stock_history" 
    )
    previous_stock = models.PositiveIntegerField()
    quantity_added = models.PositiveIntegerField(default=0)
    quantity_removed = models.PositiveIntegerField(default=0)
    new_stock = models.PositiveIntegerField()
    previous_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    new_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    supplier = models.ForeignKey('Supplier', null=True, blank=True, on_delete=models.SET_NULL)
    previous_cprice = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shop = models.ForeignKey('Shop', on_delete=models.SET_NULL, null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    new_cprice = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    added_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_on"] 

    
class StockSummaryCache(models.Model):
    product = models.ForeignKey("Products", on_delete=models.CASCADE)
    shop = models.ForeignKey("Shop", on_delete=models.CASCADE, null=True, blank=True)
    branch = models.ForeignKey("Branch", on_delete=models.CASCADE, null=True, blank=True)
    
    stock_ordered = models.PositiveIntegerField(default=0)
    total_sold = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stock_removed = models.PositiveIntegerField(default=0)
    available_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discrepancy = models.IntegerField(default=0)
    
    updated_on = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("product", "branch", "shop")


def __str__(self):
        return f"{self.product.name} | +{self.quantity_added} | -{self.quantity_removed} | {self.added_on.strftime('%Y-%m-%d %H:%M')}"

class Supplier(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=200)
    contact = models.CharField(max_length=100, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class PurchaseOrder(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey(Products, on_delete=models.CASCADE, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2,null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=20, choices=[("cash", "Cash"), ("credit", "Credit")], default="cash",null=True)


    def __str__(self):
        return f"PO-{self.id} | {self.product.name} ({self.quantity})"


class ShiftCurrency(models.Model):
   

    shift = models.ForeignKey("Shift", on_delete=models.CASCADE, related_name="currency_lines")
    currency = models.CharField(max_length=10)

    opening_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)   
    sales_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)      
    expenses_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)   
    expected_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)  
    closing_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)  
    difference = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)      

    class Meta:
        unique_together = ("shift", "currency")
        ordering = ["currency"]

    def __str__(self):
        return f"Shift {self.shift_id} {self.currency}"

    class Meta:
        unique_together = ("shift", "currency")



class Transaction(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    code = models.CharField(max_length=100, unique=True)
    receipt_number = models.CharField(max_length=50,  null=True, blank=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    sub_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    inclusive_tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    exclusive_tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tendered_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_change = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    receipt_format = models.TextField(null=True, blank=True)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=1, null=True)
    shift = models.ForeignKey("Shift", on_delete=models.SET_NULL, null=True, blank=True)
    date_added = models.DateTimeField(default=timezone.now)
    grand_total_base = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    currency = models.CharField(max_length=3, default='USD')  # USD or ZWL
    payment_method = models.CharField(max_length=20, choices=[
        ('Cash','Cash'),('EcoCash','EcoCash'),('Bank','Bank')
    ])
    amount_change = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.code} - {self.customer_name or 'Walk-in'}"
    class Meta:
        unique_together = ("receipt_number","branch")
        
class ReceiptSequence(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("shop", "branch")
        

class SalesItem(models.Model):
    TRANSACTION_TYPES = [
        ("sale", "Sale"),
        ("return", "Return"),
        ("breakage", "Breakage"),
        ]
    transaction = models.ForeignKey(Transaction, related_name="items", on_delete=models.CASCADE)
    
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    qty = models.IntegerField(default=0)
    
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    #sale = models.ForeignKey('Sales', on_delete=models.CASCADE, null=True, blank=True)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True)
    #date_added = models.DateTimeField(default=timezone.now) 
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, default="sale")
    inclusive_tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    exclusive_tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.product.name} x {self.qty}"
        

class ReceiptSettings(models.Model):
    branch = models.OneToOneField(Branch, on_delete=models.CASCADE, related_name="receipt_settings")
    logo = models.ImageField(upload_to="receipt_logos/", blank=True, null=True)
    custom_header_name = models.CharField(max_length=100, blank=True, help_text="Overrides shop name on receipt if set")
    footer_text = models.CharField(max_length=200, blank=True, default="Thank you for shopping with us!")
    paper_width = models.PositiveIntegerField(default=80, help_text="in mm")
    margin_top = models.PositiveIntegerField(default=8)
    margin_bottom = models.PositiveIntegerField(default=8)
    show_timestamp = models.BooleanField(default=True)
    auto_print = models.BooleanField(default=False)

    def __str__(self):
        return f"Receipt settings for {self.shop.name}"
    
class StockAdjustment(models.Model):
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()  # number of items removed
    reason = models.CharField(max_length=50, choices=[("breakage","Breakage"), ("shortage","Shortage")])
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - {self.reason} ({self.quantity})"

class StockCount(models.Model):
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    counted_by = models.ForeignKey(User, on_delete=models.CASCADE)
    report = models.ForeignKey("StockCountReport", on_delete=models.CASCADE, related_name="counts" ,null=True)  # ✅ link
    physical_count = models.IntegerField()
    system_stock = models.IntegerField(default=0) 
    discrepancy = models.IntegerField()
    date_counted = models.DateTimeField()

    def __str__(self):
        return f"{self.product.name} - {self.date_counted.strftime('%Y-%m-%d')}"
    
class StockCountReport(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('SALE', 'Sale'),
        ('RETURN', 'Return'),
        ('BREAKAGE', 'Breakage'),
        ('ADJUST', 'Adjustment'),
    ]
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"{self.product.name} - {self.movement_type} ({self.quantity})"
    

class Order(models.Model):
    oderno = models.CharField(max_length=100, unique=True, null=True, blank=True)
    shop = models.ForeignKey('Shop', on_delete=models.CASCADE)
    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, null=True, blank=True)
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    quantity = models.CharField(max_length=100, null=True, blank=True)
    amount = models.CharField(max_length=100, null=True, blank=True)
    total_amount = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.oderno}"


class ExchangeRate(models.Model):
    BASE_CURRENCY = 'USD' 

    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1.0'))
    updated_at = models.DateTimeField(auto_now=True)
    shop = models.ForeignKey('Shop', on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, null=True, blank=True)
    source = models.CharField(max_length=100, blank=True, null=True)
    
    class Meta:
        unique_together = ("currency", "branch")

    def convert_from_base(self, amount):
        return amount * self.rate

    def convert_to_base(self, amount):
        return amount / self.rate

    def __str__(self):
        return f"1 {self.BASE_CURRENCY} = {self.rate} {self.currency}"


class Role(models.Model):
    shop = models.ForeignKey("Shop", on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    permissions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("shop", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name

from django.contrib.auth import get_user_model

class ExchangeRateAudit(models.Model):
    currency = models.CharField(max_length=10)
    old_rate = models.DecimalField(max_digits=18, decimal_places=2)
    new_rate = models.DecimalField(max_digits=18, decimal_places=2)
    shop = models.ForeignKey('Shop', on_delete=models.CASCADE)
    branch = models.ForeignKey('Branch', on_delete=models.CASCADE, null=True, blank=True)
    changed_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    action = models.CharField(max_length=50)  # UPDATE / CREATE

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.currency} {self.action}"
   
from django.db import models
from django.contrib.auth.models import User

class PaymentProof(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='payment_proofs/')
    submitted_at = models.DateTimeField(auto_now_add=True)
    verified = models.BooleanField(default=False)
    rejected = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} - {'Verified' if self.verified else 'Pending'}"

class StockUpdate(models.Model):
    product = models.ForeignKey(Products, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL)
    old_stock = models.IntegerField()
    added_stock = models.IntegerField()
    old_price = models.FloatField()
    new_price = models.FloatField()
    old_cprice = models.FloatField()
    new_cprice = models.FloatField()
    date_added = models.DateTimeField(default=timezone.now)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=20, choices=[("cash", "Cash"), ("credit", "Credit")], default="cash")


    def __str__(self):
        return f"{self.user} updated {self.product.name} at {self.date_added}"

class CustomUserAdmin(UserAdmin):
    # Remove fields
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'first_name', 'last_name', 'email'),
        }),
    )
    list_display = ("username", "email", "first_name", "last_name")  # no staff/superuser
    list_filter = ()  # remove staff/superuser filter

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    allowed_pages = models.JSONField(default=list)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
    

class Page(models.Model):
    title = models.CharField(max_length=100)
    url_name = models.CharField(max_length=100)  
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    assigned_to = models.ManyToManyField(User, related_name='visible_pages', blank=True)

    def __str__(self):
        return self.title
        
from django.db import models
from django.contrib.auth.models import User

from django.db import models
from django.contrib.auth.models import User

class Shift(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    opened_at = models.DateTimeField(auto_now_add=True)
    money_left = models.DecimalField(max_digits=10, decimal_places=2)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0)  
    closed_at = models.DateTimeField(null=True, blank=True)
    actual_money = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_closed = models.BooleanField(default=False)

    def __str__(self):
        if self.closed_at:
            return f"Shift closed by {self.user.username} at {self.closed_at}"
        return f"Shift opened by {self.user.username} at {self.opened_at}"
    
    def difference(self):
        if self.actual_money is not None:
            return self.actual_money - self.money_left
        return None

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class SaleDeletionLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    transaction = models.ForeignKey(Transaction, on_delete=models.SET_NULL, null=True)
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True)
    product_details = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    reason = models.TextField(blank=True)
    deleted_at = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        return f"Sale {self.transaction_id} deleted by {self.user}"

class Expense(models.Model):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE)
    currency = models.CharField(max_length=10, default="USD")
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=1)   # units per 1 USD, saved when logged
    amount_usd = models.DecimalField(max_digits=12, decimal_places=2, default=0)      # saved when logged
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Freeze the rate the first time the expense is saved. Later rate changes never touch it.
        if not self.amount_usd:
            from .utils import rate_at
            rate = rate_at(self.shop, self.branch, self.currency, self.created_at or timezone.now()) or Decimal("1")
            self.exchange_rate = rate
            self.amount_usd = (Decimal(str(self.amount)) / rate).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

from django.db import models

class Item(models.Model):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    stock = models.IntegerField(default=0)

    def __str__(self):
        return self.name
 
class CreditPayment(models.Model):
    purchase_order = models.ForeignKey('PurchaseOrder', on_delete=models.CASCADE, related_name='credits')
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status_choices = (
        ('pending', 'Pending'),
        ('partial', 'Partial'),
        ('paid', 'Paid'),
    )
    status = models.CharField(max_length=10, choices=status_choices, default='pending')
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def remaining_amount(self):
        return self.total_amount - self.paid_amount
   

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class StockSnapshot(models.Model):
    """Stock value taken automatically when a shift opens or closes."""
    KIND = [("opening", "Opening"), ("closing", "Closing")]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    shift = models.ForeignKey("Shift", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="stock_snapshots")
    kind = models.CharField(max_length=10, choices=KIND)
    cost_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)    # stock x cost price
    retail_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)  # stock x selling price
    taken_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-taken_at"]


class ExchangeRateHistory(models.Model):
    """Every rate a currency has had, with the moment it took effect."""
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    currency = models.CharField(max_length=10)
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    effective_from = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-effective_from"]
        indexes = [models.Index(fields=["shop", "branch", "currency", "effective_from"])]

    def __str__(self):
        return f"{self.currency} {self.rate} from {self.effective_from:%Y-%m-%d %H:%M}"

class IncomeEntry(models.Model):
    """Figures the user types in for the income statement."""
    TYPES = [
        ("fixed", "Fixed cost"),
        ("variable", "Variable cost"),
        ("purchase", "Purchase"),
        ("opening_stock", "Opening stock"),
        ("sales_return", "Sales return"),
    ]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    entry_type = models.CharField(max_length=20, choices=TYPES)
    name = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    amount_usd = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # fixed at entry time
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_entry_type_display()}: {self.name} {self.amount_usd}"

class PrintJob(models.Model):
    PENDING, PRINTING, DONE, FAILED = "pending", "printing", "done", "failed"
    STATUS_CHOICES = [(PENDING, "Pending"), (PRINTING, "Printing"),
                      (DONE, "Done"), (FAILED, "Failed")]

    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="print_jobs")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class PrinterSettings(models.Model):
    PAPER_SIZES = [
        ('A4', 'A4 (210mm x 297mm)'),
        ('80', 'Thermal 80mm'),
        ('58', 'Thermal 58mm'),
    ]
    CONNECTION_TYPES = [
        ('system', 'System Printer'),
        ('bluetooth', 'Bluetooth (BLE)'),
    ]

    shop = models.OneToOneField('posApp.Shop', on_delete=models.CASCADE, related_name='printer_settings')
    printer_name = models.CharField(max_length=255, blank=True, null=True, help_text="Name of the printer")
    paper_size = models.CharField(max_length=10, choices=PAPER_SIZES, default='80')
    auto_print = models.BooleanField(default=False, help_text="Automatically print after sale?")
    margin_top = models.IntegerField(default=5)
    margin_bottom = models.IntegerField(default=5)
    header_text = models.CharField(max_length=255, blank=True, null=True)
    footer_text = models.CharField(max_length=255, blank=True, null=True)

    connection_type = models.CharField(max_length=20, choices=CONNECTION_TYPES, default='system')
    ble_service_uuid = models.CharField(
        max_length=100, blank=True, default="49535343-fe7d-4ae5-8fa9-9fafd205e455"
    )
    ble_characteristic_uuid = models.CharField(
        max_length=100, blank=True, default="49535343-1e4d-4bd9-ba61-23c647249616"
    )

    last_updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.shop.name} Printer Settings"
    
class CreditInstallment(models.Model):
    credit_payment = models.ForeignKey(CreditPayment, on_delete=models.CASCADE, related_name='installments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    paid_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    paid_at = models.DateTimeField(auto_now_add=True)

GRACE_PERIOD_DAYS = 3


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration_days = models.PositiveIntegerField(default=30)  # e.g., 30 for monthly, 365 for yearly
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} (${self.price})"


class BillingMethod(models.Model):
    PROVIDERS = [
        ("ecocash", "EcoCash"),
        ("bank", "Bank Transfer"),
        ("card", "Debit/Credit Card"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    provider = models.CharField(max_length=50, choices=PROVIDERS)
    account_number = models.CharField(max_length=100)  # e.g. EcoCash number
    is_default = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username} - {self.provider}"

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserActivity(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    activity_type = models.CharField(
        max_length=50,
        choices=[('login', 'Login'), ('logout', 'Logout'), ('page_view', 'Page View')]
    )
    page_url = models.CharField(max_length=500, blank=True, null=True)
    timestamp = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    session_duration = models.DurationField(blank=True, null=True)  # duration in seconds

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user.username} - {self.activity_type} - {self.timestamp}"


class Subscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField()
    next_billing_date = models.DateField()
    is_active = models.BooleanField(default=True)
    auto_renew = models.BooleanField(default=False)
    custom_name = models.CharField(max_length=100, blank=True, null=True)
    custom_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    custom_duration_days = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} → {self.plan.name}"

    def is_in_grace_period(self):
        today = timezone.now().date()
        return self.end_date < today <= self.end_date + timedelta(days=GRACE_PERIOD_DAYS)

    def renew(self, auto=False):
        today = timezone.now().date()

        # Try to charge user before extending subscription
        if not self.charge_user():
            self.is_active = False
            self.save()
            return False  # ❌ Renewal failed due to no payment

        # ✅ inside grace period → continue old cycle
        if today <= self.end_date + timedelta(days=GRACE_PERIOD_DAYS):
            new_start = self.end_date
        else:
            new_start = today  # fresh start

        self.start_date = new_start
        self.end_date = new_start + timedelta(days=self.plan.duration_days)
        self.next_billing_date = self.end_date
        self.is_active = True
        self.save()
        return True

    def has_access(self):
        """Check if subscription allows POS access"""
        today = timezone.now().date()
        if self.is_active and self.end_date >= today:
            return True
        if self.is_in_grace_period():
            return True
        return False


from django.db import models
from django.contrib.auth.models import User

class PaymentTransaction(models.Model):
    PROVIDERS = [
        ("ecocash", "EcoCash"),
        ("bank", "Bank Transfer"),
        ("card", "Debit/Credit Card"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    provider = models.CharField(max_length=20, choices=PROVIDERS, default="ecocash")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField(max_length=200)
    poll_url = models.TextField()  
    status = models.CharField(max_length=50, default="Pending")  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.amount} ({self.status})"


class PlanPayment(models.Model):
    STATUS = [("pending", "Pending"), ("paid", "Paid"),
              ("failed", "Failed"), ("cancelled", "Cancelled")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="plan_payments")
    plan = models.ForeignKey("SubscriptionPlan", on_delete=models.PROTECT)
    method = models.CharField(max_length=20)                  
    phone = models.CharField(max_length=20, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField(max_length=64, unique=True)
    poll_url = models.CharField(max_length=500, blank=True)
    redirect_url = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    activated = models.BooleanField(default=False)           
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.reference} {self.method} {self.status}"