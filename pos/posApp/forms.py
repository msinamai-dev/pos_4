# payments/forms.py

from django import forms
from .models import PaymentProof

class PaymentProofForm(forms.ModelForm):
    class Meta:
        model = PaymentProof
        fields = ['image']
# posApp/forms.py

from django import forms
from .models import Department

PAGES = [
    ('dashboard', 'Dashboard'),
    ('sales', 'Sales'),
    ('products', 'Products'),
    ('subscriptions', 'Subscriptions'),
    ('users', 'Manage Users'),
]

class DepartmentForm(forms.ModelForm):
    allowed_pages = forms.MultipleChoiceField(
        choices=PAGES,
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    class Meta:
        model = Department
        fields = ['name', 'allowed_pages']

from django import forms
from .models import Shop

class ShopForm(forms.ModelForm):
    class Meta:
        model = Shop
        fields = ['name', 'location']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter shop name'
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter shop location'
            }),
        }
        labels = {
            'name': 'Shop Name',
            'location': 'Shop Location',
        }
from django import forms
from .models import Branch
from .models import ReceiptSettings

class ReceiptSettingsForm(forms.ModelForm):
    class Meta:
        model = ReceiptSettings
        fields = ["logo", "custom_header_name", "footer_text", "paper_width",
                  "margin_top", "margin_bottom", "show_timestamp", "auto_print"]

class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ['name', 'location', 'shop', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'location': forms.TextInput(attrs={'class': 'form-control'}),
            'shop': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
from django import forms
from django.contrib.auth.models import User, Permission
from .models import Branch

class CustomUserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

    class Meta:
        model = User
        fields = ['first_name','last_name','username', 'email', 'password', 'is_staff']

    def __init__(self, *args, **kwargs):
        shop = kwargs.pop('shop', None)
        super().__init__(*args, **kwargs)
        if shop:
            self.fields['branch'].queryset = Branch.objects.filter(shop=shop)
from django import forms

class ExcelUploadForm(forms.Form):
    excel_file = forms.FileField(label="Upload Excel File")

from django import forms
from django.contrib.auth.models import User

from .models import UserProfile, Shop, Branch, Department


from django import forms
from .models import Products, Branch
class TransferProductForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Products.objects.all())
    destination_branch = forms.ModelChoiceField(queryset=Branch.objects.all())
    quantity = forms.IntegerField()


from .models import UserProfile, Shop, Branch, Role
from .permissions_registry import flat_permissions, normalise
from .decorators import can_grant


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    is_superuser = forms.BooleanField(required=False, label="Superuser")
    is_staff = forms.BooleanField(required=False, label="Staff")

    class Meta:
        model = User
        fields = ["first_name","last_name","username", "email", "password", "is_superuser", "is_staff"]


class UserProfileForm(forms.ModelForm):
    role = forms.ModelChoiceField(
        queryset=Role.objects.none(),
        required=False,
        empty_label="— No role (extra permissions only) —",
    )
    permissions = forms.MultipleChoiceField(
        choices=[],                      # filled in __init__ so new pages appear automatically
        required=False,
        label="Extra permissions",
        widget=forms.CheckboxSelectMultiple,
    )
    subscribe_now = forms.BooleanField(required=False, label="Subscribe this user?")

    class Meta:
        model = UserProfile
        fields = ['phone_number', 'country', 'shop', 'branch', 'role', 'permissions']

    def __init__(self, *args, **kwargs):
        self.admin_user = kwargs.pop('admin_user', None)
        super().__init__(*args, **kwargs)

        self.fields['permissions'].choices = flat_permissions()   # always up to date

        admin_profile = getattr(self.admin_user, 'userprofile', None)
        if admin_profile and admin_profile.shop:
            shop = admin_profile.shop
            self.fields['shop'].queryset = Shop.objects.filter(id=shop.id)
            self.fields['branch'].queryset = Branch.objects.filter(shop=shop)
            self.fields['role'].queryset = Role.objects.filter(shop=shop)
        else:
            self.fields['shop'].queryset = Shop.objects.none()
            self.fields['branch'].queryset = Branch.objects.none()

        if self.instance and self.instance.pk:
            self.initial['permissions'] = sorted(normalise(self.instance.permissions))

    def clean_permissions(self):
        codes = normalise(self.cleaned_data.get('permissions'))
        existing = normalise(self.instance.permissions) if self.instance.pk else set()
        if not can_grant(self.admin_user, codes - existing):
            raise forms.ValidationError("You can't give permissions that you don't have yourself.")
        return sorted(codes)

    def clean_role(self):
        role = self.cleaned_data.get('role')
        if role and role.pk != self.instance.role_id and not can_grant(self.admin_user, role.permissions):
            raise forms.ValidationError("That role has permissions you don't have yourself.")
        return role