import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pickle import FALSE
from types import SimpleNamespace
from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from .models import PlanPayment
from .payments.utils import start_mobile_payment, start_card_payment, check_status, normalise_phone
from posApp.income_statement import build_income_statement
from .models import IncomeEntry, ProductTransfer, ReceiptSequence, ShiftCurrency, ShiftCurrency, StockUpdate 
import json, sys
import base64, hashlib, hmac, os, uuid
from urllib.parse import urlencode, urlparse, urlsplit
from .models import ReceiptSettings
from .forms import  ReceiptSettingsForm
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from django.db import models
from django.core.paginator import Paginator
import io
from posApp.context_processors import COUNTRY_CODES
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from django.http import FileResponse, HttpResponse
from openpyxl import Workbook
from django.shortcuts import render, redirect, get_object_or_404
from django.http import  JsonResponse
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User, Group
from django.db.models import Q, F, Sum, Count, Case, When, Value, FloatField
from django.utils.timezone import now, localtime, make_aware
from django.contrib import messages
from .models import ExchangeRate, ExchangeRateAudit, PrinterSettings, Products, Branch, Category, PurchaseOrder, SaleDeletionLog, StockAdjustment, Subscription, SubscriptionPlan, Supplier, CreditInstallment, CreditPayment, UserActivity
import pandas as pd
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST, require_http_methods
from django.db.models.functions import TruncDate
from fpdf import FPDF
from django.utils.timezone import now, timedelta
from .models import  UserProfile, Department
from .forms import DepartmentForm
from .forms import BranchForm
from django.urls import reverse
from .forms import ShopForm
from .forms import CustomUserForm
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .permissions import filter_queryset_for_user
from django.core.mail import send_mail
from .models import Shift
from .models import Expense
from django.shortcuts import render
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models.functions import TruncMonth
from posApp.models import (
    Category, Products,  SalesItem, Transaction, StockHistory,
    UserProfile, PaymentProof, Shop
)
from django.contrib.auth import login
from django.urls import reverse
from django.contrib.auth.models import User
from .models import UserProfile, Shop, Branch, ERPShopLinkRequest, ERPShopLinkAudit
import logging
from django.db import transaction as db_transaction
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.http import JsonResponse
from datetime import timedelta
from django.contrib.auth.hashers import make_password
from .forms import UserCreateForm, UserProfileForm
from .decorators import permission_required, subscription_required, permissions_for_user, get_user_permissions
from .permissions_registry import all_codes, create_default_roles, normalise, get_permission_groups
from django.core.exceptions import PermissionDenied
from .models import Role
from django.shortcuts import render, get_object_or_404, redirect
from django.shortcuts import render
from django.db.models import Count, Max
from .models import UserActivity
from django.core.exceptions import ValidationError
import pandas as pd
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.utils.text import slugify
from .models import Products, Category, Branch
from .models import Products, StockCount, StockCountReport
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
import base64  


from .utils import BASE_CURRENCY, convert_amount, convert_amount, end_of_day, generate_unique_code, is_shift_open, rate_at, rate_at, round_2dp, shift_close_currencies, shop_currencies, take_stock_snapshot, transfer_product_logic  
import json
from collections import OrderedDict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace

from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

def _erp_link_secret():
    return os.environ.get("POS_LINK_SHARED_SECRET", "").encode("utf-8")


def _verify_erp_link_token(token):
    secret = _erp_link_secret()
    if len(secret) < 32 or not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if int(payload.get("expires_at", 0)) <= int(timezone.now().timestamp()):
        return None
    return payload


def _sign_erp_callback(values):
    secret = _erp_link_secret()
    if len(secret) < 32:
        raise ValueError("POS_LINK_SHARED_SECRET must contain at least 32 characters.")
    raw = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, raw, hashlib.sha256).hexdigest()


@login_required
@require_http_methods(["GET", "POST"])
def erp_link_authorize(request):
    """POS-side owner consent page for a short-lived ERP shop-link request."""
    token = request.GET.get("token") or request.POST.get("token", "")
    payload = _verify_erp_link_token(token)
    if not payload:
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Invalid shop-link request",
            "message": "This link is invalid or has expired. Ask the ERP administrator to create a new request.",
            "success": False,
        }, status=400)
    try:
        shop = Shop.objects.select_related("owner", "owner__user").get(pk=int(payload["shop_id"]), is_active=True)
        request_uuid = uuid.UUID(str(payload["request_id"]))
        expires_at = timezone.datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.get_current_timezone())
    except (Shop.DoesNotExist, KeyError, ValueError, TypeError, OverflowError):
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Shop unavailable", "message": "The requested shop could not be found or is inactive.", "success": False,
        }, status=404)
    # Never allow a signed token to redirect the browser to an arbitrary host.
    callback = payload.get("return_url", "")
    expected_base = os.environ.get("ERP_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    if not callback.startswith(expected_base + "/"):
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Invalid return address", "message": "The ERP callback address is not trusted. Contact the system administrator.", "success": False,
        }, status=400)
    link_request, created = ERPShopLinkRequest.objects.get_or_create(
        request_id=request_uuid,
        defaults={
            "company_id": int(payload["company_id"]), "company_name": str(payload["company_name"])[:255],
            "shop": shop, "branch_id": payload.get("branch_id"), "requested_by": str(payload.get("requested_by", ""))[:150],
            "token_nonce": str(payload["nonce"]), "expires_at": expires_at,
        },
    )
    if not created and (link_request.shop_id != shop.pk or link_request.company_id != int(payload["company_id"]) or link_request.token_nonce != str(payload["nonce"])):
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Request mismatch", "message": "This request ID has already been used for a different shop or company.", "success": False,
        }, status=400)
    if link_request.status != ERPShopLinkRequest.Status.PENDING:
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Request already processed", "message": f"This request is already {link_request.get_status_display().lower()}.", "success": False,
        }, status=409)
    if link_request.expires_at <= timezone.now():
        link_request.status = ERPShopLinkRequest.Status.EXPIRED
        link_request.save(update_fields=["status"])
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Request expired", "message": "This request has expired. Ask the ERP administrator to start again.", "success": False,
        }, status=410)
    if shop.owner.user_id != request.user.id:
        return render(request, "posApp/erp_link_result.html", {
            "page_title": "Owner verification required",
            "message": "You are signed in, but this account is not the registered owner of this shop. Sign in as the shop owner to approve or reject this request.",
            "success": False, "shop": shop,
        }, status=403)
    if request.method == "GET":
        return render(request, "posApp/erp_link_authorize.html", {
            "page_title": "Approve ERP shop connection", "shop": shop, "link_request": link_request, "token": token,
        })
    decision = request.POST.get("decision", "")
    if decision not in {"approved", "rejected"}:
        return render(request, "posApp/erp_link_authorize.html", {
            "page_title": "Approve ERP shop connection", "shop": shop, "link_request": link_request,
            "token": token, "error": "Choose approve or reject.",
        }, status=400)
    with db_transaction.atomic():
        link_request.status = decision
        link_request.owner_user = request.user
        link_request.decided_at = timezone.now()
        link_request.save(update_fields=["status", "owner_user", "decided_at"])
        ERPShopLinkAudit.objects.create(
            link_request=link_request, request_id=link_request.request_id,
            company_id=link_request.company_id, company_name=link_request.company_name,
            shop_id=shop.pk, event="owner_approved" if decision == "approved" else "owner_rejected",
            actor=request.user, actor_label=request.user.get_username(),
            details={"branch_id": link_request.branch_id, "requested_by": link_request.requested_by},
        )
    callback_values = {
        "request_id": str(link_request.request_id), "company_id": str(link_request.company_id),
        "shop_id": str(shop.pk), "decision": decision, "pos_user_id": str(request.user.pk),
        "approved_at": timezone.now().isoformat(),
    }
    callback_values["signature"] = _sign_erp_callback({k: v for k, v in callback_values.items() if k != "signature"})
    return redirect(callback + "?" + urlencode(callback_values))


@login_required
@require_http_methods(["GET"])
def erp_link_requests(request):
    """List ERP requests for shops owned by the signed-in POS user only."""
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    owned_shop_ids = Shop.objects.filter(owner__user=request.user).values_list("id", flat=True)
    expired_requests = ERPShopLinkRequest.objects.filter(shop_id__in=owned_shop_ids, status=ERPShopLinkRequest.Status.PENDING, expires_at__lte=timezone.now())
    for expired_request in expired_requests:
        expired_request.status = ERPShopLinkRequest.Status.EXPIRED
        expired_request.save(update_fields=["status"])
        ERPShopLinkAudit.objects.create(link_request=expired_request, request_id=expired_request.request_id,
            company_id=expired_request.company_id, company_name=expired_request.company_name,
            shop_id=expired_request.shop_id, event="request_expired", actor=None, actor_label="system",
            details={"reason": "approval window elapsed"})
    queryset = ERPShopLinkRequest.objects.filter(shop__owner__user=request.user).select_related("shop", "owner_user")
    if q:
        queryset = queryset.filter(Q(company_name__icontains=q) | Q(shop__name__icontains=q) | Q(requested_by__icontains=q))
    valid_statuses = {choice[0] for choice in ERPShopLinkRequest.Status.choices}
    if status in valid_statuses:
        queryset = queryset.filter(status=status)
    try:
        page_size = int(request.GET.get("page_size", "15"))
    except (TypeError, ValueError):
        page_size = 15
    if page_size not in (10, 15):
        page_size = 15
    paginator = Paginator(queryset.order_by("-created_at"), page_size)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "posApp/erp_link_requests.html", {
        "page_title": "ERP shop connections", "page_obj": page_obj, "q": q,
        "status_filter": status, "page_size": page_size, "query_string": params.urlencode(),
        "status_choices": ERPShopLinkRequest.Status.choices,
    })

def _form_errors(*forms):
    """JSON-safe {field: [messages]} from one or more forms."""
    out = {}
    for f in forms:
        for field, errs in f.errors.items():
            out[field] = [str(e) for e in errs]
    return out


def register_user(request):
    if request.method != "POST":
        return render(request, "posApp/register.html")

    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    firstname = request.POST.get("firstname", "").strip()
    lastname = request.POST.get("lastname", "").strip()
    password = request.POST.get("password") or ""
    business_type = request.POST.get("business_type") or "other"
    business_name = request.POST.get("business_name") or "My Business"
    phone_number = request.POST.get("phone_number") or "077*********"
    country = request.POST.get("country") or "Zimbabwe"
    shop_name = request.POST.get("shop_name") or business_name or "Default Shop"
    branch_name = request.POST.get("branch_name") or "Main Branch"

    # ---- validation ----
    if not username or not password:
        return JsonResponse({"status": "failed", "message": "Username and password are required."})
    if User.objects.filter(username__iexact=username).exists():
        return JsonResponse({"status": "failed", "message": "Username already exists."})
    if email and User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"status": "failed", "message": "Email already exists."})

    try:
        with transaction.atomic():
            # default subscription plans
            default_plans = {
                "BASIC": {"price": 9.99, "duration_days": 30, "description": "Free starter plan"},
                "PREMIUM": {"price": 19.99, "duration_days": 30, "description": "Monthly premium plan"},
                "ANNUAL": {"price": 199.99, "duration_days": 365, "description": "Yearly subscription plan"},
            }
            for name, details in default_plans.items():
                SubscriptionPlan.objects.get_or_create(name=name, defaults=details)

            user = User.objects.create_user(
                username=username, first_name=firstname, last_name=lastname,
                password=password, email=email,
                is_staff=True, is_superuser=True,
            )

            profile, _ = UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "business_type": business_type,
                    "phone_number": phone_number,
                    "must_change_password": False,
                    "country": country,
                    "business_name": business_name,
                },
            )

            shop, _ = Shop.objects.get_or_create(
                owner=profile, name=shop_name, defaults={"location": "Default Location"})
            branch, _ = Branch.objects.get_or_create(
                shop=shop, name=branch_name, defaults={"location": "Default Location"})

        
            roles = create_default_roles(shop)
            profile.shop = shop
            profile.branch = branch
            profile.role = roles["Administrator"]
            profile.permissions = list(all_codes())
            profile.access_rights = {}
            profile.save()

            basic_plan = SubscriptionPlan.objects.get(name="PREMIUM")
            today = timezone.now().date()
            end_date = today + timedelta(days=basic_plan.duration_days)
            Subscription.objects.create(
                user=user, plan=basic_plan,
                start_date=today, end_date=end_date,
                next_billing_date=end_date, is_active=True,
            )

            login(request, user)

        return JsonResponse({
            "status": "success",
            "redirect_url": reverse("home-page"),
            "message": "Account created successfully!",
        })

    except IntegrityError as e:
        logger.error(f"Integrity error during registration: {e}", exc_info=True)
        return JsonResponse({"status": "failed",
                             "message": "Registration failed due to duplicate data or database issue."})
    except Exception as e:
        logger.error(f"Unexpected error during registration: {e}", exc_info=True)
        return JsonResponse({"status": "failed",
                             "message": "Registration failed. Please try again later."})

login_required
@permission_required('user_edit')
@subscription_required
def set_user_active(request, user_id):
    """POST (AJAX): active=1 activates the user, active=0 deactivates them."""
    if request.method != "POST":
        return JsonResponse({"status": "failed", "message": "Invalid request."}, status=405)

    admin_profile = request.user.userprofile
    target = get_object_or_404(
        UserProfile.objects.select_related("user", "shop"),
        user_id=user_id,
        shop=admin_profile.shop,
    )
    u = target.user
    make_active = request.POST.get("active") == "1"

    if u.pk == request.user.pk:
        return JsonResponse({"status": "failed",
                             "message": "You can't deactivate your own account."}, status=400)
    if u.is_superuser and not request.user.is_superuser:
        return JsonResponse({"status": "failed",
                             "message": "You can't change a superuser's account."}, status=403)
    if target.shop and target.shop.owner_id == target.pk and not make_active:
        return JsonResponse({"status": "failed",
                             "message": "The shop owner can't be deactivated."}, status=400)

    u.is_active = make_active
    u.save(update_fields=["is_active"])

    messages.success(request, f"{u.username} was {'activated' if make_active else 'deactivated'}.")
    return JsonResponse({"status": "success", "is_active": u.is_active})


@login_required                      
@permission_required('create_user')
@subscription_required
def create_user_page(request):
    """GET: show the Add User page."""
    user_form = UserCreateForm()
    profile_form = UserProfileForm(admin_user=request.user)
    if not request.user.is_superuser:
        user_form.fields.pop('is_superuser')

    context = {"user_form": user_form, "profile_form": profile_form}
    context.update(permission_context(request))
    return render(request, "posApp/create_user.html", context)


@login_required
@permission_required('create_user')
@subscription_required
def create_user(request):
   
    if request.method != "POST":
        return JsonResponse({'status': 'error', 'message': 'Invalid request'})

    admin_profile = getattr(request.user, "userprofile", None)
    if not admin_profile or not admin_profile.shop:
        return JsonResponse({'status': 'error', 'message': 'Your account is not linked to a shop.'})

    user_form = UserCreateForm(request.POST)
    profile_form = UserProfileForm(request.POST, admin_user=request.user)
    if not request.user.is_superuser:
        user_form.fields.pop('is_superuser')     # only superusers can create superusers

    if not (user_form.is_valid() and profile_form.is_valid()):
        return JsonResponse({'status': 'error', 'message': _form_errors(user_form, profile_form)})

    cd = profile_form.cleaned_data
    role = cd.get('role')
    extras = cd.get('permissions') or []

    # a user with no role and no permissions could not open any page
    if not role and not extras:
        return JsonResponse({'status': 'error',
                             'message': 'Pick a role or tick at least one permission for this user.'})

    # branch admins can only add people to their own branch
    branch = cd.get('branch') or admin_profile.branch
    if not request.user.is_superuser and admin_profile.branch:
        branch = admin_profile.branch

    try:
        with transaction.atomic():
            user = user_form.save(commit=False)
            user.set_password(user_form.cleaned_data["password"])
            user.is_superuser = bool(request.user.is_superuser and user_form.cleaned_data.get("is_superuser"))
            user.is_staff = user_form.cleaned_data.get("is_staff", False)
            user.save()

            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'shop': admin_profile.shop,
                    'branch': branch,
                    'role': role,
                    'permissions': extras,           # extras only; the role's permissions are merged at check time
                    'phone_number': cd.get('phone_number'),
                    "must_change_password": True,
                    'country': cd.get('country'),
                    'business_name': admin_profile.business_name,
                    'business_type': admin_profile.business_type,
                },
            )

            if cd.get('subscribe_now'):
                plan, _ = SubscriptionPlan.objects.get_or_create(
                    name="BASIC",
                    defaults={"price": 9.99, "duration_days": 30, "description": "Free starter plan"})
                today = timezone.now().date()
                end_date = today + timedelta(days=plan.duration_days)
                Subscription.objects.create(
                    user=user, plan=plan,
                    start_date=today, end_date=end_date,
                    next_billing_date=end_date, is_active=True,
                )
    except IntegrityError:
        return JsonResponse({'status': 'error', 'message': 'That username already exists.'})

    return JsonResponse({
        'status': 'success',
        'message': f'{user.username} created successfully.',
        'redirect_url': reverse('user-list'),
    })



@login_required
def logoutuser(request):
    logout(request)
    return redirect('login')

def permission_context(request, selected=()):
    """Everything the permission picker template needs."""
    mine = permissions_for_user(request.user)          # <- this defines `mine`
    groups = [
        (group, [(code, label, code in mine) for code, label in items])
        for group, items in get_permission_groups()
    ]

    role_permissions = {}
    profile = getattr(request.user, "userprofile", None)
    if profile and profile.shop:
        for role in Role.objects.filter(shop=profile.shop):
            role_permissions[role.pk] = sorted(normalise(role.permissions))

    return {
        "permission_groups": groups,
        "role_permissions": role_permissions,
        "selected_permissions": list(selected),
    }

@login_required
@permission_required('user_list', 'create_user', any_of=True)
@subscription_required
def user_list(request):
    shop, branch = get_user_shop_and_branch(request)

    users = UserProfile.objects.select_related("user", "shop", "branch", "role").filter(shop=shop)

    selected_branch = None
    all_branches = None
    if request.user.is_superuser:
        all_branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch_id")
        if branch_id:
            selected_branch = all_branches.filter(id=branch_id).first()
            if selected_branch:
                users = users.filter(branch=selected_branch)
    elif branch:
        users = users.filter(branch=branch)

    # numbers for the cards (they follow the branch filter, not the status filter)
    active_count = users.filter(user__is_active=True).count()
    inactive_count = users.filter(user__is_active=False).count()

    status = request.GET.get("status")
    if status == "active":
        users = users.filter(user__is_active=True)
    elif status == "inactive":
        users = users.filter(user__is_active=False)
    else:
        status = ""

    context = {
        "users": users,
        "all_branches": all_branches,
        "selected_branch_id": selected_branch.id if selected_branch else None,
        "total_count": active_count + inactive_count,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "status": status,
    }
    return render(request, "posApp/user_list.html", context)



def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


@login_required
@permission_required('user_edit')
@subscription_required
def edit_user(request, user_id):
    admin_profile = request.user.userprofile
    target = get_object_or_404(
        UserProfile.objects.select_related("user", "role", "branch", "shop"),
        user_id=user_id,
        shop=admin_profile.shop,
    )
    u = target.user

   
    if u.is_superuser and not request.user.is_superuser:
        raise PermissionDenied

    form = UserProfileForm(request.POST or None, instance=target, admin_user=request.user)
    for name in ("shop", "subscribe_now"):          # not editable here
        form.fields.pop(name, None)

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()

        errors = {}
        if not username:
            errors["username"] = ["Username is required."]
        elif User.objects.filter(username__iexact=username).exclude(pk=u.pk).exists():
            errors["username"] = ["That username is already taken."]

        profile_ok = form.is_valid()
        if not profile_ok:
            errors.update({f: [str(e) for e in errs] for f, errs in form.errors.items()})

        if not errors:
            with transaction.atomic():
                u.username = username
                u.email = email
                u.is_staff = "is_staff" in request.POST
                # only superusers can change this, and nobody can change their own
                if request.user.is_superuser and u.pk != request.user.pk:
                    u.is_superuser = "is_superuser" in request.POST
                u.save()
                form.save()

            if _is_ajax(request):
                return JsonResponse({"status": "success", "message": f"{u.username} updated."})
            messages.success(request, f"{u.username} updated.")
            return redirect('user-list')

        if _is_ajax(request):
            return JsonResponse({"status": "error", "message": errors})

    selected = (set(form.data.getlist('permissions')) if request.method == "POST"
                else normalise(target.permissions))

    context = {
        "user_obj": u,
        "profile_form": form,
        "permission_groups": PERMISSION_GROUPS,
        "selected": selected,
        "grantable": permissions_for_user(request.user),   
    }

    # The modal loads this by AJAX and gets only the form, not the whole page
    if _is_ajax(request):
        return render(request, "posApp/edit_user_modal.html", context)

    context.update(permission_context(request, selected=selected))
    return render(request, "posApp/edit_user.html", context)


def login_use(request):
    
    resp = {"status": "failed", "msg": ""}

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_active:
                login(request, user)
                resp["status"] = "success"
                resp["redirect_url"] = "/home/"
            else:
                resp["msg"] = "Your account is inactive."
        else:
            resp["msg"] = "Incorrect username or password."

    return JsonResponse(resp)



def parse_amount(value):
    """'12.5' -> Decimal('12.50'). Raises ValueError on blank, bad or negative input."""
    try:
        amount = Decimal(str(value).strip()).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        raise ValueError("Enter a valid amount.")
    if amount < 0:
        raise ValueError("Amounts cannot be negative.")
    return amount


def currency_sort_key(code):
    return (code != "USD", code)


def login_user(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # Handle "remember me"
            if not request.POST.get('remember_me'):
                request.session.set_expiry(0)     

            return JsonResponse({'status': 'success', 'msg': 'Login successful'})

        blocked = User.objects.filter(username=username, is_active=False).first()
        if blocked and blocked.check_password(password):
            return JsonResponse({
                'status': 'failed',
                'deactivated': True,
                'msg': 'Your account has been deactivated. Please contact the administrator.',
            }, status=403)

        return JsonResponse({'status': 'failed', 'msg': 'Incorrect username or password.'}, status=400)

    return render(request, "posApp/login.html")


from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.http import JsonResponse
from django.shortcuts import render


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.shortcuts import render, redirect

from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse

DEFAULT_PASSWORD = "1234"


@login_required
@permission_required('user_edit')
@subscription_required
def reset_user_password(request, user_id):
    """POST (AJAX): admin resets a user's password to 1234 and forces a change at next login."""
    if request.method != "POST":
        return JsonResponse({"status": "failed", "message": "Invalid request."}, status=405)

    admin_profile = request.user.userprofile
    target = get_object_or_404(
        UserProfile.objects.select_related("user"),
        user_id=user_id,
        shop=admin_profile.shop,
    )
    u = target.user

    if u.pk == request.user.pk:
        return JsonResponse({"status": "failed",
                             "message": "Use Change Password for your own account."}, status=400)
    if u.is_superuser and not request.user.is_superuser:
        return JsonResponse({"status": "failed",
                             "message": "You can't reset a superuser's password."}, status=403)

    with transaction.atomic():
        u.set_password(DEFAULT_PASSWORD)          # also signs them out of any open session
        u.save(update_fields=["password"])
        target.must_change_password = True
        target.save(update_fields=["must_change_password"])

    return JsonResponse({
        "status": "success",
        "message": f"Password for {u.username} was reset to {DEFAULT_PASSWORD}. "
                   f"They must change it the next time they sign in.",
    })


@login_required
def change_password(request):
    profile = getattr(request.user, "userprofile", None)
    forced = bool(profile and profile.must_change_password)

    form = PasswordChangeForm(request.user, request.POST or None)
    form.fields["old_password"].label = "Current password"
    form.fields["new_password1"].label = "New password"
    form.fields["new_password2"].label = "Confirm new password"
    for f in form.fields.values():
        f.widget.attrs.update({"class": "form-control"})

    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["new_password1"] == DEFAULT_PASSWORD:
            form.add_error("new_password1",
                           f"Choose a different password. {DEFAULT_PASSWORD} is only the temporary one.")
        else:
            form.save()
            update_session_auth_hash(request, request.user)      # keep them signed in
            UserProfile.objects.filter(user=request.user).update(must_change_password=False)
            messages.success(request, "Password changed successfully.")
            return redirect(reverse("pos-page"))

    return render(request, "posApp/change_password.html", {
        "form": form,
        "forced": forced,
        "logout_url": reverse(logoutuser),
    })


from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

@login_required
def toggle_theme(request):
    profile = request.user.userprofile
    profile.theme = "dark" if profile.theme == "light" else "light"
    profile.save(update_fields=["theme"])
    return JsonResponse({"theme": profile.theme})

@api_view(['POST'])
def login_api(request):
    username = request.data.get('username')
    password = request.data.get('password')

    user = authenticate(username=username, password=password)
    if user is None:
        return Response({'status': 'failed', 'message': 'Invalid username or password'})

    # Get shop and branch
    try:
        shop = user.shop  # or however you link user to shop
        branch = user.branch
    except:
        return Response({'status': 'failed', 'message': 'No shop or branch assigned'})

    return Response({
        'status': 'success',
        'shop_id': shop.id,
        'branch_id': branch.id
    })

def get_user_shop_and_branch(request):
    user = request.user
    logger.info(f"Resolving shop/branch for user: {user}")

    if not user.is_authenticated:
        logger.warning("Anonymous user -> No shop/branch")
        return None, None

    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        logger.warning(f"No UserProfile found for {user}")
        return None, None

    shop = getattr(profile, "shop", None)
    branch = getattr(profile, "branch", None)

    if not branch and shop:
        branch = Branch.objects.filter(shop=shop).first()
        logger.info(f"No branch on profile, fallback branch: {branch}")

    branch_id = request.GET.get("branch")
    if branch_id and shop:
        branch = Branch.objects.filter(id=branch_id, shop=shop).first()
        logger.info(f"Overriding branch via query param -> {branch}")

    logger.info(f"Resolved Shop: {shop}, Branch: {branch}")
    return shop, branch

def forgot_password(request):
    if request.method == "POST":
        email = request.POST.get("email")
        try:
            user = User.objects.get(email=email)
            reset_link = f"http://127.0.0.1:8000/reset-password/?user={user.id}"
            send_mail(
                "TG POS Password Reset",
                f"Click the link to reset your password: {reset_link}",
                "technical@thinkgoldtechnologies.co.zw",
                [email],
                fail_silently=False,
            )
            return JsonResponse({"status": "success"})
        except User.DoesNotExist:
            return JsonResponse({"status": "failed", "message": "Email not found"})
    return render(request, "posApp/forgot_password.html")


from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import permission_required, subscription_required, permissions_for_user
from .forms import UserCreateForm, UserProfileForm
from .models import Role, Subscription, SubscriptionPlan, UserProfile
from .permissions_registry import PERMISSION_GROUPS, normalise


@login_required
def admin_dashboard(request):
    today = timezone.localdate()
    one_day_ago = timezone.now() - timedelta(days=1)

    total_users = User.objects.count()
    total_subscribers = Subscription.objects.filter(is_active=True).count()
    expired_subscribers = Subscription.objects.filter(end_date__lt=today).count()

    total_shops = Shop.objects.count()
    total_branches = Branch.objects.count()

    location_counts = list(
        UserProfile.objects.values('country')
        .exclude(country__isnull=True)
        .exclude(country__exact='')
        .annotate(total=models.Count('id'))
        .order_by('-total')
    )

    busiest_shop = (
        UserProfile.objects
        .values('shop__id', 'shop__name')
        .exclude(shop__isnull=True)
        .annotate(user_count=models.Count('id'))
        .order_by('-user_count')
        .first()
    )

    # same rules as the two "suspicious" detail pages, so the numbers match
    short_sessions_count = UserActivity.objects.filter(
        activity_type='logout', session_duration__lte=timedelta(minutes=1)
    ).count()

    multiple_ips_count = (
        UserActivity.objects
        .filter(timestamp__gte=one_day_ago)
        .values('user__id')
        .annotate(ip_count=Count('ip_address', distinct=True))
        .filter(ip_count__gt=1)
        .count()
    )

    top_countries = location_counts[:8]

    context = {
        'total_users': total_users,
        'total_subscribers': total_subscribers,
        'expired_subscribers': expired_subscribers,
        'total_shops': total_shops,
        'total_branches': total_branches,
        'location_counts': location_counts,
        'busiest_shop': busiest_shop,
        'short_sessions_count': short_sessions_count,
        'multiple_ips_count': multiple_ips_count,
        'country_labels': json.dumps([c['country'] for c in top_countries]),
        'country_totals': json.dumps([c['total'] for c in top_countries]),
    }
    return render(request, 'posApp/admin_dashboard.html', context)


def todays_logins(request):
    filter_option = request.GET.get('filter', 'today')
    now = timezone.now()

    # Default values
    users_today = User.objects.none()

    # TODAY
    if filter_option == 'today':
        start = now.replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=1)
        users_today = User.objects.filter(last_login__range=(start, end))

    # YESTERDAY
    elif filter_option == 'yesterday':
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)
        end = start + timedelta(days=1)
        users_today = User.objects.filter(last_login__range=(start, end))

    # THIS WEEK
    elif filter_option == 'week':
        start = now - timedelta(days=now.weekday())  # Monday
        users_today = User.objects.filter(last_login__date__gte=start.date())

    # THIS MONTH
    elif filter_option == 'month':
        start = now.replace(day=1)
        users_today = User.objects.filter(last_login__date__gte=start.date())

    # 🆕 LAST MONTH
    elif filter_option == 'last_month':
        first_day_this_month = now.replace(day=1)
        last_month_end = first_day_this_month - timedelta(days=1)
        start = last_month_end.replace(day=1)
        end = last_month_end.replace(day=calendar.monthrange(last_month_end.year, last_month_end.month)[1])
        users_today = User.objects.filter(last_login__date__range=(start.date(), end.date()))

    # 🆕 LAST 3 MONTHS
    elif filter_option == 'last_3_months':
        three_months_ago = now - timedelta(days=90)
        users_today = User.objects.filter(last_login__date__gte=three_months_ago.date())

    # THIS YEAR
    elif filter_option == 'year':
        start = now.replace(month=1, day=1)
        users_today = User.objects.filter(last_login__date__gte=start.date())

    # 🧮 Chart Data (last 7 days)
    last_7_days = [now.date() - timedelta(days=i) for i in range(6, -1, -1)]
    labels = [d.strftime("%b %d") for d in last_7_days]
    data = [User.objects.filter(last_login__date=d).count() for d in last_7_days]

    context = {
        'users_today': users_today.order_by('-last_login'),
        'today': now.date(),
        'active_filter': filter_option,
        'login_chart_labels': labels,
        
        'login_chart_data': data,
    }

    return render(request, 'posApp/todays_logins.html', context)

def suspicious_short_sessions_view(request):
    users = UserActivity.objects.filter(activity_type='logout', session_duration__lte=timedelta(minutes=1))
    return render(request, 'posApp/suspicious_short_sessions.html', {'users': users})

def suspicious_multiple_ips_view(request):
    one_day_ago = timezone.now() - timedelta(days=1)

    # Query users with multiple IPs in the last day
    users = (
        UserActivity.objects
        .filter(timestamp__gte=one_day_ago)
        .values('user__id', 'user__username')  # fields for grouping
        .annotate(
            ip_count=Count('ip_address', distinct=True),
            last_login=Max('timestamp')
        )
        .filter(ip_count__gt=1)
        .order_by('-ip_count', '-last_login')  # order by aggregated fields
    )

    context = {
        'users': users,
    }
    return render(request, 'posApp/suspicious_multiple_ips.html', context)


@login_required
@permission_required('user_delete')
@subscription_required
def user_delete(request, user_id):
    profile = get_object_or_404(UserProfile, user_id=user_id, shop=request.user.userprofile.shop)
    if profile.user_id == request.user.id or (profile.user.is_superuser and not request.user.is_superuser):
        raise PermissionDenied
    if request.method == "POST":
        profile.user.delete()
        return redirect('user-list')
    return render(request, "posApp/user_confirm_delete.html", {"profile": profile})

from itertools import cycle

@login_required
def home_api(request):
    today = localtime().date()

    transactions = Transaction.objects.all()
    transaction_count = transactions.count()
    total_sales = transactions.aggregate(total=Sum('grand_total'))['total'] or 0
    daily_sales = transactions.filter(date_added__date=today).aggregate(total=Sum('grand_total'))['total'] or 0
    yesterday = today - timedelta(days=1)
    yesterday_sales = transactions.filter(date_added__date=yesterday).aggregate(total=Sum('grand_total'))['total'] or 0
    total_qty = SalesItem.objects.filter(transaction__in=transactions).aggregate(total_qty=Sum('qty'))['total_qty'] or 0

    top_product = (
        SalesItem.objects.filter(transaction__in=transactions)
        .select_related('product')
        .values('product__name')
        .annotate(total_qty=Sum('qty'))
        .order_by('-total_qty')
        .first()
    )
    top_product_name = top_product['product__name'] if top_product else "No Sales"
    top_product_qty = top_product['total_qty'] if top_product else 0

    sold_items = SalesItem.objects.filter(transaction__in=transactions).select_related('product')
    actual_profit = 0
    total_loss = 0
    product_profits = {}

    for item in sold_items:
        diff = (item.product.price - item.product.cprice) * item.qty
        if diff >= 0:
            actual_profit += diff
        else:
            total_loss += abs(diff)
        product_profits[item.product.name] = product_profits.get(item.product.name, 0) + diff

    profitable = {k: v for k, v in product_profits.items() if v > 0}
    top_profit_product = max(profitable, key=profitable.get) if profitable else None
    top_profit_value = profitable[top_profit_product] if top_profit_product else 0

    losses = {k: v for k, v in product_profits.items() if v < 0}
    loss_product = min(losses, key=losses.get) if losses else None
    loss_value = abs(losses[loss_product]) if loss_product else 0

    all_products = Products.objects.all()
    expected_profit = sum([(p.price - p.cprice) * p.stock for p in all_products])
    product_count = all_products.count()
    category_count = Category.objects.count()

    low_stock_products = all_products.filter(stock__lt=20)
    low_stock_list = [{"name": p.name, "stock": p.stock} for p in low_stock_products]

    data = {
        'today': str(today),
        'categories': category_count,
        'products': product_count,
        'transactions': transaction_count,
        'total_sales': float(total_sales),
        'quantity': total_qty,
        'daily_sales': float(daily_sales),
        'yesterday_sales': float(yesterday_sales),
        'top_product_name': top_product_name,
        'top_product_qty': top_product_qty,
        'expected_profit': expected_profit,
        'actual_profit': actual_profit,
        'total_loss': total_loss,
        'top_profit_product': top_profit_product,
        'top_profit_value': top_profit_value,
        'loss_product': loss_product,
        'loss_value': loss_value,
        'low_stock_count': low_stock_products.count(),
        'low_stock_products': low_stock_list
        
    }

    return JsonResponse(data)

CURRENCY_STYLES = {
    "USD": ("c-blue",   "#00b8e6"),
    "ZIG": ("c-orange", "#c65610"),
    "ZWG": ("c-orange", "#981bd7"),
    "ZAR": ("c-lime",   "#6fd62f"),
    "ZWL": ("c-red",    "#ff3e1d"),
    "EUR": ("c-violet", "#5b5fc7"),
}
FALLBACK_STYLES = [
    ("c-violet", "#5b5fc7"), ("c-red", "#ff3e1d"),
    ("c-lime", "#6fd62f"), ("c-orange", "#ffab00"),
]


def _pct_change(today_val, yesterday_val):
    t = float(today_val or 0)
    y = float(yesterday_val or 0)
    if y > 0:
        return (t - y) / y * 100
    return 100.0 if t > 0 else 0.0


@login_required
def home(request):
    from .models import Expense, IncomeEntry          # needed for the net-profit block

    if "view_dashboard" not in get_user_permissions(request):
        return redirect("pos-page")
    today = localtime().date()
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    shop, user_branch = get_user_shop_and_branch(request)

    if not request.user.is_authenticated:
        return redirect("login")

    if not shop:
        messages.error(request, "Your account is missing business details. Please complete your profile.")
        return redirect('login')

    branch = user_branch
    selected_branch_id = None
    if request.user.is_superuser:
        selected_branch_id = request.GET.get("branch_id")
        if selected_branch_id:
            branch = Branch.objects.filter(id=selected_branch_id, shop=shop).first()

    transaction_filter = {"shop": shop}
    if branch:
        transaction_filter["branch"] = branch
    if start_date and end_date:
        transaction_filter["date_added__date__range"] = [start_date, end_date]

    transactions = Transaction.objects.filter(**transaction_filter)
    transaction_count = transactions.count()

    usd_amount = Case(
        When(grand_total_base__gt=0, then=F("grand_total_base")),
        default=F("grand_total"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    total_sales = transactions.aggregate(total=Sum(usd_amount))["total"] or 0
    total_qty = SalesItem.objects.filter(transaction__in=transactions).aggregate(total_qty=Sum("qty"))["total_qty"] or 0

    daily_sales = transactions.filter(date_added__date=today).aggregate(total=Sum(usd_amount))["total"] or 0
    yesterday = today - timedelta(days=1)
    yesterday_sales = transactions.filter(date_added__date=yesterday).aggregate(total=Sum(usd_amount))["total"] or 0

    top_product = (
        SalesItem.objects.filter(transaction__in=transactions)
        .values("product__name")
        .annotate(total_qty=Sum("qty"))
        .order_by("-total_qty")
        .first()
    )
    top_product_name = top_product["product__name"] if top_product else "No Sales"
    top_product_qty = top_product["total_qty"] if top_product else 0

    # ---------- profit on sold products ----------
    sold_items = SalesItem.objects.filter(transaction__in=transactions).select_related("product")
    actual_profit, total_loss = 0, 0
    product_profits = {}
    for item in sold_items:
        diff = (item.product.price - item.product.cprice) * item.qty
        if diff >= 0:
            actual_profit += diff
        else:
            total_loss += abs(diff)
        product_profits[item.product.name] = product_profits.get(item.product.name, 0) + diff

    profitable = {k: v for k, v in product_profits.items() if v > 0}
    top_profit_product = max(profitable, key=profitable.get) if profitable else None
    top_profit_value = profitable[top_profit_product] if top_profit_product else 0

    losses = {k: v for k, v in product_profits.items() if v < 0}
    loss_product = min(losses, key=losses.get) if losses else None
    loss_value = losses[loss_product] if loss_product else 0

    # ---------- Profit after ALL expenses (fixed + variable + shift expenses) ----------
    expense_filter = {"shop": shop}          # expenses logged from the POS ("Add Expense")
    cost_filter = {"shop": shop}             # fixed / variable costs typed into the income statement
    if branch:
        expense_filter["branch"] = branch
        cost_filter["branch"] = branch
    if start_date and end_date:
        expense_filter["created_at__date__range"] = [start_date, end_date]
        cost_filter["date__range"] = [start_date, end_date]

    shift_expenses = Expense.objects.filter(**expense_filter).aggregate(t=Sum("amount_usd"))["t"] or 0

    cost_entries = IncomeEntry.objects.filter(**cost_filter)
    fixed_costs = cost_entries.filter(entry_type="fixed").aggregate(t=Sum("amount_usd"))["t"] or 0
    variable_costs = cost_entries.filter(entry_type="variable").aggregate(t=Sum("amount_usd"))["t"] or 0

    gross_profit = actual_profit - total_loss            # profit on sold products, losses taken off
    total_expenses = shift_expenses + fixed_costs + variable_costs
    net_profit = gross_profit - total_expenses
    net_margin = (net_profit / total_sales * 100) if total_sales else 0

    # ---------- categories / products ----------
    category_filter = {"shop": shop}
    if branch:
        category_filter["branch"] = branch
    category_count = Category.objects.filter(**category_filter).count()

    product_filter = {"shop": shop}
    if branch:
        product_filter["branch"] = branch
    all_products = Products.objects.filter(**product_filter)
    expected_profit = sum([(p.price - p.cprice) * p.stock for p in all_products])
    product_count = all_products.count()

    # ---------- monthly ----------
    monthly_data = (
        transactions.annotate(month=TruncMonth("date_added"))
        .values("month")
        .annotate(total=Sum(usd_amount))
        .order_by("-month")[:12]
    )
    monthly_data = list(monthly_data)[::-1]
    monthly_labels = [m["month"].strftime("%b %Y") for m in monthly_data]
    monthly_sales = [float(m["total"]) for m in monthly_data]

    highest_sales_month, highest_sales_amount = None, 0
    if monthly_data:
        best = max(monthly_data, key=lambda x: x["total"])
        highest_sales_month = best["month"].strftime("%B %Y")
        highest_sales_amount = float(best["total"])

    # ---------- per currency ----------
    def code(v):
        return (v or "USD").upper()

    totals = defaultdict(lambda: {"total": Decimal("0"), "usd": Decimal("0"), "count": 0})
    for r in (transactions.values("currency")
              .annotate(total=Sum("grand_total"), usd=Sum(usd_amount), count=Count("id"))):
        t = totals[code(r["currency"])]
        t["total"] += r["total"] or 0
        t["usd"] += r["usd"] or 0
        t["count"] += r["count"]

    today_by_cur = defaultdict(Decimal)
    for r in (transactions.filter(date_added__date=today)
              .values("currency").annotate(total=Sum("grand_total"))):
        today_by_cur[code(r["currency"])] += r["total"] or 0

    yesterday_by_cur = defaultdict(Decimal)
    for r in (transactions.filter(date_added__date=yesterday)
              .values("currency").annotate(total=Sum("grand_total"))):
        yesterday_by_cur[code(r["currency"])] += r["total"] or 0

    monthly_rows = list(
        transactions.annotate(month=TruncMonth("date_added"))
        .values("month", "currency")
        .annotate(amount=Sum("grand_total"), usd=Sum(usd_amount))
        .order_by("month")
    )

    rate_filter = {"shop": shop, "is_active": True}
    if branch:
        rate_filter["branch"] = branch
    active_rates = set(ExchangeRate.objects.filter(**rate_filter).values_list("currency", flat=True))
    codes = ["USD"] + sorted(({c.upper() for c in active_rates} | set(totals.keys())) - {"USD"})

    fallback_iter = cycle(FALLBACK_STYLES)
    styles = {}
    for c in codes:
        styles[c] = CURRENCY_STYLES.get(c) or next(fallback_iter)

    months = sorted({r["month"] for r in monthly_rows})[-12:]
    month_index = {m: i for i, m in enumerate(months)}
    series = {c: {"amount": [0.0] * len(months), "usd": [0.0] * len(months)} for c in codes}
    for r in monthly_rows:
        i = month_index.get(r["month"])
        if i is None:
            continue
        s = series[code(r["currency"])]
        s["amount"][i] += float(r["amount"] or 0)
        s["usd"][i] += float(r["usd"] or 0)

    monthly_currency_data = {
        "labels": [m.strftime("%b %Y") for m in months],
        "series": series,
        "colors": {c: styles[c][1] for c in codes},
    }

    top_month_by_cur = {}
    for r in monthly_rows:
        c = code(r["currency"])
        amt = r["amount"] or 0
        if c not in top_month_by_cur or amt > top_month_by_cur[c][1]:
            top_month_by_cur[c] = (r["month"], amt)

    currency_sales = []
    for c in codes:
        tm = top_month_by_cur.get(c)
        currency_sales.append({
            "currency": c,
            "cls": styles[c][0],
            "color": styles[c][1],
            "total": totals[c]["total"] if c in totals else Decimal("0"),
            "usd_value": totals[c]["usd"] if c in totals else Decimal("0"),
            "count": totals[c]["count"] if c in totals else 0,
            "today": today_by_cur.get(c, Decimal("0")),
            "yesterday": yesterday_by_cur.get(c, Decimal("0")),
            "growth": _pct_change(today_by_cur.get(c, 0), yesterday_by_cur.get(c, 0)),
            "top_month": tm[0].strftime("%B %Y") if tm else None,
            "top_month_amount": tm[1] if tm else Decimal("0"),
        })

    sales_growth = 0
    if yesterday_sales > 0:
        sales_growth = ((daily_sales - yesterday_sales) / yesterday_sales) * 100
    elif daily_sales > 0:
        sales_growth = 100

    product_data = (
        SalesItem.objects.filter(transaction__in=transactions)
        .values("product__name")
        .annotate(total=Sum("qty"))
        .order_by("-total")
    )
    product_labels = [p["product__name"] for p in product_data]
    product_sales = [int(p["total"]) for p in product_data]

    low_stock_filter = {"shop": shop, "stock__lt": 20}
    if branch:
        low_stock_filter["branch"] = branch
    low_stock_products = Products.objects.filter(**low_stock_filter)

    # ---------- context ----------
    context = {
        "today": today,
        "categories": category_count,
        "products": product_count,
        "transaction": transaction_count,
        "total_sales": total_sales,
        "quantity": total_qty,
        "top_product_name": top_product_name,
        "top_product_qty": top_product_qty,
        "expected_profit": expected_profit,
        "actual_profit": actual_profit,
        "total_loss": total_loss,
        "gross_profit": gross_profit,
        "shift_expenses": shift_expenses,
        "fixed_costs": fixed_costs,
        "variable_costs": variable_costs,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "net_margin": net_margin,
        "shop_name": shop.name,
        "branch_name": branch.name if branch else (user_branch.name if user_branch else None),
        "branch": branch or user_branch,
        "top_profit_product": top_profit_product,
        "top_profit_value": top_profit_value,
        "loss_product": loss_product,
        "loss_value": abs(loss_value),
        "monthly_labels": json.dumps(monthly_labels),
        "monthly_sales": json.dumps(monthly_sales),
        "monthly_currency_data": json.dumps(monthly_currency_data),
        "product_labels": json.dumps(product_labels),
        "product_sales": json.dumps(product_sales),
        "highest_sales_month": highest_sales_month,
        "highest_sales_amount": highest_sales_amount,
        "currency_sales": currency_sales,
        "daily_sales": daily_sales,
        "yesterday_sales": yesterday_sales,
        "sales_growth": sales_growth,
        "low_stock_count": low_stock_products.count(),
        "low_stock_products": low_stock_products,
        "start_date": start_date,
        "end_date": end_date,
        "selected_branch_id": int(selected_branch_id) if selected_branch_id else None,
    }

    if request.user.is_superuser:
        context["all_branches"] = Branch.objects.filter(shop=shop)

    return render(request, "posApp/home.html", context)

@login_required
@permission_required('view_products')
def stock_attention_view(request):
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return render(request, 'posApp/stock_attention.html', {'error': 'No shop associated with this user.'})

    # Start with all products that have stock
    products = Products.objects.filter(stock__gt=0, shop=shop)
    selected_branch = user_branch

    # Filter by branch if not superuser
    if user_branch and not request.user.is_superuser:
        products = products.filter(branch=user_branch)
    
    # Superuser can filter by any branch
    branches = None
    if request.user.is_superuser:
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch")
        if branch_id:
            selected_branch = branches.filter(id=branch_id).first()
            if selected_branch:
                products = products.filter(branch=selected_branch)

    # Prepare a list to store product info with days_since_sale
    product_data = []

    for product in products:
        # Get the last sale of this product
        last_sale_item = SalesItem.objects.filter(product=product).order_by('-product__date_updated').first()
        if last_sale_item:
            days_since_sale = (now() - last_sale_item.transaction.date_added).days
            last_sale_date = last_sale_item.transaction.date_added
        else:
            days_since_sale = 999  
            last_sale_date = None

        
        if days_since_sale > 30:
            status = "Dead Stock"
        elif days_since_sale > 14:
            status = "Very Slow"
        elif days_since_sale > 7:
            status = "Slow Moving"
        else:
            status = "Active"

        if status != "Active":
            product_data.append({
                'id': product.id,
                'name': product.name,
                'stock': product.stock,
                'last_sale_date': last_sale_date,
                'days_since_sale': days_since_sale,
                'status': status
            })

    product_data = sorted(product_data, key=lambda x: x['days_since_sale'], reverse=True)

    
    top_products = product_data[:20]

    # Count the statuses
    dead_count = sum(1 for p in product_data if p['status'] == 'Dead Stock')
    very_slow_count = sum(1 for p in product_data if p['status'] == 'Very Slow')
    slow_count = sum(1 for p in product_data if p['status'] == 'Slow Moving')

    context = {
        'products': top_products,
        'dead_count': dead_count,
        'very_slow_count': very_slow_count,
        'slow_count': slow_count,
        'branches': branches,
        'selected_branch': selected_branch,
    }

    return render(request, 'posApp/stock_attention.html', context)

from django.db.models import Sum

def sales_notifications(request):
    total_sales = SalesItem.objects.filter(product__date_added__datet=datetime.now().date()
                                           ).aaggregate(total =Sum('total'))['total'] or 0
    
    notification = None
    if total_sales >= 100:
        notification = "Daily sales of $100 reached!"

    return {'sales_notification': notification}

@login_required
def monthlysales(request):
    current_year = now().year
    selected_year = int(request.GET.get("year", current_year))
    years = range(current_year - 9, current_year + 1)  # last 10 years

    # 1. Calculate profit per line item: (selling_price - cost_price) * qty
    profit_expression = ExpressionWrapper(
        (F('price') - F('product__cprice')) * F('qty'),
        output_field=FloatField()
    )

    qs = SalesItem.objects.filter(
        transaction__date_added__year=selected_year,
        type="sale"
    ).annotate(
        profit=profit_expression  # <-- add profit per item
    )

    
    sales = (
        qs.annotate(month=TruncMonth("transaction__date_added"))
          .values("month", "product__name", "product__id")
          .annotate(
              total_sold=Sum("qty"),
              total_profit=Sum("profit")  
          )
          .order_by("month", "-total_profit")  
    )

    monthly_top_products = defaultdict(list)
    for item in sales:
        if len(monthly_top_products[item["month"]]) < 10:
            monthly_top_products[item["month"]].append(item)

    context = {
        "monthly_top_products": dict(monthly_top_products),
        "selected_year": selected_year,
        "years": years,
    }

    return render(request, "posApp/monthlysales.html", context)


@login_required
def about(request):
    shop, _ = get_user_shop_and_branch(request)
    context = {
        'page_title': 'About',
        'shop_name': shop.name if shop else "No Shop",
    }
    return render(request, 'posApp/about.html', context)

@login_required
@permission_required('view_categories')
def category(request):
    shop, user_branch = get_user_shop_and_branch(request)

    if not shop:
        return render(request, 'posApp/category.html', {'error': 'No shop associated with this user.'})

    categories = Category.objects.filter(shop=shop)

    selected_branch = user_branch

    if user_branch and not request.user.is_superuser:
        categories = categories.filter(branch=user_branch)

    branches = None
    if request.user.is_superuser:
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch")
        if branch_id:
            selected_branch = branches.filter(id=branch_id).first()
            if selected_branch:
                categories = categories.filter(branch=selected_branch)

    context = {
        'page_title': 'Category List',
        'category': categories,
        'shop_name': shop.name,
        'branches': branches,
        'selected_branch': selected_branch,
    }

    return render(request, 'posApp/category.html', context)

@csrf_exempt
def category1(request):
    if request.method == "GET":
        # List all categories
        categories = Category.objects.all()
        categories_list = [{"id": c.id, "name": c.name} for c in categories]
        return JsonResponse({"status": "success", "categories": categories_list})

    elif request.method == "POST":
        # Create a new category
        try:
            data = json.loads(request.body)
            name = data.get("name")
            if not name:
                return JsonResponse({"status": "error", "msg": "Name is required"})
            category = Category.objects.create(name=name)
            return JsonResponse({"status": "success", "id": category.id, "name": category.name})
        except Exception as e:
            return JsonResponse({"status": "error", "msg": str(e)})

    elif request.method == "PUT":
        # Update a category
        try:
            data = json.loads(request.body)
            category_id = data.get("id")
            name = data.get("name")
            if not category_id or not name:
                return JsonResponse({"status": "error", "msg": "ID and name are required"})
            category = Category.objects.filter(id=category_id).first()
            if not category:
                return JsonResponse({"status": "error", "msg": "Category not found"})
            category.name = name
            category.save()
            return JsonResponse({"status": "success", "id": category.id, "name": category.name})
        except Exception as e:
            return JsonResponse({"status": "error", "msg": str(e)})

    elif request.method == "DELETE":
        # Delete category
        try:
            data = json.loads(request.body)
            category_id = data.get("id")
            if not category_id:
                return JsonResponse({"status": "error", "msg": "ID is required"})
            Category.objects.filter(id=category_id).delete()
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "msg": str(e)})


@csrf_exempt
def products_api(request):
    if request.method == "GET":
        qs = Products.objects.all()
        product_list = [
            {
                "id": p.id,
                "name": p.name,
                "stock": p.stock,
                "price": float(p.price),
                "cprice": float(p.cprice),
                "category_id": p.category.id if p.category else None,
                "category_name": p.category.name if p.category else "Uncategorized",
                "branch": p.branch.name if p.branch else None,
                "status": p.status
            } for p in qs
        ]
        return JsonResponse({"status": "success", "products": product_list})

    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            name = data.get("name")
            stock = data.get("stock", 0)
            price = data.get("price", 0.0)
            cprice = data.get("cprice", 0.0)
            category_id = data.get("category_id")
            category = Category.objects.filter(id=category_id).first() if category_id else None

            product = Products.objects.create(
                name=name, stock=stock, price=price, cprice=cprice, category=category
            )
            return JsonResponse({"status": "success", "id": product.id, "name": product.name})
        except Exception as e:
            return JsonResponse({"status": "error", "msg": str(e)})

    elif request.method == "PUT":
        try:
            data = json.loads(request.body)
            product_id = data.get("id")
            product = Products.objects.filter(id=product_id).first()
            if not product:
                return JsonResponse({"status": "error", "msg": "Product not found"})

            # Update fields
            product.name = data.get("name", product.name)
            product.stock = data.get("stock", product.stock)
            product.price = data.get("price", product.price)
            product.cprice = data.get("cprice", product.cprice)

            category_id = data.get("category_id")
            if category_id:
                category = Category.objects.filter(id=category_id).first()
                product.category = category

            product.status = data.get("status", product.status)
            product.save()
            return JsonResponse({"status": "success", "id": product.id, "name": product.name})
        except Exception as e:
            return JsonResponse({"status": "error", "msg": str(e)})

    elif request.method == "DELETE":
        try:
            data = json.loads(request.body)
            product_id = data.get("id")
            if not product_id:
                return JsonResponse({"status": "error", "msg": "ID is required"})
            Products.objects.filter(id=product_id).delete()
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "msg": str(e)})

from django.db import transaction
from django.core.exceptions import ValidationError

class TransferError(Exception):
    """Raised inside the transfer transaction to roll everything back."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@login_required
@subscription_required
@permission_required('transfer_product')
def transfer_product(request):
    user = request.user

    try:
        profile = UserProfile.objects.select_related("shop", "branch").get(user=user)
        shop = profile.shop
        user_branch = profile.branch
    except UserProfile.DoesNotExist:
        if user.is_superuser:
            shop = Shop.objects.first()
            user_branch = Branch.objects.filter(shop=shop).first() if shop else None
            profile = UserProfile.objects.create(user=user, shop=shop, branch=user_branch)
        else:
            return render(request, "posApp/transfer_product.html", {
                "error": "⚠️ No profile found. Contact admin to assign you a shop and branch."
            })

    if not shop or not user_branch:
        return render(request, "posApp/transfer_product.html", {
            "error": "⚠️ No shop or branch assigned to your profile. Contact admin."
        })

    # --- POST: Perform transfer (one or many products, each to any branch) ---
    if request.method == "POST":
        product_ids = request.POST.getlist("product_id[]") or request.POST.getlist("product_id")
        dest_ids    = request.POST.getlist("destination_branch_id[]") or request.POST.getlist("destination_branch_id")
        quantities  = request.POST.getlist("quantity[]") or request.POST.getlist("quantity")

        # A single destination can be sent for the whole list
        if len(dest_ids) == 1 and len(product_ids) > 1:
            dest_ids = dest_ids * len(product_ids)

        if not product_ids:
            return JsonResponse({"status": "error", "message": "Add at least one product to transfer."}, status=400)
        if not (len(product_ids) == len(dest_ids) == len(quantities)):
            return JsonResponse({"status": "error", "message": "Every row needs a product, a branch and a quantity."}, status=400)

        # Parse rows and merge duplicates (same product -> same branch)
        merged = {}
        for pid, did, qty in zip(product_ids, dest_ids, quantities):
            try:
                pid = int(pid)
                did = int(did)
                qty = int(qty)
                if qty <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return JsonResponse({"status": "error",
                                     "message": "Every row needs a product, a branch and a quantity above 0."},
                                    status=400)
            merged[(pid, did)] = merged.get((pid, did), 0) + qty

        rows = [(pid, did, qty) for (pid, did), qty in merged.items()]

        try:
            with transaction.atomic():
                try:
                    live_shop = Shop.objects.get(pk=shop.pk)
                except Shop.DoesNotExist:
                    raise TransferError("Your shop no longer exists.", 400)

                try:
                    live_branch = Branch.objects.get(pk=user_branch.pk, shop=live_shop)
                except Branch.DoesNotExist:
                    raise TransferError("Your branch no longer exists.", 400)

                # Lock every source product once
                source_products = {
                    p.id: p for p in Products.objects.select_for_update().filter(
                        id__in={r[0] for r in rows}, branch=live_branch, shop=live_shop
                    )
                }

                # Destination branches
                dest_branches = {
                    b.id: b for b in Branch.objects.filter(
                        id__in={r[1] for r in rows}, shop=live_shop
                    )
                }

                # Validate everything before touching any stock
                needed = {}
                for pid, did, qty in rows:
                    product = source_products.get(pid)
                    if not product:
                        raise TransferError("A selected product was not found in your branch.", 404)
                    dest = dest_branches.get(did)
                    if not dest:
                        raise TransferError("A destination branch was not found.", 404)
                    if dest.id == live_branch.id:
                        raise TransferError("Cannot transfer to the same branch.", 400)
                    needed[pid] = needed.get(pid, 0) + qty

                for pid, total_qty in needed.items():
                    product = source_products[pid]
                    if product.stock < total_qty:
                        raise TransferError(
                            f"Not enough stock for {product.name}. "
                            f"Available: {product.stock}, requested: {total_qty}", 400)

                # Do the transfers
                summary = []
                for pid, did, qty in rows:
                    product = source_products[pid]
                    destination_branch = dest_branches[did]

                    product.stock -= qty
                    product.save(update_fields=["stock"])

                    dest_product, created = Products.objects.get_or_create(
                        name=product.name,
                        shop=live_shop,
                        branch=destination_branch,
                        defaults={
                            "category":      product.category,
                            "stock":         0,
                            "code":          f"{product.code}-{destination_branch.pk}",
                            "description":   product.description,
                            "status":        True,
                            "price":         product.price,
                            "cprice":        product.cprice,
                            "supplier":      product.supplier,
                            "tax_type":      product.tax_type,
                            "inclusive_tax": product.inclusive_tax,
                            "exclusive_tax": product.exclusive_tax,
                            "unit":          product.unit,
                        }
                    )
                    dest_product.stock += qty
                    dest_product.save(update_fields=["stock"])

                    ProductTransfer.objects.create(
                        product=product,
                        from_branch=live_branch,
                        to_branch=destination_branch,
                        quantity=qty,
                        created_by=user,
                    )
                    summary.append(f"{qty} x {product.name} -> {destination_branch.name}")

        except TransferError as e:
            return JsonResponse({"status": "error", "message": e.message}, status=e.status)
        except Exception as e:
            logger.exception(f"Transfer failed for user {user}: {e}")
            return JsonResponse({"status": "error",
                                  "message": f"Transfer failed: {str(e)}"}, status=500)

        logger.info(f"User {user} transferred from {live_branch.name}: " + "; ".join(summary))
        return JsonResponse({
            "status":  "success",
            "message": f"Successfully transferred {len(summary)} item(s): " + "; ".join(summary) + ".",
        })

    # --- GET: Render form ---
    products = Products.objects.filter(shop=shop, branch=user_branch, status=True, stock__gt=0)
    branches = Branch.objects.filter(shop=shop).exclude(id=user_branch.id)

    context = {
        "products":   products if products.exists() else None,
        "branches":   branches if branches.exists() else None,
        "page_title": "Transfer Product",
    }
    if not products.exists():
        context["error"] = "No products with stock found in your branch."
    elif not branches.exists():
        context["error"] = "No other branches available in this shop."

    return render(request, "posApp/transfer_product.html", context)

from django.db.models import Q, Sum

@login_required
@permission_required('view_reports')
def product_transfer_report(request):
    try:
        profile = UserProfile.objects.select_related("shop", "branch").get(user=request.user)
    except UserProfile.DoesNotExist:
        return render(request, "posApp/product_transfer_report.html", {
            "rows": [], "error": "No profile found. Contact admin to assign you a shop and branch.",
        })

    branch = profile.branch
    view_type = request.GET.get("type", "all")   # all | sent | received

    qs = ProductTransfer.objects.select_related(
        "product", "from_branch", "to_branch", "created_by"
    ).filter(from_branch__shop=profile.shop)

    if view_type == "sent":
        qs = qs.filter(from_branch=branch)
    elif view_type == "received":
        qs = qs.filter(to_branch=branch)
    else:
        view_type = "all"
        qs = qs.filter(Q(from_branch=branch) | Q(to_branch=branch))

    qs = qs.order_by("-id")

    # Find the date field on ProductTransfer, whatever it is called
    field_names = [f.name for f in ProductTransfer._meta.get_fields()]
    date_field = next(
        (n for n in ("created_at", "date", "date_created", "date_added", "timestamp", "created")
         if n in field_names),
        None,
    )

    rows = []
    for t in qs:
        rows.append({
            "product": t.product.name,
            "from_branch": t.from_branch.name,
            "to_branch": t.to_branch.name,
            "quantity": t.quantity,
            "direction": "Sent" if t.from_branch_id == branch.id else "Received",
            "by": t.created_by.get_username() if t.created_by else "-",
            "date": getattr(t, date_field) if date_field else None,
        })

    total_sent = ProductTransfer.objects.filter(from_branch=branch).aggregate(s=Sum("quantity"))["s"] or 0
    total_received = ProductTransfer.objects.filter(to_branch=branch).aggregate(s=Sum("quantity"))["s"] or 0

    return render(request, "posApp/product_transfer_report.html", {
        "rows": rows,
        "branch": branch,
        "view_type": view_type,
        "total_sent": total_sent,
        "total_received": total_received,
        "has_date": date_field is not None,
    })

@login_required
@permission_required('view_categories')
@subscription_required
def manage_category(request):
    shop, user_branch = get_user_shop_and_branch(request)
    category = None
    selected_branch = user_branch
    branches = None

    if not shop:
        return render(request, 'posApp/manage_category.html', {
            'error': 'No shop associated with this user.'
        })

    
    if request.user.is_superuser:
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch_id")
        if branch_id:
            selected_branch = branches.filter(id=branch_id).first()

    if request.method == 'GET':
        category_id = request.GET.get('id')
        if category_id and category_id.isnumeric():
            qs = Category.objects.filter(id=category_id, shop=shop)

            
            if selected_branch:
                qs = qs.filter(branch=selected_branch)

            category = qs.first()

    context = {
        'category': category,
        'shop_name': shop.name,
        'branches': branches,            
        'selected_branch': selected_branch,
    }
    return render(request, 'posApp/manage_category.html', context)

@login_required
@permission_required('view_categories')
@subscription_required
def save_category(request):
    if request.method == "POST":
        resp = {'status': 'failed'}
        try:
            shop, user_branch = get_user_shop_and_branch(request)
            if not shop:
                resp['msg'] = "Shop not found for this user"
                return JsonResponse(resp)

            category_id = request.POST.get('id')
            name = request.POST.get('name')
            description = request.POST.get('description', '')
            status = request.POST.get('status', '1') == '1'

            # --- Branch handling ---
            branch = user_branch
            if request.user.is_superuser:
                branch_id = request.POST.get("branch")
                if branch_id:
                    branch = Branch.objects.filter(id=branch_id, shop=shop).first()

            if not branch:
                resp['msg'] = "Branch must be selected"
                return JsonResponse(resp)

            if not name:
                resp['msg'] = "Category name is required"
                return JsonResponse(resp)

            # --- Duplicate check ---
            duplicate = Category.objects.filter(shop=shop, branch=branch, name=name)
            if category_id:
                duplicate = duplicate.exclude(id=category_id)
            if duplicate.exists():
                return JsonResponse({
                    "status": "failed",
                    "msg": f"Category '{name}' already exists in branch {branch.name}"
                })

            # --- Update existing category ---
            if category_id and category_id.isnumeric():
                category = Category.objects.filter(id=category_id, shop=shop).first()
                if not category:
                    resp['msg'] = "Category not found"
                    return JsonResponse(resp)

                category.name = name
                category.description = description
                category.status = status
                category.branch = branch  # always assign branch explicitly
                category.save(update_fields=['name', 'description', 'status', 'branch'])

                resp['status'] = 'success'
                resp['msg'] = 'Category updated successfully'

            # --- Create new category ---
            else:
                Category.objects.create(
                    shop=shop,
                    branch=branch,
                    name=name,
                    description=description,
                    status=status
                )
                resp['status'] = 'success'
                resp['msg'] = 'Category created successfully'

        except Exception as e:
            resp['msg'] = str(e)

        return JsonResponse(resp)

    return JsonResponse({'status': 'failed', 'msg': 'Invalid request'})

from django.views.decorators.http import require_http_methods
@login_required
@csrf_exempt   
@require_http_methods(["POST"])
def save_categry(request):
    resp = {'status': 'failed'}

    try:
        shop, branch = get_user_shop_and_branch(request)
        if not shop:
            resp['msg'] = "Shop not found for this user"
            return JsonResponse(resp, status=400)

        # Handle JSON or Form data
        if request.content_type == "application/json":
            data = json.loads(request.body.decode("utf-8"))
        else:
            data = request.POST

        category_id = data.get('id')
        name = data.get('name')
        description = data.get('description', '')
        status = data.get('status', '1')

        if not name:
            resp['msg'] = "Category name is required"
            return JsonResponse(resp, status=400)

        if category_id and str(category_id).isnumeric():
            category = Category.objects.filter(id=category_id, shop=shop).first()
            if category:
                category.name = name
                category.description = description
                category.status = status
                category.save()
                resp['status'] = 'success'
                resp['msg'] = "Category updated successfully"
                resp['category'] = {
                    "id": category.id,
                    "name": category.name,
                    "description": category.description,
                    "status": category.status
                }
            else:
                resp['msg'] = "Category not found"
                return JsonResponse(resp, status=404)
        else:
            category = Category.objects.create(
                shop=shop,
                name=name,
                description=description,
                status=status
            )
            resp['status'] = 'success'
            resp['msg'] = "Category created successfully"
            resp['category'] = {
                "id": category.id,
                "name": category.name,
                "description": category.description,
                "status": category.status
            }

    except Exception as e:
        resp['msg'] = str(e)
        return JsonResponse(resp, status=500)

    return JsonResponse(resp, status=200)

@login_required
@subscription_required
@permission_required('delete')
def delete_category(request):
    resp = {'status': 'failed', 'msg': ''}
    category_id = request.POST.get('id')

    if not category_id:
        resp['msg'] = "Category ID not provided"
        return JsonResponse(resp)

    try:
        shop, branch = get_user_shop_and_branch(request)  # unpack properly
        Category.objects.filter(id=category_id, shop=shop).delete()
        resp['status'] = 'success'
        messages.success(request, 'Category successfully deleted.')
    except Exception as e:
        resp['msg'] = str(e)

    return JsonResponse(resp)

# @login_required
@permission_required('view_products')
@subscription_required
def products(request):
    shop, branch = get_user_shop_and_branch(request)

    if not shop:
        return render(request, 'posApp/products.html', {
            'error': 'No shop associated with this user.'
        })

    
    qs = Products.objects.filter(shop=shop)

    
    if branch and not request.user.is_superuser:
        qs = qs.filter(branch=branch)

    
    selected_branch = branch
    if request.user.is_superuser:
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch")
        if branch_id:
            selected_branch = branches.filter(id=branch_id).first()
            if selected_branch:
                qs = qs.filter(branch=selected_branch)
    else:
        branches = None
        selected_branch = branch

    context = {
        'page_title': 'Product List',
        'products': qs,
        'shop_name': shop.name,
        'branches': branches,            
        'selected_branch': selected_branch,
    }
    return render(request, 'posApp/products.html', context)

    from django.http import JsonResponse


from django.db import transaction, IntegrityError
from django.utils.text import slugify
import pandas as pd

@login_required
@csrf_exempt
@subscription_required
@permission_required('edit_product')
def import_products(request):
    shop, branch = get_user_shop_and_branch(request)
    if not branch:
        messages.error(request, "No branch assigned")
        return redirect("product-page")

    if request.method == "POST" and request.FILES.get("file"):
        excel_file = request.FILES["file"]

        try:
            df = pd.read_excel(excel_file)
            df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
            
            column_aliases = {
                "product": ["product", "product_name", "item", "name"],
                "category": ["category", "group", "type"],
                "stock": ["stock", "quantity", "qty", "amount"],
                "description": ["description", "desc", "details"],
                "code": ["code", "sku", "barcode"] # optional
            }

            found_columns = {}
            for key, aliases in column_aliases.items():
                for alias in aliases:
                    if alias in df.columns:
                        found_columns[key] = alias
                        break

            required = ["product", "category", "stock"]
            missing = [col for col in required if col not in found_columns]
            if missing:
                messages.error(request, f"Missing column(s): {', '.join(missing)}")
                return redirect("product-page")

            count_new = 0
            count_updated = 0

            with transaction.atomic(): 
                for _, row in df.iterrows():
                    product_name = str(row[found_columns["product"]]).strip()
                    if not product_name or product_name.lower() == 'N/A':
                        continue 

                    category_name = str(row[found_columns["category"]]).strip()
                    category, _ = Category.objects.get_or_create(
                        name=category_name,
                        shop=shop,
                        branch=branch
                    )

                    excel_code = str(row[found_columns.get("code", "")]).strip() if "code" in found_columns else ""
                    product_code = excel_code if excel_code and excel_code.lower()!= 'N/A' else slugify(product_name)

                    description = str(row[found_columns.get("description", "")]).strip() if "description" in found_columns else ""
                    stock = row[found_columns["stock"]]
                    stock = 0 if pd.isna(stock) else int(stock)

                    obj, created = Products.objects.update_or_create(
                        name=product_name,
                        branch=branch, 
                        defaults={
                            "shop": shop,
                            "category": category,
                            "stock": stock,
                            "code": product_code,
                            "description": description,
                            "status": True,
                        },
                    )
                    if created:
                        count_new += 1
                    else:
                        count_updated += 1

            messages.success(request, f"Import done: {count_new} new, {count_updated} updated.")

        except IntegrityError as e:
            messages.error(request, f"Duplicate error: {e}. Check for duplicate product names in your Excel.")
        except Exception as e:
            messages.error(request, f"Import failed: {e}")

    return redirect("product-page")



@login_required
@permission_required('manage_products', 'edit_product', any_of=True)
@subscription_required
def manage_products(request):
    shop, user_branch = get_user_shop_and_branch(request)
    
    if not shop:
        return render(request, 'posApp/manage_product.html', {
            'error': 'No shop associated with this user.'
        })

    # Base queryset filtered by shop
    categories = Category.objects.filter(shop=shop, status=1)

    # Superuser can select branch via query param
    selected_branch = user_branch
    if request.user.is_superuser:
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch")
        if branch_id:
            selected_branch = branches.filter(id=branch_id).first()
            if selected_branch:
                categories = categories.filter(branch=selected_branch)
    else:
        branches = None
        if user_branch:
            categories = categories.filter(branch=user_branch)

    # Products filtered by shop and selected branch
    products = Products.objects.filter(shop=shop, status=True)
    if selected_branch:
        products = products.filter(branch=selected_branch)

    # Editing: load the product so the form opens filled with its current values
    product = None
    product_id = request.GET.get("id")
    if product_id and product_id.isdigit():
        product_qs = Products.objects.filter(pk=product_id, shop=shop)
        if not request.user.is_superuser and user_branch:
            product_qs = product_qs.filter(branch=user_branch)
        product = product_qs.first()
        if product and product.category_id:
            # make sure the product's own category is in the list even if it belongs to another branch
            categories = categories | Category.objects.filter(pk=product.category_id, shop=shop)

    context = {
        'page_title': 'Manage Products',
        'product': product,
        'categories': categories,
        'products': products,
        'branches': branches,
        'selected_branch': selected_branch,
        'shop_name': shop.name,
    }
    return render(request, 'posApp/manage_product.html', context)
@login_required
@permission_required('manage_products')
@subscription_required
def save_product(request):
    if request.method == "POST":
        try:
            shop, branch = get_user_shop_and_branch(request)
            if not shop:
                return JsonResponse({"status": "failed", "msg": "No shop found for this user"})

            # --- Gather inputs ---
            product_id = request.POST.get("id")
            name = request.POST.get("name")
            code = request.POST.get("code") or None
            category_id = request.POST.get("category_id")
            unit = request.POST.get("unit")
            description = request.POST.get("description")
            status = request.POST.get("status", "1") == "1"

            def _num(key):
                raw = request.POST.get(key)
                return float(raw) if raw not in (None, "") else None

            # None = the field was not sent, so an edit keeps the current value
            # (editing a product must never reset its prices to 0)
            price = _num("price")
            cost_price = _num("cprice")
            add_stock = int(request.POST.get("add_stock") or 0)

            # Editing never silently moves a product to another branch
            if product_id:
                existing = Products.objects.filter(id=product_id, shop=shop).first()
                if existing and existing.branch_id:
                    branch = existing.branch

            #  Branch handling
            if request.user.is_superuser:
                branch_id = request.POST.get("branch")
                if branch_id:
                    branch = Branch.objects.filter(id=branch_id, shop=shop).first()
            if not branch:
                return JsonResponse({"status": "failed", "msg": "Branch must be selected"})

            # --- Duplicate check ---
            duplicate = Products.objects.filter(shop=shop, branch=branch, name=name)
            if product_id:
                duplicate = duplicate.exclude(id=product_id)
            if duplicate.exists():
                return JsonResponse({
                    "status": "failed",
                    "msg": f"Product '{name}' already exists in branch {branch.name}"
                })

            def record_purchase(product, qty, previous_stock, old_price, old_cprice):
                """Stock that comes in is a purchase: it feeds the income statement."""
                unit_cost = Decimal(str(product.cprice or 0))
                total_cost = (unit_cost * qty).quantize(Decimal("0.01"))

                StockUpdate.objects.create(
                    product=product,
                    user=request.user,
                    old_stock=previous_stock,
                    added_stock=qty,
                    old_price=float(old_price or 0),
                    new_price=float(product.price or 0),
                    old_cprice=float(old_cprice or 0),
                    new_cprice=float(product.cprice or 0),
                    shop=shop,
                    branch=branch,
                    total_cost=total_cost,
                    payment_method="cash",
                )
                StockHistory.objects.create(
                    product=product,
                    previous_stock=previous_stock,
                    quantity_added=qty,
                    quantity_removed=0,
                    new_stock=product.stock,
                    previous_price=old_price or 0,
                    new_price=product.price or 0,
                    previous_cprice=old_cprice or 0,
                    new_cprice=product.cprice or 0,
                    shop=shop,
                    branch=branch,
                    added_by=request.user,
                )

            # --- Update existing product ---
            if product_id:
                product = Products.objects.filter(id=product_id, shop=shop).first()
                if not product:
                    return JsonResponse({"status": "failed", "msg": "Product not found"})

                previous_stock = product.stock
                old_price = product.price
                old_cprice = product.cprice

                with db_transaction.atomic():
                    product.name = name
                    product.code = code
                    product.category_id = category_id
                    product.unit = unit
                    product.description = description
                    product.status = status
                    if price is not None:
                        product.price = price
                    if cost_price is not None:
                        product.cprice = cost_price
                    product.branch = branch

                    if add_stock > 0:
                        product.stock += add_stock

                    product.save()

                    if add_stock > 0:
                        record_purchase(product, add_stock, previous_stock, old_price, old_cprice)

                response = {
                    "status": "success",
                    "msg": "Product updated successfully",
                    "product_id": product.id,
                    "new_stock": product.stock,
                }
                if add_stock > 0 and not product.cprice:
                    response["warning"] = "Cost price is 0, so this stock was recorded as a purchase of 0.00."
                return JsonResponse(response)

            # --- Create new product ---
            else:
                with db_transaction.atomic():
                    product = Products.objects.create(
                        shop=shop,
                        branch=branch,
                        name=name,
                        code=code,
                        category_id=category_id,
                        unit=unit,
                        description=description,
                        status=status,
                        price=price or 0,
                        cprice=cost_price or 0,
                        stock=add_stock
                    )

                    if add_stock > 0:
                        record_purchase(product, add_stock, 0, 0, 0)

                response = {
                    "status": "success",
                    "msg": "Product created successfully",
                    "product_id": product.id,
                    "new_stock": product.stock,
                }
                if add_stock > 0 and not product.cprice:
                    response["warning"] = "Cost price is 0, so this stock was recorded as a purchase of 0.00."
                return JsonResponse(response)

        except Exception as e:
            return JsonResponse({"status": "failed", "msg": str(e)})

    return JsonResponse({"status": "failed", "msg": "Invalid request"})

@login_required
@permission_required('manage_products')
@subscription_required
@require_POST
def delete_product(request, product_id):
    """Delete a product that has no business history.
    A product that was sold, purchased or transferred keeps its records, so it can
    only be made Inactive (deleting it would also delete those sales/purchases)."""
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "No shop found for this user"})

    qs = Products.objects.filter(pk=product_id, shop=shop)
    if not request.user.is_superuser and user_branch:
        qs = qs.filter(branch=user_branch)
    product = qs.first()
    if not product:
        return JsonResponse({"status": "failed", "msg": "Product not found"})

    blockers = []
    sold = SalesItem.objects.filter(product=product).count()
    if sold:
        blockers.append(f"{sold} sale line(s)")
    ordered = PurchaseOrder.objects.filter(product=product).count()
    if ordered:
        blockers.append(f"{ordered} purchase order(s)")
    moved = ProductTransfer.objects.filter(product=product).count()
    if moved:
        blockers.append(f"{moved} branch transfer(s)")

    if blockers:
        return JsonResponse({
            "status": "failed",
            "msg": (f"'{product.name}' cannot be deleted because it has " + ", ".join(blockers)
                    + ". Edit the product and set its status to Inactive instead."),
        })

    name = product.name
    product.delete()   # stock history/counts of this product are removed with it
    return JsonResponse({"status": "success", "msg": f"Product '{name}' deleted."})


@login_required
@subscription_required
def stock_modal_form(request):
    shop, user_branch = get_user_shop_and_branch(request)
    product_id = request.GET.get("id")

    # Determine branch
    selected_branch = user_branch
    if request.user.is_superuser:
        branch_id = request.GET.get("branch_id")
        if branch_id:
            selected_branch = Branch.objects.filter(id=branch_id, shop=shop).first()

    if not shop or not selected_branch:
        return JsonResponse({"status": "failed", "msg": "❌ Shop or branch not found."}, status=400)

    # Filter product by shop and branch
    product = Products.objects.filter(id=product_id, shop=shop, branch=selected_branch).first()
    if not product:
        return JsonResponse({"status": "failed", "msg": "❌ Product not found in this branch."}, status=404)

    # Suppliers in this shop (optionally, you could filter by branch if needed)
    suppliers = Supplier.objects.filter(shop=shop)

    return render(request, "posApp/stock_modal_form.html", {
        "product": product,
        "suppliers": suppliers,
        "branch": selected_branch
    })

@require_POST
@login_required
@permission_required('manage_stocks')
@subscription_required
def update_stock(request):
    def safe_float(val, default=0):
        
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    try:
        product_id = request.POST.get("id")
        price = safe_float(request.POST.get("price", 0))
        cost_price = safe_float(request.POST.get("cprice", 0))
        stock_change = int(request.POST.get("add_stock", 0))
        supplier_id = request.POST.get("supplier_id")
        payment_method = request.POST.get("payment_method", "cash").lower()

        # Tax fields
        tax_type = request.POST.get("tax_type", "none").lower()
        inclusive_tax = safe_float(request.POST.get("inclusive_tax", 0))
        exclusive_tax = safe_float(request.POST.get("exclusive_tax", 0))

        # Get user's shop and branch
        shop, user_branch = get_user_shop_and_branch(request)
        if not shop:
            return JsonResponse({"status": "failed", "msg": "❌ User shop not found"})

        # Determine branch for product update
        selected_branch = user_branch
        if request.user.is_superuser and request.POST.get("branch_id"):
            branch_id = request.POST.get("branch_id")
            selected_branch = Branch.objects.filter(id=branch_id, shop=shop).first()
        if not selected_branch:
            return JsonResponse({"status": "failed", "msg": "❌ Branch not found or invalid"})

        # Get product
        product = Products.objects.filter(id=product_id, shop=shop, branch=selected_branch).first()
        if not product:
            return JsonResponse({"status": "failed", "msg": "❌ Product not found in this branch/shop"})

        previous_stock = product.stock
        previous_price = product.price
        previous_cprice = product.cprice

        supplier = Supplier.objects.filter(id=supplier_id).first() if supplier_id else None

        # --- Update Product ---
        product.price = price
        product.cprice = cost_price
        product.stock += stock_change
        product.tax_type = tax_type

        # Apply only the selected tax
        if tax_type == "inclusive":
            product.inclusive_tax = inclusive_tax
            product.exclusive_tax = 0
        elif tax_type == "exclusive":
            product.exclusive_tax = exclusive_tax
            product.inclusive_tax = 0
        else:
            product.exclusive_tax = 0
            product.inclusive_tax = 0

        product.save()

        # Quantities for stock history
        quantity_added = max(stock_change, 0)
        quantity_removed = abs(min(stock_change, 0))
        total_cost = quantity_added * cost_price if quantity_added > 0 else 0

        # --- Log Stock History ---
        StockHistory.objects.create(
            product=product,
            supplier=supplier,
            previous_stock=previous_stock,
            quantity_added=quantity_added,
            quantity_removed=quantity_removed,
            new_stock=product.stock,
            previous_price=previous_price,
            new_price=price,
            previous_cprice=previous_cprice,
            new_cprice=cost_price,
            added_by=request.user,
            shop=shop,
            branch=selected_branch
        )

        # --- Purchase Order + Credit Handling ---
        if quantity_added > 0:
            po = PurchaseOrder.objects.create(
                supplier=supplier,
                product=product,
                quantity=quantity_added,
                cost_price=cost_price,
                payment_method=payment_method,
                added_by=request.user,
                shop=shop,
                branch=selected_branch
            )
            if payment_method == "credit":
                CreditPayment.objects.create(
                    purchase_order=po,
                    total_amount=total_cost,
                    paid_amount=0,
                    shop=shop,
                    branch=selected_branch,
                    status="pending",
                    added_by=request.user
                )

        return JsonResponse({
            "status": "success",
            "msg": f"✅ Stock updated successfully! Tax type: {tax_type.upper()}",
            "product_id": product.id,
            "new_stock": product.stock,
            "tax_type": tax_type,
            "inclusive_tax": product.inclusive_tax,
            "exclusive_tax": product.exclusive_tax,
        })

    except Exception as e:
        return JsonResponse({"status": "failed", "msg": f" Error: {str(e)}"})

@login_required
@csrf_exempt
@subscription_required
def import_stock_update(request):
    shop, branch = get_user_shop_and_branch(request)

    if request.method == "POST" and request.FILES.get("file"):
        excel_file = request.FILES["file"]

        try:
            df = pd.read_excel(excel_file)

            df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
            required_columns = ["product", "new_price", "new_cprice", "stock_change"]
            missing = [c for c in required_columns if c not in df.columns]
            if missing:
                messages.error(request, f"Missing column(s): {', '.join(missing)}")
                return redirect("stock")

            updated_count = 0
            with transaction.atomic():
                for _, row in df.iterrows():
                    product_name = str(row["product"]).strip()
                    product = Products.objects.filter(
                        name__iexact=product_name,
                        shop=shop,
                        branch=branch
                    ).first()

                    if not product:
                        continue  

                    previous_stock = product.stock
                    previous_price = product.price
                    previous_cprice = product.cprice

                    new_price = float(row["new_price"])
                    new_cprice = float(row["new_cprice"])
                    stock_change = int(row["stock_change"])

                    # Update product
                    product.price = new_price
                    product.cprice = new_cprice
                    product.stock = F('stock') + stock_change
                    product.save()
                    product.refresh_from_db()

                   
                    if stock_change > 0:
                        payment_method = row.get("payment_method", "cash").lower()  # optional column
                        po = PurchaseOrder.objects.create(
                            supplier=None,  
                            product=product,
                            quantity=stock_change,
                            cost_price=new_cprice,
                            payment_method=payment_method,
                            added_by=request.user,
                            shop=shop,
                            branch=branch
                        )

                        if payment_method == "credit":
                            CreditPayment.objects.create(
                                purchase_order=po,
                                total_amount=stock_change * new_cprice,
                                paid_amount=0,
                                shop=shop,
                                branch=branch,
                                status="pending",
                                added_by=request.user
                            )

                    # Log stock history
                    StockHistory.objects.create(
                        product=product,
                        previous_stock=previous_stock,
                        quantity_added=max(stock_change, 0),
                        quantity_removed=abs(min(stock_change, 0)),
                        new_stock=product.stock,
                        previous_price=previous_price,
                        new_price=new_price,
                        previous_cprice=previous_cprice,
                        new_cprice=new_cprice,
                        added_by=request.user,
                        shop=shop,
                        branch=branch
                    )

                    updated_count += 1

            messages.success(request, f"✅ {updated_count} products updated successfully!")

        except Exception as e:
            messages.error(request, f"Import failed: {e}")

    return redirect("stock")

@login_required
@csrf_exempt
@require_POST
@subscription_required
def bulk_stock_upload(request):
    shop, branch = get_user_shop_and_branch(request)
    if not request.FILES.get('file'):
        return JsonResponse({'status': 'failed', 'msg': 'No file uploaded'})

    excel_file = request.FILES['file']
    try:
        df = pd.read_excel(excel_file)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        required_cols = ['product', 'selling_price', 'cost_price', 'stock']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            return JsonResponse({'status': 'failed', 'msg': f'Missing columns: {", ".join(missing)}'})

        count = 0
        with transaction.atomic():
            for _, row in df.iterrows():
                product_name = str(row['product']).strip()
                product = Products.objects.filter(name__iexact=product_name, shop=shop).first()
                if product:
                    previous_stock = product.stock
                    previous_price = product.price
                    previous_cprice = product.cprice

                    new_price = float(row['selling_price'])
                    new_cprice = float(row['cost_price'])
                    new_stock = int(row['stock'])

                    stock_change = new_stock - previous_stock

                    # Update product
                    product.price = new_price
                    product.cprice = new_cprice
                    product.stock = new_stock
                    product.save()

                    # Log stock history
                    StockHistory.objects.create(
                        product=product,
                        previous_stock=previous_stock,
                        quantity_added=max(stock_change, 0),
                        quantity_removed=abs(min(stock_change, 0)),
                        new_stock=new_stock,
                        previous_price=previous_price,
                        new_price=new_price,
                        previous_cprice=previous_cprice,
                        new_cprice=new_cprice,
                        added_by=request.user,
                        shop=shop,
                        branch=branch
                    )

                    if stock_change > 0:
                        po = PurchaseOrder.objects.create(
                            supplier=None,  # optionally add supplier from Excel
                            product=product,
                            quantity=stock_change,
                            cost_price=new_cprice,
                            payment_method='cash',  
                            added_by=request.user,
                            shop=shop,
                            branch=branch
                        )

                        if po.payment_method.lower() == 'credit':
                            CreditPayment.objects.create(
                                purchase_order=po,
                                total_amount=stock_change * new_cprice,
                                paid_amount=0,
                                shop=shop,
                                branch=branch,
                                status='pending',
                                added_by=request.user
                            )

                    count += 1

        return JsonResponse({'status': 'success', 'msg': f'{count} products updated successfully!'})

    except Exception as e:
        return JsonResponse({'status': 'failed', 'msg': f'Error: {str(e)}'})

@login_required
def download_stock_excel(request):
    shop, branch = get_user_shop_and_branch(request)

    products = Products.objects.filter(shop=shop)
    if branch:
        products = products.filter(branch=branch)

    data = []
    for p in products:
        data.append({
            "Product": p.name,
            "Selling Price": p.price,
            "Cost Price": p.cprice,
            "Stock": p.stock,
            "Category": p.category.name if p.category else "",
            #"Supplier": p.supplier.name if p.supplier else "",
            "Description": p.description,
        })

    df = pd.DataFrame(data)

    
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=stock.xlsx'

    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Stock')

    return response


from django.db.models import F, OuterRef, Subquery, Sum, Value, DecimalField
from django.db.models.functions import Coalesce, Now
from django.db.models import IntegerField


def _in_range(qs, field, start, end):
   
    if start:
        qs = qs.filter(**{f"{field}__date__gte": start})
    if end:
        qs = qs.filter(**{f"{field}__date__lte": end})
    return qs


def _sum_by(qs, key, value):
    """{key: total} in one query."""
    rows = qs.order_by().values(key).annotate(t=Sum(value)).values_list(key, "t")
    return {k: (v or 0) for k, v in rows}


@login_required
@permission_required('view_stock_history')
def stock_update_history(request):
    shop, branch = get_user_shop_and_branch(request)
    if not shop or not branch:
        return JsonResponse({"status": "failed", "msg": "❌ No shop/branch found for this user"})

 
    start = _safe_date(request.GET.get('start_date'))
    end = _safe_date(request.GET.get('end_date'))
    product_id = request.GET.get('product')
    user_id = request.GET.get('user')

    next_update_time = (
        StockHistory.objects
        .filter(
            product=OuterRef('product'),
            added_on__gt=OuterRef('added_on'),
            quantity_added__gt=0,
        )
        .order_by('added_on')
        .values('added_on')[:1]
    )

    updates = (
        StockHistory.objects.select_related('added_by', 'product', 'supplier')
        .filter(product__shop=shop, product__branch=branch)
        .annotate(
            current_stock=F('product__stock'),
            window_end=Coalesce(Subquery(next_update_time), Now()),
        )
    )

    sold_subquery = (
        SalesItem.objects
        .filter(
            product=OuterRef('product'),
            type='sale',
            transaction__date_added__gte=OuterRef('added_on'),
            transaction__date_added__lt=OuterRef('window_end'),
        )
        .values('product')
        .annotate(total=Sum('qty'))
        .values('total')
    )

    adjusted_subquery = (
        StockAdjustment.objects
        .filter(
            product=OuterRef('product'),
            date__gte=OuterRef('added_on'),
            date__lt=OuterRef('window_end'),
        )
        .values('product')
        .annotate(total=Sum('quantity'))
        .values('total')
    )

    updates = updates.annotate(
        sold_qty=Coalesce(Subquery(sold_subquery, output_field=IntegerField()), 0),
        adjusted_qty=Coalesce(Subquery(adjusted_subquery, output_field=IntegerField()), 0),
    ).annotate(
        expected_stock=F('new_stock') - F('sold_qty') - F('adjusted_qty'),
    )

    updates = _in_range(updates, 'added_on', start, end)
    if product_id:
        updates = updates.filter(product_id=product_id)
    if user_id:
        updates = updates.filter(added_by_id=user_id)

    restocks = list(updates)
    for u in restocks:
        u.kind = 'update'
        u.event_time = u.added_on

    # ---------- stock counts ----------
    counts = (
        StockCount.objects
        .select_related('product', 'counted_by')
        .filter(product__shop=shop, product__branch=branch)
    )
    counts = _in_range(counts, 'date_counted', start, end)
    if product_id:
        counts = counts.filter(product_id=product_id)
    if user_id:
        counts = counts.filter(counted_by_id=user_id)

    count_rows = []
    for c in counts:
        count_rows.append(SimpleNamespace(
            kind='count',
            event_time=c.date_counted,
            product=c.product,
            added_by=c.counted_by,
            system_stock=c.physical_count - c.discrepancy,
            physical_count=c.physical_count,
            discrepancy=c.discrepancy,
            value_diff=c.discrepancy * (c.product.cprice or 0),
            current_stock=c.product.stock,
        ))

    movements = sorted(restocks + count_rows, key=lambda m: m.event_time, reverse=True)

    # ---------- branch transfers (sent from, or received by, this branch) ----------
    transfers = (
        ProductTransfer.objects
        .select_related('product', 'from_branch', 'to_branch', 'created_by')
        .filter(from_branch__shop=shop)
        .filter(Q(from_branch=branch) | Q(to_branch=branch))
    )
    transfers = _in_range(transfers, 'created_at', start, end)
    if product_id:
        picked = Products.objects.filter(pk=product_id, shop=shop, branch=branch).first()
        if picked:
            # sent: the product itself. received: same product name, now living in this branch
            transfers = transfers.filter(
                Q(product_id=picked.pk) | Q(to_branch=branch, product__name=picked.name)
            )
    if user_id:
        transfers = transfers.filter(created_by_id=user_id)

    transfer_rows = [
        SimpleNamespace(
            event_time=t.created_at,
            direction="Out" if t.from_branch_id == branch.id else "In",
            product=t.product,
            from_branch=t.from_branch,
            to_branch=t.to_branch,
            quantity=t.quantity,
            created_by=t.created_by,
        )
        for t in transfers.order_by('-created_at')
    ]

    # ---------- summary: one row per product ----------
    products_qs = (Products.objects.filter(shop=shop, branch=branch)
                   .select_related('category').order_by('name'))
    if product_id:
        products_qs = products_qs.filter(pk=product_id)
    ids = list(products_qs.values_list('pk', flat=True))

    added = _sum_by(_in_range(StockHistory.objects.filter(product_id__in=ids),
                              'added_on', start, end), 'product_id', 'quantity_added')

    def sales_of(kind):
        return _sum_by(_in_range(
            SalesItem.objects.filter(product_id__in=ids, type=kind),
            'transaction__date_added', start, end), 'product_id', 'qty')

    sold, returned, broken = sales_of('sale'), sales_of('return'), sales_of('breakage')

    adjusted = _sum_by(_in_range(StockAdjustment.objects.filter(product_id__in=ids),
                                 'date', start, end), 'product_id', 'quantity')
    sent = _sum_by(_in_range(ProductTransfer.objects.filter(from_branch=branch, product_id__in=ids),
                             'created_at', start, end), 'product_id', 'quantity')
    received_by_name = _sum_by(_in_range(
        ProductTransfer.objects.filter(to_branch=branch, product__shop=shop),
        'created_at', start, end), 'product__name', 'quantity')
    count_diff = _sum_by(_in_range(StockCount.objects.filter(product_id__in=ids),
                                   'date_counted', start, end), 'product_id', 'discrepancy')

    summary = []
    for p in products_qs:
        r = SimpleNamespace(
            product=p,
            added=added.get(p.pk, 0),
            sold=sold.get(p.pk, 0),
            returned=returned.get(p.pk, 0),
            broken=broken.get(p.pk, 0),
            adjusted=adjusted.get(p.pk, 0),
            sent=sent.get(p.pk, 0),
            received=received_by_name.get(p.name, 0),
            count_diff=count_diff.get(p.pk, 0),
            current=p.stock,
        )
        r.active = any((r.added, r.sold, r.returned, r.broken, r.adjusted,
                        r.sent, r.received, r.count_diff))
        summary.append(r)

    totals = SimpleNamespace(
        added=sum(r.added for r in summary),
        sold=sum(r.sold for r in summary),
        returned=sum(r.returned for r in summary),
        broken=sum(r.broken for r in summary),
        adjusted=sum(r.adjusted for r in summary),
        sent=sum(r.sent for r in summary),
        received=sum(r.received for r in summary),
        count_diff=sum(r.count_diff for r in summary),
        current=sum(r.current for r in summary),
    )

    # ---------- dropdowns ----------
    products = Products.objects.filter(shop=shop, branch=branch)
    users = User.objects.filter(
        Q(stockhistory__product__shop=shop, stockhistory__product__branch=branch) |
        Q(stockcount__product__shop=shop, stockcount__product__branch=branch) |
        Q(producttransfer__from_branch__shop=shop)
    ).distinct()

    return render(request, "posApp/stock_history.html", {
        "stock_updates": movements,
        "transfer_rows": transfer_rows,
        "summary": summary,
        "totals": totals,
        "products": products,
        "users": users,
        "branch_name": branch.name,
        "shop_name": shop.name,
    })


@login_required
@permission_required('view_stock_history')
def product_history(request, product_id):
    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "❌ No shop found for this user"})

    qs = Products.objects.filter(pk=product_id, shop=shop).select_related('category', 'branch')
    if not request.user.is_superuser and branch:
        qs = qs.filter(branch=branch)
    product = get_object_or_404(qs)

    events = []

    def add(time, kind, label, delta, who, detail):
        events.append({
            "time": time, "kind": kind, "label": label, "delta": delta,
            "qty_in": delta if delta > 0 else 0,
            "qty_out": -delta if delta < 0 else 0,
            "who": who, "detail": detail,
        })

    # restocks / stock updates
    for h in StockHistory.objects.filter(product=product).select_related('added_by', 'supplier'):
        delta = (h.quantity_added or 0) - (h.quantity_removed or 0)
        bits = []
        if h.supplier:
            bits.append(f"Supplier: {h.supplier.name}")
        if h.previous_price != h.new_price:
            bits.append(f"Price {h.previous_price} → {h.new_price}")
        if h.previous_cprice != h.new_cprice:
            bits.append(f"Cost {h.previous_cprice} → {h.new_cprice}")
        add(h.added_on, "restock" if delta > 0 else "update",
            "Restock" if delta > 0 else "Stock update",
            delta, h.added_by, " · ".join(bits))

    # sales, returns, breakage
    sale_labels = {"sale": ("Sale", -1), "return": ("Return", 1), "breakage": ("Breakage", -1)}
    for s in (SalesItem.objects.filter(product=product)
              .select_related('transaction', 'transaction__user')):
        label, sign = sale_labels.get(s.type, ("Sale", -1))
        t = s.transaction
        ref = getattr(t, "receipt_number", "") or getattr(t, "code", "")
        add(t.date_added, s.type if s.type in sale_labels else "sale", label,
            sign * s.qty, t.user, f"Receipt {ref}" if ref else "")

    # adjustments (breakage, loss, ...)
    for a in StockAdjustment.objects.filter(product=product).select_related('added_by'):
        add(a.date, "adjustment", "Adjustment", -a.quantity, a.added_by, a.reason or "")

    # stock counts
    for c in StockCount.objects.filter(product=product).select_related('counted_by'):
        add(c.date_counted, "count", "Stock count", c.discrepancy, c.counted_by,
            f"System {c.physical_count - c.discrepancy}, counted {c.physical_count}")

    # transfers out of this product
    for t in (ProductTransfer.objects.filter(product=product)
              .select_related('to_branch', 'created_by')):
        add(t.created_at, "transfer_out", "Transfer out", -t.quantity, t.created_by,
            f"To {t.to_branch.name}")

    # transfers into this product (same name, received by this branch)
    for t in (ProductTransfer.objects
              .filter(to_branch=product.branch, product__name=product.name, product__shop=shop)
              .exclude(product=product).select_related('from_branch', 'created_by')):
        add(t.created_at, "transfer_in", "Transfer in", t.quantity, t.created_by,
            f"From {t.from_branch.name}")

    # running balance, worked back from the stock the product has right now
    events.sort(key=lambda e: e["time"], reverse=True)
    balance = product.stock
    for e in events:
        e["balance"] = balance
        balance -= e["delta"]
    opening = balance          # stock before the first recorded movement

    # date filter is applied after the balances are worked out
    start = _safe_date(request.GET.get('start_date'))
    end = _safe_date(request.GET.get('end_date'))
    if start or end:
        def day(e):
            return timezone.localtime(e["time"]).date()
        events = [e for e in events
                  if (not start or day(e) >= start) and (not end or day(e) <= end)]

    total_in = sum(e["qty_in"] for e in events)
    total_out = sum(e["qty_out"] for e in events)

    return render(request, "posApp/product_history.html", {
        "product": product,
        "events": events,
        "opening": opening,
        "total_in": total_in,
        "total_out": total_out,
        "start_date": request.GET.get('start_date', ''),
        "end_date": request.GET.get('end_date', ''),
    })


@login_required
def credit_list(request):
    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return HttpResponseForbidden("User shop not found")

    credits = CreditPayment.objects.select_related(
        'purchase_order',
        'purchase_order__product',
        'purchase_order__supplier',
        'purchase_order__shop',
    ).prefetch_related('installments')

    # Filter by shop and branch
    credits = credits.filter(purchase_order__shop=shop)
    if branch:
        credits = credits.filter(branch=branch)

    # Due date = purchase order date + 30 days (same rule as the purchase order PDF)
    credits = list(credits)
    today = timezone.localdate()
    for c in credits:
        c.due_date = timezone.localtime(c.purchase_order.date).date() + timedelta(days=30)
        c.days_left = (c.due_date - today).days

    return render(request, 'posApp/credit_management.html', {
        'credits': credits
    })


@login_required
@subscription_required
def credit_modal_form(request):
    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "❌ User shop not found"})

    credit_id = request.GET.get("id")
    credit = get_object_or_404(CreditPayment, pk=credit_id)

    # Ensure credit belongs to user's shop
    if credit.purchase_order.shop != shop:
        return HttpResponseForbidden("Unauthorized access")

    installments = credit.installments.all().order_by('paid_at')

    return render(request, "posApp/credit_modal_form.html", {
        "credit": credit,
        "purchase_order": credit.purchase_order,
        "installments": installments
    })


@login_required
@subscription_required
def pay_credit(request, pk):
    if request.method != "POST":
        return JsonResponse({"status": "failed", "msg": "Invalid request"})

    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "❌ User shop not found"})

    credit = get_object_or_404(CreditPayment, pk=pk)

    # Ensure credit belongs to user's shop
    if credit.purchase_order.shop != shop:
        return JsonResponse({"status": "failed", "msg": "Unauthorized"})

    pay_amount = Decimal(request.POST.get("pay_amount", 0))
    if pay_amount <= 0 or pay_amount > credit.remaining_amount:
        return JsonResponse({"status": "failed", "msg": "Invalid payment amount"})

    # Update credit
    credit.paid_amount += pay_amount
    credit.status = 'paid' if credit.paid_amount >= credit.total_amount else 'partial'
    credit.save()

    # Log installment
    CreditInstallment.objects.create(
        credit_payment=credit,
        amount=pay_amount,
        paid_by=request.user
    )

    # Update the PurchaseOrder status based on related credit(s)
    order = credit.purchase_order
    # If all related credits are fully paid -> purchase order paid
    if all(c.paid_amount >= c.total_amount for c in order.credits.all()):
        order.status = 'paid'
    else:
        order.status = 'partial'
    order.save()

    return JsonResponse({
        "status": "success",
        "msg": f"✅ ${pay_amount} paid successfully!",
        "order_status": order.status
    })


@login_required
def stock_management(request):
    shop, branch = get_user_shop_and_branch(request)
    if shop and branch:
        products = Products.objects.filter(shop=shop, branch=branch)
        suppliers = Supplier.objects.filter(shop=shop)
    else:
        
        products = Products.objects.all()
        suppliers = Supplier.objects.all()

    return render(request, "posApp/stock_management.html", {
        "products": products,
        "suppliers": suppliers
    })
    
@login_required
def supplier_page(request):
    shop, user_branch = get_user_shop_and_branch(request)

    if request.user.is_superuser:
       
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch_id")
        suppliers = Supplier.objects.filter(shop=shop)
        if branch_id:
            suppliers = suppliers.filter(branch_id=branch_id)
    else:
        branches = [user_branch]  
        suppliers = Supplier.objects.filter(branch=user_branch)

    return render(request, "posApp/supplier_page.html", {
        "suppliers": suppliers,
        "branches": branches,
        "selected_branch": branch_id if request.user.is_superuser else None
    })


import logging
from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse
logger = logging.getLogger(__name__)

@login_required
@subscription_required
def add_supplier(request):
    
    if request.user.is_superuser:
        branch_id = request.GET.get("branch_id") or request.POST.get("branch_id")
        branch = Branch.objects.filter(id=branch_id).first() if branch_id else None
        suppliers_qs = Supplier.objects.all()
        if branch:
            suppliers_qs = suppliers_qs.filter(branch=branch)
        branches = Branch.objects.all()
    else:
        shop, branch = get_user_shop_and_branch(request)
        if not branch:
            messages.error(request, 'No branch assigned')
            return redirect('supplier_page')
        suppliers_qs = Supplier.objects.filter(branch=branch)
        branches = [branch]

    
    supplier_id = request.GET.get("id") or request.POST.get("id")
    supplier = suppliers_qs.filter(id=supplier_id).first() if supplier_id else None

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        contact = request.POST.get("contact", "").strip()
        email = request.POST.get("email", "").strip()
        address = request.POST.get("address", "").strip()

        if not name or not contact:
            messages.error(request, "Name and Contact are required.")
        else:
            try:
                if not supplier:
                    if request.user.is_superuser:
                        branch_id = request.POST.get("branch_id")
                        branch = Branch.objects.filter(id=branch_id).first()
                        if not branch:
                            messages.error(request, "Branch is required for superuser.")
                            return render(request, "posApp/supplier_form.html", {"supplier": supplier, "branches": branches})

                    Supplier.objects.create(
                        shop=branch.shop,
                        branch=branch,
                        name=name, contact=contact, email=email, address=address
                    )
                    messages.success(request, "Supplier added successfully!")
                else: # UPDATE
                    supplier.name = name
                    supplier.contact = contact
                    supplier.email = email
                    supplier.address = address
                    supplier.save()
                    messages.success(request, "Supplier updated successfully!")

                # REDIRECT BACK TO SUPPLIER PAGE TO CLOSE MODAL
                url = reverse('supplier_page')
                if request.user.is_superuser and branch:
                    url = f"{url}?branch_id={branch.id}"
                return redirect(url)

            except Exception as e:
                logger.error(f"User {request.user.id} failed to save supplier: {str(e)}")
                messages.error(request, "Server error. Please try again.")

    return render(request, "posApp/supplier_form.html", {
        "supplier": supplier,
        "branches": branches,
        "selected_branch": branch if not request.user.is_superuser else None
    })

@login_required
@subscription_required
def delete_supplier(request, supplier_id):
  
    try:
        supplier = Supplier.objects.filter(id=supplier_id).first()
        if not supplier:
            return JsonResponse({"status": "failed", "msg": "Supplier not found."})

        supplier.delete()
        return JsonResponse({"status": "success", "msg": "Supplier deleted successfully!"})

    except Exception as e:
        return JsonResponse({"status": "failed", "msg": str(e)})

@login_required
@permission_required('manage_purchase_orders')
@subscription_required
def purchase_orders(request):
    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return redirect("dashboard") 


    selected_branch = branch
    if request.user.is_superuser and request.GET.get("branch_id"):
        branch_id = request.GET.get("branch_id")
        selected_branch = Branch.objects.filter(id=branch_id, shop=shop).first()


    orders = PurchaseOrder.objects.filter(shop=shop)
    if selected_branch:
        orders = orders.filter(branch=selected_branch)

    orders = orders.select_related("supplier", "product", "added_by")\
                   .prefetch_related("credits")

 
    for order in orders:
        if order.payment_method == 'credit':
            credits = order.credits.all()
            if all(c.paid_amount >= c.total_amount for c in credits):
                order.status = 'paid'
            elif any(c.paid_amount > 0 for c in credits):
                order.status = 'partial'
            else:
                order.status = 'pending'

    now = timezone.now()
    start_date = now - timedelta(days=30)
    top_product = (
        PurchaseOrder.objects.filter(date__gte=start_date, shop=shop)
        .values(
            'product__id',
            'product__name',
            'product__price',
            'product__cprice'
        )
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')
        .first()
    )

    top_product_card = {}
    if top_product:
        qty = top_product['total_qty']
        selling_price = top_product['product__price']
        cost_price = top_product['product__cprice']
        top_product_card = {
            'name': top_product['product__name'],
            'selling_price': selling_price,
            'cost_price': cost_price,
            'quantity': qty,
            'expected_revenue': selling_price * qty,
            'expected_profit': (selling_price - cost_price) * qty
        }

    branches = Branch.objects.filter(shop=shop) if request.user.is_superuser else None

    return render(request, "posApp/purchase_order.html", {
        "orders": orders,
        "top_product_card": top_product_card,
        "branches": branches,
        "selected_branch": selected_branch,
        "shop_name": shop.name
    })

@login_required
@permission_required('manage_purchase_orders')
def purchase_order_view(request):
    order_id = request.GET.get("id")
    order = PurchaseOrder.objects.filter(id=order_id).select_related(
        "supplier", "product", "added_by"
    ).prefetch_related("credits").first()

    if not order:
        return HttpResponse("<p class='text-danger'>Purchase order not found.</p>")
    credit = order.credits.first() if order.payment_method == 'credit' else None
    discount = float(order.discount) if hasattr(order, 'discount') and order.discount else 0
    discount = Decimal(str(discount))

    total_cost = Decimal(order.quantity or 0) * (order.cost_price or Decimal('0'))
    total_cost -= (discount / Decimal('100')) * total_cost

    due_date = order.date + timedelta(days=30) if order.payment_method == 'credit' else None

    return render(request, "posApp/purchase_order_modal.html", {
        "order": order,
        "credit": credit,
        "discount": discount,
        "total_cost": total_cost,
        "due_date": due_date
    })


@login_required
def download_purchase_order_pdf(request, order_id):
    order = get_object_or_404(PurchaseOrder, pk=order_id)

    # Calculate discount and total cost
    discount = float(order.discount) if hasattr(order, 'discount') else 0
    total_cost = order.quantity * float(order.cost_price)
    total_cost_after_discount = total_cost - (discount / 100 * total_cost)
    profile = UserProfile.objects.filter(user=request.user).first()
    business_name = profile.business_name if profile and profile.business_name else ""
    branch_name = profile.branch.name if profile and profile.branch else "Main Branch"
    phone = profile.phone_number if profile and profile.phone_number else ""
    country = profile.country if profile and profile.country else "ZIMBABWE"

    # If credit, get first credit object and due date
    credit = order.credits.first() if order.payment_method == 'credit' else None
    due_date = order.date + timedelta(days=30) if order.payment_method == 'credit' else None

    # Create PDF
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_font("Arial", 'B', 20)
    pdf.cell(0, 10, business_name, ln=True, align='C')

    pdf.set_font("Arial", 'B', 18)
    pdf.cell(0, 6, f" {country} | {branch_name}| {phone}", ln=True, align='C')
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, f"Purchase Order #{order.id}", ln=True, align='C')

    pdf.set_font("Arial", '', 12)
    pdf.ln(10)
    pdf.cell(0, 8, f"Supplier: {order.supplier.name if order.supplier else 'N/A'}", ln=True)
    pdf.cell(0, 8, f"Product: {order.product.name if order.product else 'N/A'}", ln=True)
    pdf.cell(0, 8, f"Quantity: {order.quantity}", ln=True)
    pdf.cell(0, 8, f"Cost Price: ${order.cost_price:.2f}", ln=True)
    pdf.cell(0, 8, f"Discount: {discount}%", ln=True)
    pdf.cell(0, 8, f"Total Cost: ${total_cost_after_discount:.2f}", ln=True)
    pdf.cell(0, 8, f"Payment Method: {order.payment_method.capitalize()}", ln=True)

    if order.payment_method == 'credit' and credit:
        pdf.cell(0, 8, f"Credit Status: {credit.status.capitalize()}", ln=True)
        pdf.cell(0, 8, f"Paid Amount: ${credit.paid_amount:.2f}", ln=True)
        pdf.cell(0, 8, f"Remaining Amount: ${credit.remaining_amount:.2f}", ln=True)
        pdf.cell(0, 8, f"Payment Due Date: {due_date.strftime('%B %d, %Y')}", ln=True)

    pdf.cell(0, 8, f"Date: {order.date.strftime('%B %d, %Y')}", ln=True)
    pdf.cell(0, 8, f"Added By: {order.added_by.username}", ln=True)

    # Signatures
    pdf.ln(20)
    pdf.cell(90, 10, "Supplier Signature: ___________________", border=0)
    pdf.cell(0, 10, "Received By: ___________________", border=0)

    # Return PDF response
    response = HttpResponse(pdf.output(dest='S').encode('latin-1'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="purchase_order_{order.id}.pdf"'
    return response


from decimal import Decimal
from .models import ExchangeRate

from posApp.models import ExchangeRate

@csrf_exempt
def pos_api(request):
    shop, branch = get_user_shop_and_branch(request)
    products = Products.objects.filter(shop=shop, status=True)

    product_list = [
        {
            'id': product.id,
            'name': product.name,
            'price': float(product.price),
            'quantity': product.stock,
            'category': product.category.name if product.category else "",
        }
        for product in products
    ]

    data = {
        'status': 'success',
        'shop_name': shop.name,
        'products': product_list,
    }
    return JsonResponse(data)

from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.http import JsonResponse
import json

BASE_CURRENCY = "USD"

@csrf_exempt
def pos_checkout(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=400)

    try:
        body = json.loads(request.body.decode("utf-8"))
        shop, branch = get_user_shop_and_branch(request)

        cart = body.get("cart") or []

       
        chosen_currency = body.get("currency")

        if not isinstance(chosen_currency, str) or not chosen_currency:
            chosen_currency = BASE_CURRENCY   # USD is always the default

        chosen_currency = chosen_currency.upper().strip()

        if not cart:
            return JsonResponse({"status": "error", "message": "Cart is empty"}, status=400)


        rate = Decimal("1.0")

        if chosen_currency != BASE_CURRENCY:
            rate_obj = ExchangeRate.objects.filter(shop=shop, branch=branch, currency=chosen_currency).first()

            if rate_obj and rate_obj.rate and float(rate_obj.rate) > 0:
                rate = Decimal(str(rate_obj.rate))
            else:
                chosen_currency = BASE_CURRENCY
                rate = Decimal("1.0")

        total_base = Decimal("0.00")

        with transaction.atomic():

            txn = Transaction.objects.create(
                shop=shop,
                branch=branch,
                code=generate_unique_code(),
                currency=chosen_currency,
                exchange_rate=rate,
                grand_total=Decimal("0.00"),
                grand_total_base=Decimal("0.00"),
                user=request.user if request.user.is_authenticated else None
            )

            for item in cart:

                product_id = item.get("id")
                qty = int(item.get("qty") or 1)

                product = Products.objects.select_for_update().get(
                    id=product_id,
                    shop=shop
                )

                unit_price_base = Decimal(str(product.price))

                line_total_base = (unit_price_base * qty).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )

                if product.stock < qty:
                    raise ValueError(f"Not enough stock for {product.name}")

                product.stock -= qty
                product.save(update_fields=["stock"])

                total_base += line_total_base

                SalesItem.objects.create(
                    transaction=txn,
                    product=product,
                    qty=qty,
                    exchange_rate=rate if rate else Decimal("1.0"),
                    unit_price=unit_price_base,
                    total=line_total_base
                )

          
            grand_total_display = (total_base * rate).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP
            )

            txn.grand_total_base = total_base
            txn.grand_total = grand_total_display
            txn.save(update_fields=["grand_total", "grand_total_base"])

        return JsonResponse({
            "status": "success",
            "transaction_id": txn.id,
            "grand_total": str(txn.grand_total),
            "currency": chosen_currency
        })

    except Exception as e:
        return JsonResponse({
            "status": "error",
            "message": str(e)
        }, status=500)
    
@login_required
def add_order(request):
    if request.method == "POST":
        orderno = request.POST.get("orderno")
        amount = request.POST.get("amount")
        quantity = request.POST.get("quantity")
        total_amount = request.POST.get("amount")



@login_required
def stocktracker(request):
 
    try:
        profile = request.user.userprofile
        shop = profile.shop
        branch = profile.branch
    except Exception:
        return render(request, "posApp/trackproductreport.html", {"error": "No shop found for this user."})

    if not shop:
        return render(request, "posApp/trackproductreport.html", {"error": "No shop found for this user."})


    products = Products.objects.filter(shop=shop,branch=branch)
    
    today = now().date()

    
    sales_data = (
        SalesItem.objects.filter(transaction__shop=shop, transaction__date_added__date=today)
        .values('product_id', 'product__name')
        .annotate(total_amount=Sum('total'), quantity_sold=Sum('qty'))
        .order_by('-total_amount')
    )
    labels = [item['product__name'] for item in sales_data]
    amounts = [float(item['total_amount']) for item in sales_data]

   
    for product in products:
        last_stock = product.stock_history.order_by('-added_on').first()
        product.last_stock_update = localtime(last_stock.added_on) if last_stock else None

    
    low_stock_products = products.filter(stock__lt=20)

    
    stock_history_qs = StockHistory.objects.filter(product__in=products).select_related('product', 'added_by').order_by('-added_on')
    latest_stock_update = stock_history_qs.first()
    oldest_stock_update = stock_history_qs.order_by('added_on').first()

    latest_info = None
    if latest_stock_update:
        latest_info = {
            "product": latest_stock_update.product.name if latest_stock_update.product else "Unknown",
            "user": latest_stock_update.added_by.username if latest_stock_update.added_by else "System",
            "time": localtime(latest_stock_update.added_on),
            "product_id": latest_stock_update.product.id if latest_stock_update.product else None,
        }

    oldest_info = None
    if oldest_stock_update:
        oldest_info = {
            "product": oldest_stock_update.product.name if oldest_stock_update.product else "Unknown",
            "user": oldest_stock_update.added_by.username if oldest_stock_update.added_by else "System",
            "time": localtime(oldest_stock_update.added_on),
            "product_id": oldest_stock_update.product.id if oldest_stock_update.product else None,
        }

   
    category_stock_data = (
        products
        .values('category__name')
        .annotate(total_stock=Sum('stock'))
        .order_by('category__name')
    )
    category_labels = [item['category__name'] or "Uncategorized" for item in category_stock_data]
    category_stock = [int(item['total_stock']) for item in category_stock_data]

    
    low_stock_chart_data = products.order_by('stock')[:5]
    low_stock_labels = [p.name for p in low_stock_chart_data]
    low_stock_values = [int(p.stock) for p in low_stock_chart_data]

  
    low_stock_colors = [
        "rgba(220,53,69,0.8)" if p.stock < 20 else
        "rgba(54,162,235,0.8)" if p.stock <= 55 else
        "rgba(40,167,69,0.8)"
        for p in low_stock_chart_data
    ]

    # Prepare context
    context = {
        "products": products,
        "labels_json": json.dumps(labels),
        "amounts_json": json.dumps(amounts),
        "today": today,
        "shop_name": shop.name,
        "low_stock_count": low_stock_products.count(),
        "low_stock_products": low_stock_products,
        "latest_info": latest_info,
        "oldest_info": oldest_info,
        "stock_updates": stock_history_qs,
        "latest_stock_product_id": latest_info["product_id"] if latest_info else None,
        "oldest_stock_product_id": oldest_info["product_id"] if oldest_info else None,
        "category_labels": json.dumps(category_labels),
        "category_stock": json.dumps(category_stock),
        "low_stock_labels": json.dumps(low_stock_labels),
        "low_stock_values": json.dumps(low_stock_values),
        "low_stock_colors": json.dumps(low_stock_colors),
    }

    return render(request, 'posApp/trackproductreport.html', context)

@login_required
@permission_required("stock_adjustment")
@subscription_required
def adjust_stock(request):
    shop, branch = get_user_shop_and_branch(request)

    if not shop:
        return render(request, "posApp/adjust_stock.html", {
            "error": "No shop associated with this user."
        })

    # Superuser branch filtering
    selected_branch = branch
    if request.user.is_superuser:
        branches = Branch.objects.filter(shop=shop)
        branch_id = request.GET.get("branch")
        if branch_id:
            selected_branch = branches.filter(id=branch_id).first()
    else:
        branches = None
        selected_branch = branch

   
    products_qs = Products.objects.filter(shop=shop)
    if selected_branch:
        products_qs = products_qs.filter(branch=selected_branch)

    if request.method == "POST":
        product_id = request.POST.get("product_id")
        quantity = int(request.POST.get("quantity", 0))
        reason = request.POST.get("reason")

        product = get_object_or_404(products_qs, pk=product_id)

       
        if quantity > product.stock:
            return JsonResponse({
                "status": "failed",
                "msg": f"Not enough items in stock. Available: {product.stock}"
            })

       
        StockAdjustment.objects.create(
            product=product,
            quantity=quantity,
            reason=reason,
            added_by=request.user
        )

        
        product.stock = F('stock') - quantity
        product.save(update_fields=['stock'])

        return JsonResponse({
            "status": "success",
            "msg": f"{quantity} items removed from stock for reason: {reason}"
        })

    context = {
        "products": products_qs,
        "shop_name": shop.name,
        "branches": branches,
        "selected_branch": selected_branch
    }

    return render(request, "posApp/adjust_stock.html", context)

import csv
from django.db.models import Sum, Count, F, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse

@login_required
@permission_required("stock_adjustment")
@subscription_required
def stock_adjustment_report(request):
    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "❌ No shop found for this user"})

    adjustments = (
        StockAdjustment.objects
        .select_related('product', 'added_by')
        .filter(product__shop=shop)
    )
    if branch:
        adjustments = adjustments.filter(product__branch=branch)

    # filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    product_id = request.GET.get('product')
    user_id = request.GET.get('user')
    reason = request.GET.get('reason')

    if start_date:
        adjustments = adjustments.filter(date__date__gte=start_date)   
    if end_date:
        adjustments = adjustments.filter(date__date__lte=end_date)     
    if product_id:
        adjustments = adjustments.filter(product_id=product_id)
    if user_id:
        adjustments = adjustments.filter(added_by_id=user_id)
    if reason:
        adjustments = adjustments.filter(reason__icontains=reason)

    adjustments = adjustments.order_by('-date')

    # CSV export (respects the same filters)
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="stock_adjustments.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Product', 'Quantity', 'Reason', 'User'])
        for a in adjustments:
            writer.writerow([
                a.date.strftime('%Y-%m-%d %H:%M'),
                a.product.name,
                a.quantity,
                a.reason,
                a.added_by.username if a.added_by else '',
            ])
        return response

    # totals
    cost_value = ExpressionWrapper(
        F('quantity') * F('product__cprice'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    totals = adjustments.aggregate(
        total_qty=Coalesce(Sum('quantity'), 0),
        total_records=Count('id'),
        total_cost=Coalesce(Sum(cost_value), 0, output_field=DecimalField()),
    )

  
    by_product = (
        adjustments
        .order_by()
        .values('product__name')
        .annotate(qty=Sum('quantity'), records=Count('id'),
                  cost=Sum(cost_value))
        .order_by('-qty')
    )

    products = Products.objects.filter(shop=shop)
    if branch:
        products = products.filter(branch=branch)

    users = User.objects.filter(stockadjustment__product__shop=shop).distinct()

    return render(request, "posApp/stock_adjustment_report.html", {
        "adjustments": adjustments,
        "totals": totals,
        "by_product": by_product,
        "products": products,
        "users": users,
    })

@login_required
@permission_required("stock_count")
@subscription_required
def stock_count(request):
    shop, branch = get_user_shop_and_branch(request)

    
    selected_branch = branch
    if request.user.is_superuser and request.GET.get('branch'):
        branch_id = request.GET.get('branch')
        selected_branch = Branch.objects.filter(id=branch_id).first()

    
    products = Products.objects.all()
    if shop:
        products = products.filter(shop=shop)
    if selected_branch:
        products = products.filter(branch=selected_branch)
    products = products.filter(stock__gt=0)

    product_data = []
    for product in products:
        product_data.append({
            'product': product,
            'current_stock': product.stock,
        })

    if request.method == "POST":
        report = StockCountReport.objects.create(user=request.user)

        for item in product_data:
            counted_value = int(request.POST.get(f"count_{item['product'].id}", item['current_stock']))
            discrepancy = counted_value - item['current_stock']

            StockCount.objects.create(
                product=item['product'],
                counted_by=request.user,
                report=report,
                physical_count=counted_value,
                discrepancy=discrepancy,
                date_counted=timezone.now()
            )

            item['product'].stock = counted_value
            item['product'].save(update_fields=['stock'])

        return JsonResponse({
            "status": "success",
            "msg": "Stock counts updated successfully.",
            "report_id": report.id
        })


    branches = Branch.objects.filter(shop=shop) if request.user.is_superuser and shop else None

    return render(request, "posApp/stock_count.html", {
        "product_data": product_data,
        "branches": branches,
        "selected_branch": selected_branch,
    })


from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse


def _get_selected_branch(request, shop, user_branch):
    if request.user.is_superuser:
        branch_id = request.GET.get("branch")
        if branch_id and shop:
            return Branch.objects.filter(id=branch_id, shop=shop).first()
        return None  # superuser with no selection = all branches
    return user_branch


def _build_count_report_context(report, shop, selected_branch):
    counts = (
        report.counts
        .select_related("product", "counted_by")
        .filter(product__shop=shop)
    )
    if selected_branch:
        counts = counts.filter(product__branch=selected_branch)

    counts = list(counts.order_by("product__name"))  # list, so the attributes below persist

    total_shortage = 0
    total_overstock = 0
    total_shortage_qty = 0
    total_overstock_qty = 0
    net_discrepancy_qty = 0
    matched = 0

    for c in counts:
        c.system_stock = c.physical_count - c.discrepancy
        c.value_diff = c.discrepancy * c.product.cprice   

        if c.discrepancy < 0:
            total_shortage += abs(c.discrepancy) * c.product.cprice
            total_shortage_qty += abs(c.discrepancy)
        elif c.discrepancy > 0:
            total_overstock += c.discrepancy * c.product.cprice
            total_overstock_qty += c.discrepancy
        else:
            matched += 1

        net_discrepancy_qty += c.discrepancy

    return {
        "counts": counts,
        "products_counted": len(counts),
        "matched": matched,
        "report_by": report.user.username if report.user else "-",
        "report_date": report.timestamp,
        "total_shortage": total_shortage,
        "total_overstock": total_overstock,
        "total_shortage_qty": total_shortage_qty,
        "total_overstock_qty": total_overstock_qty,
        "net_discrepancy_qty": net_discrepancy_qty,
        "net_value": total_overstock - total_shortage,
    }


@login_required
@permission_required("stock_count_report")
def stock_count_reports(request, report_id=None):
    shop, branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "No shop found for this user."})

    if report_id:
        return redirect("view-stock-count-report", report_id=report_id)

    selected_branch = _get_selected_branch(request, shop, branch)

 
    count_filter = Q(counts__product__shop=shop)
    if selected_branch:
        count_filter &= Q(counts__product__branch=selected_branch)

    valid_ids = StockCountReport.objects.filter(count_filter).values("id")
    reports = (
        StockCountReport.objects
        .select_related("user")
        .filter(id__in=valid_ids)
        .annotate(total_products=Count("counts", filter=count_filter, distinct=True))
    )


    users = User.objects.filter(
        id__in=reports.values_list("user_id", flat=True)
    ).distinct()

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    user_id = request.GET.get("user")

    if start_date:
        reports = reports.filter(timestamp__date__gte=start_date)   
    if end_date:
        reports = reports.filter(timestamp__date__lte=end_date)    
    if user_id:
        reports = reports.filter(user_id=user_id)

    reports = list(reports.order_by("-timestamp"))

    totals = {
        "total_reports": len(reports),
        "total_products": sum(r.total_products for r in reports),
    }

    return render(request, "posApp/stock_count_report.html", {
        "reports": reports,
        "totals": totals,
        "users": users,
        "branches": Branch.objects.filter(shop=shop) if request.user.is_superuser else None,
        "selected_branch": selected_branch,
    })


@login_required
@permission_required("stock_count_report")
def view_stock_count_report(request, report_id):
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return JsonResponse({"status": "failed", "msg": "No shop found for this user."})

    report = get_object_or_404(
        StockCountReport.objects.select_related("user").filter(
            counts__product__shop=shop
        ).distinct(),
        id=report_id,
    )

    selected_branch = _get_selected_branch(request, shop, user_branch)

    context = _build_count_report_context(report, shop, selected_branch)
    context.update({
        "report": report,
        "branches": Branch.objects.filter(shop=shop) if request.user.is_superuser else None,
        "selected_branch": selected_branch,
    })
    return render(request, "posApp/view_stock_count_report.html", context)

@login_required
@permission_required("stock_count_report")
def stock_summary(request):
    shop, user_branch = get_user_shop_and_branch(request)
    selected_branch = user_branch

    # Branch filter via GET for superusers
    if request.user.is_superuser and request.GET.get("branch"):
        branch_id = request.GET.get("branch")
        selected_branch = Branch.objects.filter(id=branch_id, shop=shop).first()

    products = Products.objects.all()
    if shop:
        products = products.filter(shop=shop)
    if selected_branch:
        products = products.filter(branch=selected_branch)

    stock_summaries = []
    total_shortage = total_shortage_qty = total_overstock = total_overstock_qty = net_discrepancy_qty = 0

    for product in products:

        stock_ordered = StockHistory.objects.filter(
            product=product, shop=shop, branch=selected_branch
        ).aggregate(total=Sum('quantity_added'))['total'] or 0

        
        total_sold = Transaction.objects.filter(
            items__product=product, items__type = 'sale', shop=shop, branch=selected_branch
        ).aggregate(total=Sum('items__qty'))['total'] or 0

        # Total removed with reason
        stock_removed = StockAdjustment.objects.filter(
            product=product, date__lte=timezone.now(), added_by__isnull=False
        ).aggregate(total=Sum('quantity'))['total'] or 0

        # Latest stock count
        latest_count = StockCount.objects.filter(
            product=product, report__isnull=False
        ).order_by('-date_counted').first()

        
        available_stock = product.stock

        
        physical_count = latest_count.physical_count if latest_count else 0

        # Discrepancy
        discrepancy = latest_count.discrepancy if latest_count else 0

       
        if discrepancy < 0:
            total_shortage += abs(discrepancy) * product.cprice
            total_shortage_qty += abs(discrepancy)
        elif discrepancy > 0:
            total_overstock += discrepancy * product.cprice
            total_overstock_qty += discrepancy
        net_discrepancy_qty += discrepancy

        stock_summaries.append({
            'product': product,
            'stock_ordered': stock_ordered,
            'total_sold': total_sold,
            'stock_removed': stock_removed,
            'available_stock': available_stock,
            'physical_count': physical_count,  
            'discrepancy': discrepancy,
        })

    branches = Branch.objects.filter(shop=shop) if request.user.is_superuser and shop else None

    context = {
        "stock_summaries": stock_summaries,
        "branches": branches,
        "selected_branch": selected_branch,
        "total_shortage": total_shortage,
        "total_shortage_qty": total_shortage_qty,
        "total_overstock": total_overstock,
        "total_overstock_qty": total_overstock_qty,
        "net_discrepancy_qty": net_discrepancy_qty,
    }

    return render(request, "posApp/stock_summary.html", context)

@login_required
@permission_required("saleslist")
def salesList(request):
    shop, branch = get_user_shop_and_branch(request)
    sales = Transaction.objects.filter(shop=shop)
    if branch:
        sales = sales.filter(branch=branch)

    sale_data = []
    for sale in sales:
        data = {field.name: getattr(sale, field.name)
                for field in sale._meta.get_fields(include_parents=False)
                if field.related_model is None}

        items = SalesItem.objects.filter(transaction=sale)
        data['items'] = items
        data['item_count'] = len(items)
        data['tax_amount'] = format(float(getattr(sale, 'tax_amount', 0)), '.2f')

        sale_data.append(data)

    context = {
        'page_title': 'Sales Transactions',
        'sale_data': sale_data,
        'products': Products.objects.filter(shop=shop),
        'shop_name': shop.name,
    }

    return render(request, 'posApp/sales.html', context)


@login_required
@permission_required("stock_count_report")
def stock_report(request):
    shop, branch = get_user_shop_and_branch(request)
    reports = StockHistory.objects.select_related("product", "added_by").filter(product__shop=shop).order_by("-added_on")

    products = Products.objects.filter(shop=shop)
    users = User.objects.all()

    product_query = request.GET.get('product')
    if product_query:
        if product_query.isdigit():
            reports = reports.filter(
                Q(product__code__iexact=product_query) |
                Q(product__id=int(product_query))
            )
        else:
            reports = reports.filter(
                Q(product__name__istartswith=product_query) |
                Q(product__code__istartswith=product_query)
            )

    user_query = request.GET.get('user')
    if user_query:
        if user_query.isdigit():
            reports = reports.filter(added_by__id=int(user_query))
        else:
            reports = reports.filter(added_by__username__istartswith=user_query)

    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    if from_date:
        reports = reports.filter(added_on__date__gte=from_date)
    if to_date:
        reports = reports.filter(added_on__date__lte=to_date)

    context = {
        'page_title': 'Stock History Report',
        'reports': reports,
        'products': products,
        'users': users,
        'shop_name': shop.name,
    }

    return render(request, "posApp/stock_report.html", context)

from .print_queue import enqueue_receipt, enqueue_job
from .models import PrintJob


def _safe_date(value):
    try:
        return parse_date(value) if value else None
    except ValueError:
        return None


def _margins(sales, cost):

    profit = sales - cost
    signed = (profit / sales * 100) if sales > 0 else Decimal("0")
    if profit >= 0:
        return profit, signed, Decimal("0"), signed
    loss = ((cost - sales) / cost * 100) if cost > 0 else Decimal("0")
    return profit, Decimal("0"), loss, signed


@login_required
@permission_required('view_product_report')
def product_report(request):
    shop, branch = get_user_shop_and_branch(request)

    start_date = request.GET.get("start_date") or ""
    end_date = request.GET.get("end_date") or ""
    search = request.GET.get("search", "").strip()
    selected_currency = request.GET.get("currency", "").strip().upper()

    def code(v):
        return (v or "USD").upper()

    def D(v):
        return Decimal(str(v or 0))

    def rate_of(v):
        r = D(v)
        return r if r > 0 else Decimal("1")   # old sales without a rate count as 1

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    filters = {}
    if shop:
        filters["transaction__shop"] = shop
    if branch:
        filters["transaction__branch"] = branch
    d1, d2 = _safe_date(start_date), _safe_date(end_date)
    if d1:
        filters["transaction__date_added__date__gte"] = d1
    if d2:
        filters["transaction__date_added__date__lte"] = d2

    base_qs = SalesItem.objects.filter(**filters)
    if search:
        base_qs = base_qs.filter(
            Q(product__name__icontains=search) |
            Q(product__category__name__icontains=search)
        )

    # Currencies that appear in this period (before the currency filter),
    # so the dropdown always lists every currency that has sales.
    currencies_with_sales = {
        code(c) for c in base_qs.order_by()
        .values_list("transaction__currency", flat=True).distinct()
    }

    if selected_currency:
        if selected_currency == "USD":
            base_qs = base_qs.filter(
                Q(transaction__currency__iexact="USD") |
                Q(transaction__currency__isnull=True) |
                Q(transaction__currency="")
            )
        else:
            base_qs = base_qs.filter(transaction__currency__iexact=selected_currency)

    # Currencies to show: USD first, then active exchange rates, then any with sales
    rate_filter = {"shop": shop, "is_active": True}
    if branch:
        rate_filter["branch"] = branch
    active_rates = {
        c.upper() for c in
        ExchangeRate.objects.filter(**rate_filter).values_list("currency", flat=True)
    }
    codes = ["USD"] + sorted((active_rates | currencies_with_sales) - {"USD"})

    fallback_iter = cycle(FALLBACK_STYLES)
    styles = {c: (CURRENCY_STYLES.get(c) or next(fallback_iter)) for c in codes}

    # ------------------------------------------------------------------
    # Category rows (grouped by day + currency; item totals are in USD)
    # ------------------------------------------------------------------
    cat_rows = (
        base_qs.order_by()
        .values("product__category__name", "transaction__date_added__date",
                "transaction__currency", "transaction__exchange_rate")
        .annotate(
            total_sales=Sum("total"),
            total_cost=Sum(F("qty") * F("product__cprice")),
        )
    )

    cat_daily = {}                                   # (category, day) -> totals
    cat_period = {}                                  # category -> totals
    cur_totals = defaultdict(lambda: {"native": Decimal("0"), "usd": Decimal("0"),
                                      "cost": Decimal("0")})
    for r in cat_rows:
        sales, cost = D(r["total_sales"]), D(r["total_cost"])
        c = code(r["transaction__currency"])
        native = sales * rate_of(r["transaction__exchange_rate"])

        key = (r["product__category__name"], r["transaction__date_added__date"])
        e = cat_daily.setdefault(key, {"sales": Decimal("0"), "cost": Decimal("0"),
                                       "by_cur": defaultdict(Decimal)})
        e["sales"] += sales
        e["cost"] += cost
        e["by_cur"][c] += native

        p = cat_period.setdefault(key[0], {"sales": Decimal("0"), "cost": Decimal("0")})
        p["sales"] += sales
        p["cost"] += cost

        ct = cur_totals[c]
        ct["native"] += native
        ct["usd"] += sales
        ct["cost"] += cost

    def by_cur_list(by_cur):
        return [{"currency": c, "amount": float(by_cur[c])} for c in codes if by_cur.get(c)]

    category_data = []
    for (name, day), e in cat_daily.items():
        profit, pm, lm, _ = _margins(e["sales"], e["cost"])
        category_data.append({
            "category_name": name,
            "day_sold": day,
            "total_sales": float(e["sales"]),
            "total_cost": float(e["cost"]),
            "profit": float(profit),
            "profit_margin": float(pm),
            "loss_margin": float(lm),
            "by_currency": by_cur_list(e["by_cur"]),
        })
    category_data.sort(key=lambda x: (x["day_sold"], -x["total_sales"]))

    prod_rows = (
        base_qs.order_by()
        .values("product__name", "product__price", "product__cprice",
                "product__category__name", "transaction__date_added__date",
                "transaction__currency", "transaction__exchange_rate")
        .annotate(
            total_qty=Sum("qty"),
            total_sales=Sum("total"),
            total_cost=Sum(F("qty") * F("product__cprice")),
        )
    )

    prod_daily = {}
    prod_period = {}
    for r in prod_rows:
        sales, cost = D(r["total_sales"]), D(r["total_cost"])
        c = code(r["transaction__currency"])
        native = sales * rate_of(r["transaction__exchange_rate"])

        key = (r["product__name"], r["product__price"], r["product__cprice"],
               r["product__category__name"], r["transaction__date_added__date"])
        e = prod_daily.setdefault(key, {"qty": 0, "sales": Decimal("0"), "cost": Decimal("0"),
                                        "by_cur": defaultdict(Decimal)})
        e["qty"] += r["total_qty"] or 0
        e["sales"] += sales
        e["cost"] += cost
        e["by_cur"][c] += native

        p = prod_period.setdefault(r["product__name"], {"sales": Decimal("0"), "cost": Decimal("0")})
        p["sales"] += sales
        p["cost"] += cost

    product_data = []
    for (name, price, cprice, cat_name, day), e in prod_daily.items():
        profit, pm, lm, _ = _margins(e["sales"], e["cost"])
        product_data.append({
            "product_name": name,
            "category_name": cat_name,
            "unit_price": float(price or 0),
            "unit_cost": float(cprice or 0),
            "day_sold": day,
            "total_qty": e["qty"],
            "total_sales": float(e["sales"]),
            "total_cost": float(e["cost"]),
            "profit": float(profit),
            "profit_margin": float(pm),
            "loss_margin": float(lm),
            "by_currency": by_cur_list(e["by_cur"]),
        })
    product_data.sort(key=lambda x: (x["day_sold"], -x["total_sales"]))

    tx_counts = defaultdict(int)
    for r in (base_qs.order_by().values("transaction__currency")
              .annotate(n=Count("transaction", distinct=True))):
        tx_counts[code(r["transaction__currency"])] += r["n"]

    currency_sales = []
    for c in codes:
        t = cur_totals.get(c)
        usd = t["usd"] if t else Decimal("0")
        cost = t["cost"] if t else Decimal("0")
        profit = usd - cost
        currency_sales.append({
            "currency": c,
            "cls": styles[c][0],
            "color": styles[c][1],
            "sales": t["native"] if t else Decimal("0"),   # in that currency
            "usd": usd,                                    # USD value
            "profit": profit,                              # USD
            "margin": (profit / usd * 100) if usd > 0 else Decimal("0"),
            "count": tx_counts.get(c, 0),
        })

    currency_chart = {
        "labels": codes,
        "usd": [float(cur_totals[c]["usd"]) if c in cur_totals else 0.0 for c in codes],
        "native": [float(cur_totals[c]["native"]) if c in cur_totals else 0.0 for c in codes],
        "colors": [styles[c][1] for c in codes],
    }

    # ------------------------------------------------------------------
    # Best / worst over the whole period (ranked by margin)
    # ------------------------------------------------------------------
    def best_worst(period):
        ranked = []
        for name, t in period.items():
            _, _, _, signed = _margins(t["sales"], t["cost"])
            ranked.append((name, float(signed)))
        if not ranked:
            return None, 0, None, 0
        best = max(ranked, key=lambda x: x[1])
        worst = min(ranked, key=lambda x: x[1])
        return best[0], best[1], worst[0], worst[1]

    best_category, best_category_margin, worst_category, worst_category_margin = best_worst(cat_period)
    best_product, best_product_margin, worst_product, worst_product_margin = best_worst(prod_period)

    # ------------------------------------------------------------------
    # Charts: one bar/slice per category or product over the period (USD value)
    # ------------------------------------------------------------------
    cat_chart = sorted(cat_period.items(), key=lambda kv: -kv[1]["sales"])
    prod_chart = sorted(prod_period.items(), key=lambda kv: -kv[1]["sales"])

    context = {
        "sales_by_category": category_data,
        "sales_by_product": product_data,
        "start_date": start_date,
        "end_date": end_date,
        "search": search,
        "selected_currency": selected_currency,
        "currencies": codes,
        "currency_sales": currency_sales,
        "currency_chart": json.dumps(currency_chart),
        "category_labels": json.dumps([k for k, _ in cat_chart]),
        "category_sales": json.dumps([float(v["sales"]) for _, v in cat_chart]),
        "product_labels": json.dumps([k for k, _ in prod_chart]),
        "product_sales": json.dumps([float(v["sales"]) for _, v in prod_chart]),
        "best_category": best_category,
        "best_category_margin": best_category_margin,
        "worst_category": worst_category,
        "worst_category_margin": worst_category_margin,
        "best_product": best_product,
        "best_product_margin": best_product_margin,
        "worst_product": worst_product,
        "worst_product_margin": worst_product_margin,
    }

    return render(request, "posApp/product_report.html", context)

def get_product_report_data(request):
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    search = request.GET.get("search", "").strip()

    filters = {}
    if start_date:
        filters["transaction__date_added__date__gte"] = parse_date(start_date)
    if end_date:
        filters["transaction__date_added__date__lte"] = parse_date(end_date)

    qs = SalesItem.objects.select_related("transaction", "product", "product__category").filter(**filters)

    if search:
        qs = qs.filter(
            Q(product__name__icontains=search) |
            Q(product__category__name__icontains=search)
        )

    #  Sales grouped by category
    sales_by_category = (
        qs.annotate(day_sold=TruncDate("transaction__date_added"))
          .values("product__category__name", "day_sold")
          .annotate(
              total_sales=Sum("total"),
              total_cost=Sum(F("qty") * F("product__cprice")),
          )
          .order_by("day_sold", "-total_sales")
    )

    category_data = []
    for row in sales_by_category:
        total_sales = row["total_sales"] or 0
        total_cost = row["total_cost"] or 0
        profit = total_sales - total_cost
        profit_margin = (profit / total_sales * 100) if total_sales > 0 else 0
        loss_margin = ((total_cost - total_sales) / total_cost * 100) if total_cost > 0 and profit < 0 else 0

        category_data.append({
            "day_sold": row["day_sold"],
            "category_name": row["product__category__name"],
            "total_sales": float(total_sales),
            "total_cost": float(total_cost),
            "profit": float(profit),
            "profit_margin": float(profit_margin),
            "loss_margin": float(loss_margin),
        })

    #  Sales grouped by product
    sales_by_product = (
        qs.annotate(day_sold=TruncDate("transaction__date_added"))
          .values(
              "product__name",
              "product__price",
              "product__cprice",
              "product__category__name",
              "day_sold",
          )
          .annotate(
              total_qty=Sum("qty"),
              total_sales=Sum("total"),
              total_cost=Sum(F("qty") * F("product__cprice")),
          )
          .order_by("day_sold", "-total_sales")
    )

    product_data = []
    for row in sales_by_product:
        total_sales = row["total_sales"] or 0
        total_cost = row["total_cost"] or 0
        profit = total_sales - total_cost
        profit_margin = (profit / total_sales * 100) if total_sales > 0 else 0
        loss_margin = ((total_cost - total_sales) / total_cost * 100) if total_cost > 0 and profit < 0 else 0

        product_data.append({
            "day_sold": row["day_sold"],
            "category_name": row["product__category__name"],
            "product_name": row["product__name"],
            "unit_price": float(row["product__price"]),
            "unit_cost": float(row["product__cprice"]),
            "total_qty": row["total_qty"],
            "total_sales": float(total_sales),
            "total_cost": float(total_cost),
            "profit": float(profit),
            "profit_margin": float(profit_margin),
            "loss_margin": float(loss_margin),
        })

    return {
        "sales_by_category": category_data,
        "sales_by_product": product_data,
    }



def export_product_report_pdf(request):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=landscape(A4))
    width, height = landscape(A4)

    # Title
    p.setFont("Helvetica-Bold", 14)
    p.drawString(30, height - 40, "📊 Product & Category Sales Report")

    # Table header
    y = height - 80
    p.setFont("Helvetica-Bold", 10)
    header = "Category | Product | Qty | Sales | Cost | Profit | Profit Margin | Loss Margin"
    p.drawString(30, y, header)
    y -= 20

    # Fetch all products
    sales_data = SalesItem.objects.select_related('product', 'product__category').all()

    for item in sales_data:
        category = item.product.category.name if item.product.category else "N/A"
        product = item.product.name
        qty = item.qty
        sales = item.total
        cost = item.product.cprice * qty
        profit = sales - cost
        profit_margin = (profit / sales * 100) if sales else 0
        loss_margin = 100 - profit_margin

        line = f"{category} | {product} | {qty} | ${sales:.2f} | ${cost:.2f} | ${profit:.2f} | {profit_margin:.2f}% | {loss_margin:.2f}%"
        p.drawString(30, y, line[:150])  # prevent overflow
        y -= 15
        if y < 40:  # new page if space runs out
            p.showPage()
            y = height - 40

    p.save()
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename="product_report.pdf")

def export_product_report_excel(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Product Report"

    # Headers
    headers = [
        "Date Sold", "Category", "Product", "Unit Price", "Quantity",
        "Total Sales", "Total Cost", "Profit", "Profit Margin %", "Loss Margin %"
    ]
    ws.append(headers)

   
    sales_items = SalesItem.objects.select_related('product', 'product__category').all()

    for item in sales_items:
   
        date_sold = getattr(item, 'date_added', timezone.now()).strftime("%Y-%m-%d")

        category = item.product.category.name if item.product.category else "N/A"
        product_name = item.product.name
        qty = item.qty
        unit_price = item.product.price
        total_sales = item.total
        total_cost = (item.product.cprice or 0) * qty
        profit = total_sales - total_cost
        profit_margin = (profit / total_sales * 100) if total_sales else 0
        loss_margin = 100 - profit_margin

        ws.append([
            date_sold,
            category,
            product_name,
            unit_price,
            qty,
            total_sales,
            total_cost,
            profit,
            round(profit_margin, 2),
            round(loss_margin, 2)
        ])

   
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="product_report.xlsx"'
    wb.save(response)
    return response

@login_required
def checkout_modal(request):

    customer_name = request.GET.get('customer_name', '')
    currency = (request.GET.get('currency') or 'USD').upper()

    try:
        grand_total_usd = Decimal(request.GET.get('grand_total') or '0')
    except Exception:
        grand_total_usd = Decimal('0')

    shop, branch = get_user_shop_and_branch(request)

    
    rate = Decimal("1")
    if currency != "USD":
        ex = ExchangeRate.objects.filter(shop=shop, branch=branch, currency=currency,is_active =True).first()
        if ex and ex.rate:
            rate = Decimal(str(ex.rate))
        else:
            currency = "USD"   

    payable = (grand_total_usd * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    context = {
        "grand_total": payable,            
        "grand_total_usd": grand_total_usd,
        "currency": currency,
        "rate": rate,
        "customer_name": customer_name,
    }

    return render(request, "posApp/checkout.html", context)

@login_required
def pos(request):

    shop, branch = get_user_shop_and_branch(request)

    products = Products.objects.filter(shop=shop, branch=branch, status=True)

    other_currencies = list(
        ExchangeRate.objects.filter(shop=shop, branch=branch, is_active=True)
        .exclude(currency__iexact="USD")
        .order_by("currency")
        .values_list("currency", flat=True)
        .distinct()
    )
    currencies = ["USD"] + other_currencies

    pos_rates = {"USD": 1.0}
    for r in ExchangeRate.objects.filter(
        shop=shop, branch=branch, is_active=True
    ).exclude(currency__iexact="USD"):
        try:
            pos_rates[r.currency] = float(r.rate)
        except (TypeError, ValueError):
            pass

    # Real shift state, same filter that save_pos uses
    open_shift_obj = Shift.objects.filter(
        shop=shop, branch=branch, user=request.user, is_closed=False
    ).last()
    shift_open = open_shift_obj is not None

    # Currencies the cashier must count when closing (same rule as close_shift)
    shift_currencies = shift_close_currencies(open_shift_obj) if open_shift_obj else []

    context = {
        "page_title": "Point of Sale",
        "products": products,
        "product_json": json.dumps([
            {
                'id': product.id,
                'name': product.name,
                'price': float(product.price),
                'inclusive_tax': float(product.inclusive_tax),
                'exclusive_tax': float(product.exclusive_tax),
            }
            for product in products
        ]),
        "currencies": currencies,
        "default_currency": "USD",
        "exchange_rates_json": json.dumps(pos_rates),
        "shift_open": shift_open,
        "shift_currencies": shift_currencies,
    }

    return render(request, "posApp/pos.html", context)

    
def generate_receipt_number(shop, branch):
  
    with db_transaction.atomic():
        seq, _ = ReceiptSequence.objects.select_for_update().get_or_create(
            shop=shop, branch=branch
        )
        seq.last_number += 1
        seq.save(update_fields=["last_number"])
        next_number = seq.last_number

    shop_prefix = (shop.name or "SH")[:2].upper()
    branch_prefix = (branch.name or "BR")[:2].upper()

    return f"{shop_prefix}-{branch_prefix}-{next_number:06d}"

@login_required
@subscription_required
def save_pos(request):
    if request.method != "POST":
        return JsonResponse({"status": "failed", "msg": "Invalid request method."})

    shop, branch = get_user_shop_and_branch(request)
    data = request.POST

    def to_dec(val, default="0"):
        """Safe Decimal conversion (empty / bad values fall back to default)."""
        try:
            return Decimal(str(val if val not in (None, "") else default))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal(default)

    shift = Shift.objects.filter(shop=shop, branch=branch, user=request.user, is_closed=False).last()
    if not shift:
        return JsonResponse({"status": "failed", "msg": "No active shift. Please open a shift first."})

    products = data.getlist("product_id[]")
    qtys = data.getlist("qty[]")
    prices = data.getlist("price[]")
    transaction_types = data.getlist("transaction_type[]")

    if not products:
        return JsonResponse({"status": "failed", "msg": "No products submitted."})

    if not (len(products) == len(qtys) == len(prices)):
        return JsonResponse({"status": "failed", "msg": "Invalid item data."})

    try:
        grand_total_usd = Decimal("0.00")
        for i in range(len(products)):
            line = (Decimal(prices[i]) * int(qtys[i])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            grand_total_usd += line
        grand_total_usd = grand_total_usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return JsonResponse({"status": "failed", "msg": "Invalid item data."})

    payment_currency = (data.get("payment_currency") or data.get("currency") or "USD").upper().strip()
    payment_method = data.get("payment_method") or data.get("payment_type") or ""

    rate = Decimal("1")
    if payment_currency != "USD":
        ex = ExchangeRate.objects.filter(
            shop=shop, branch=branch, currency=payment_currency, is_active=True
        ).first()
        if not ex:
            return JsonResponse({
                "status": "failed",
                "msg": f"Exchange rate for {payment_currency} not found."
            })
        rate = Decimal(str(ex.rate))

    if rate == 0:
        return JsonResponse({"status": "failed", "msg": "Invalid exchange rate (0)."})

    tendered_amount_in_currency = to_dec(data.get("tendered_amount")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    grand_total_in_currency = (grand_total_usd * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if tendered_amount_in_currency < grand_total_in_currency:
        return JsonResponse({"status": "failed", "msg": "Insufficient funds."})

    change_in_currency = (tendered_amount_in_currency - grand_total_in_currency).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    inclusive_tax_total = to_dec(data.get("inclusive_tax_amount"))
    exclusive_tax_total = to_dec(data.get("exclusive_tax_amount"))

    try:
        with db_transaction.atomic():

            receipt_number = generate_receipt_number(shop, branch)

            transaction = Transaction.objects.create(
                shop=shop,
                branch=branch,
                code=f"{timezone.now().strftime('%Y%m%d%H%M%S')}",
                receipt_number=receipt_number,
                customer_name=data.get("customer_name"),
                sub_total=(grand_total_usd - exclusive_tax_total).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ),                                              
                inclusive_tax_amount=inclusive_tax_total,
                exclusive_tax_amount=exclusive_tax_total,
                grand_total=grand_total_in_currency,            
                grand_total_base=grand_total_usd,               
                tendered_amount=tendered_amount_in_currency,
                amount_change=change_in_currency,
                exchange_rate=rate,
                currency=payment_currency,
                payment_method=payment_method,
                user=request.user,
                shift=shift,
            )

            item_count = max(1, len(products))
            has_type_field = "type" in [f.name for f in SalesItem._meta.get_fields()]
            for i in range(len(products)):
                prod = Products.objects.select_for_update().get(pk=products[i], shop=shop)
                qty = int(qtys[i])
                price_usd = Decimal(prices[i])
                total_usd = (price_usd * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                tx_type = transaction_types[i] if i < len(transaction_types) else "sale"

                if tx_type == "sale":
                    if prod.stock < qty:
                        raise ValueError(f"Insufficient stock for {prod.name} (Available: {prod.stock}, Requested: {qty})")
                    prod.stock -= qty

                elif tx_type == "return":
                    prod.stock += qty

                elif tx_type == "breakage":
                    if prod.stock < qty:
                        raise ValueError(f"Not enough stock to mark {qty} as breakage for {prod.name}")
                    prod.stock -= qty

                prod.save()

                SalesItem.objects.create(
                    transaction=transaction,
                    product=prod,
                    qty=qty,
                    price=float(price_usd),
                    exchange_rate=rate,
                    total=float(total_usd),
                    inclusive_tax_amount=float(inclusive_tax_total / item_count),
                    exclusive_tax_amount=float(exclusive_tax_total / item_count),
                    type=tx_type if has_type_field else "sale",
                )

    except ValueError as e:
        return JsonResponse({"status": "failed", "msg": str(e)})
    except Exception as e:
        return JsonResponse({"status": "failed", "msg": "Server error: " + str(e)})

    job_id = None
    try:
        job = enqueue_receipt(transaction, request.user)
        job_id = job.id
    except Exception:
        logger.exception("Could not queue receipt for transaction %s", transaction.pk)

    return JsonResponse({
        "status": "success",
        "sale_id": transaction.id,
        "receipt_number": transaction.receipt_number,
        "print_job_id": job_id,
    })

from .print_queue import MAX_ATTEMPTS

@login_required
def print_job_status(request):
    shop, branch = get_user_shop_and_branch(request)
    try:
        ids = [int(i) for i in request.GET.get("ids", "").split(",") if i.strip()]
    except ValueError:
        ids = []
    jobs = PrintJob.objects.filter(
        pk__in=ids, transaction__shop=shop, transaction__branch=branch
    ).select_related("transaction")
    return JsonResponse({"jobs": [
        {"id": j.id, "status": j.status, "error": j.error,
         "attempts": j.attempts, "max_attempts": MAX_ATTEMPTS,
         "receipt": j.transaction.receipt_number}
        for j in jobs
    ]})


@login_required
def print_job_retry(request):
    if request.method != "POST":
        return JsonResponse({"status": "failed", "message": "Invalid request method."})
    shop, branch = get_user_shop_and_branch(request)
    try:
        payload = json.loads(request.body or "{}")
        job = PrintJob.objects.get(
            pk=payload.get("id"), transaction__shop=shop, transaction__branch=branch
        )
    except (ValueError, PrintJob.DoesNotExist):
        return JsonResponse({"status": "failed", "message": "Print job not found."})

    job.status = PrintJob.PENDING
    job.attempts = 0
    job.error = ""
    job.save(update_fields=["status", "attempts", "error", "updated_at"])
    enqueue_job(job)
    return JsonResponse({"status": "success"})

    

def generate_barcode(transaction_code):

    CODE128 = barcode.get_barcode_class('code128')
    buffer = BytesIO()
    CODE128(transaction_code, writer=ImageWriter()).write(buffer)
    barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return barcode_base64

@login_required
def exchange_rates(request):
    shop, branch = get_user_shop_and_branch(request)
    rates = ExchangeRate.objects.filter(shop=shop, branch=branch).order_by("currency")

    if request.method == "POST":
        currency = request.POST.get("currency")
        rate = request.POST.get("rate")
        source = request.POST.get("source", "")
        if not currency or not rate:
            return JsonResponse({
                "status": "failed",
                "msg": "Currency and rate are required"
            })
        ExchangeRate.objects.update_or_create(
            shop=shop,
            branch=branch,
            currency=currency.upper().strip(),
            defaults={
                "rate": rate,
                "source": source
            }
        )
        return JsonResponse({"status": "success"})

    return render(request, "posApp/exchange_rates.html", {
        "rates": rates
    })


@login_required
def exchange_rate_list(request):
    shop, branch = get_user_shop_and_branch(request)
    rate_list = ExchangeRate.objects.filter(shop=shop, branch=branch).order_by("currency")

    currency_filter = request.GET.get("currency")
    if currency_filter:
        rate_list = rate_list.filter(currency__icontains=currency_filter.upper())

    from django.core.paginator import Paginator
    paginator = Paginator(rate_list, 10)  
    page_number = request.GET.get("page")
    rates = paginator.get_page(page_number)

    context = {
        "rates": rates,
        "currency_filter": currency_filter or ""
    }
    return render(request, "posApp/exchange_rates.html", context)


@login_required
def update_exchange_rate(request):
    shop, branch = get_user_shop_and_branch(request)

    if request.method == "POST":
        rate_id = request.POST.get("id")
        try:
            new_rate = round_2dp(request.POST.get("rate"))
        except Exception:
            return JsonResponse({"status": "failed", "msg": "Enter a valid rate."})
        if new_rate <= 0:
            return JsonResponse({"status": "failed", "msg": "Rate must be greater than 0."})
        try:
          
            rate_obj = ExchangeRate.objects.get(id=rate_id, shop=shop, branch=branch)
            old_rate = rate_obj.rate
            rate_obj.rate = new_rate
            if "source" in request.POST:
                rate_obj.source = request.POST.get("source", "").strip()
            rate_obj.save()
            ExchangeRateAudit.objects.create(
                shop=shop,
                branch=branch,
                currency=rate_obj.currency,
                old_rate=round_2dp(old_rate),
                new_rate=new_rate,
                changed_by=request.user,
                action="UPDATE"
            )
            return JsonResponse({
                "status": "success",
                "rate": str(new_rate)
            })
        except ExchangeRate.DoesNotExist:
            return JsonResponse({
                "status": "failed",
                "msg": "Rate not found"
            })
    return JsonResponse({
        "status": "failed",
        "msg": "Invalid request"
    })


@login_required
def add_exchange_rate(request):
    shop, branch = get_user_shop_and_branch(request)

    if request.method == "POST":
        currency = request.POST.get("currency", "").strip().upper()
        try:
            rate = round_2dp(request.POST.get("rate"))
        except Exception:
            return JsonResponse({"status": "failed", "msg": "Enter a valid rate."})
        source = request.POST.get("source", "")
        if len(currency) != 3 or not currency.isalpha():
            return JsonResponse({"status": "failed", "msg": "Enter a valid 3-letter currency code."})
        if currency == "USD":
            return JsonResponse({"status": "failed",
                                 "msg": "USD is the base currency (always 1.00) and is always available in the POS."})
        if rate <= 0:
            return JsonResponse({"status": "failed", "msg": "Rate must be greater than 0."})
        old = (ExchangeRate.objects.filter(shop=shop, branch=branch, currency=currency)
               .values_list("rate", flat=True).first())
        obj, created = ExchangeRate.objects.update_or_create(
            shop=shop,
            branch=branch,
            currency=currency,
            defaults={
                "rate": rate,
                "source": source
            }
        )
        ExchangeRateAudit.objects.create(
            shop=shop,
            branch=branch,
            currency=currency,
            old_rate=round_2dp(old) if old is not None else rate,
            new_rate=rate,
            changed_by=request.user,
            action="CREATE" if created else "UPDATE"
        )
        return JsonResponse({"status": "success"})
    return JsonResponse({
        "status": "failed",
        "msg": "Invalid request"
    })

@login_required
@require_POST
def toggle_exchange_rate(request):
    shop, branch = get_user_shop_and_branch(request)
    try:
        obj = ExchangeRate.objects.get(id=request.POST.get("id"), shop=shop, branch=branch)
    except ExchangeRate.DoesNotExist:
        return JsonResponse({"status": "failed", "msg": "Rate not found"})

    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    ExchangeRateAudit.objects.create(
        shop=shop, branch=branch, currency=obj.currency,
        old_rate=round_2dp(obj.rate), new_rate=round_2dp(obj.rate),
        changed_by=request.user,
        action="ACTIVATE" if obj.is_active else "DEACTIVATE",
    )
    return JsonResponse({"status": "success", "is_active": obj.is_active})


@login_required
@require_POST
def delete_exchange_rate(request):
    shop, branch = get_user_shop_and_branch(request)
    try:
        obj = ExchangeRate.objects.get(id=request.POST.get("id"), shop=shop, branch=branch)
    except ExchangeRate.DoesNotExist:
        return JsonResponse({"status": "failed", "msg": "Rate not found"})

    if Transaction.objects.filter(
        currency=obj.currency, shift__shop=shop, shift__branch=branch
    ).exists():
        return JsonResponse({
            "status": "failed",
            "msg": f"{obj.currency} already has sales. Deactivate it instead of deleting.",
        })

    ExchangeRateAudit.objects.create(
        shop=shop, branch=branch, currency=obj.currency,
        old_rate=round_2dp(obj.rate), new_rate=round_2dp(obj.rate),
        changed_by=request.user, action="DELETE",
    )
    obj.delete()
    return JsonResponse({"status": "success"})

@login_required
def exchange_rate_audit_log(request):
    shop, branch = get_user_shop_and_branch(request)
    audits = ExchangeRateAudit.objects.filter(shop=shop, branch=branch).order_by("-created_at")
    return render(
        request,
        "posApp/exchange_rate_audit.html",
        {
            "audits": audits
        }
    )


@login_required
def api_get_exchange_rates(request):
    shop, branch = get_user_shop_and_branch(request)
    rates = ExchangeRate.objects.filter(shop=shop, branch=branch)
    data = {rate.currency_to: float(rate.rate) for rate in rates if rate.currency_from == "USD"}
    return JsonResponse(data)

@csrf_exempt
@login_required
def cancel_transaction(request):
    if request.method == "POST":
        data = json.loads(request.body)
        transaction_id = data.get("transaction_id")
        shop, _ = get_user_shop_and_branch(request)
        transaction = Transaction.objects.filter(id=transaction_id, shop=shop).first()

        if not transaction:
            return JsonResponse({"status": "failed", "msg": "Transaction not found!"})


        sales_items = SalesItem.objects.filter(transaction=transaction)
        for item in sales_items:
            product = item.product
            product.stock += item.qty
            product.save()

        transaction.delete()  
        return JsonResponse({"status": "success", "msg": "Transaction cancelled and stock rolled back!"})
    
    return JsonResponse({"status": "failed", "msg": "Invalid request"})

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone



def _dec(value):
   
    return Decimal(str(value or 0))


@login_required
def receipt(request, transaction_id=None):
    shop, _ = get_user_shop_and_branch(request)
    sale_id = transaction_id or request.GET.get("id")
    transaction = Transaction.objects.filter(id=sale_id, shop=shop).first()

    if not transaction:
        messages.error(request, "Transaction not found!")
        return redirect("pos-page")


    if not request.GET.get("view"):
        came_from = urlparse(request.META.get("HTTP_REFERER", "")).path.rstrip("/")
        if came_from == reverse("pos-page").rstrip("/"):
            return render(request, "posApp/receipt_skip.html", {"pos_url": reverse("pos-page")})

    receipt_settings, _ = ReceiptSettings.objects.get_or_create(branch=transaction.branch)
    fmt = get_receipt_format(transaction)      

    profile = UserProfile.objects.filter(shop=shop).first()
    business_type = profile.business_type if profile else "General"
    branch = profile.branch if profile else "Main"
    printer_settings = PrinterSettings.objects.filter(shop=shop).first()

    if printer_settings:
        paper_size_map = {"A4": 210, "80": 80, "58": 58}
        paper_width = paper_size_map.get(printer_settings.paper_size, 80)
        margin_top = printer_settings.margin_top or 5
        margin_bottom = printer_settings.margin_bottom or 5
        header_text = printer_settings.header_text or ""
        footer_text = printer_settings.footer_text or "Thank you for shopping with us!"
        auto_print = printer_settings.auto_print
    else:
        paper_width = 80
        margin_top = 5
        margin_bottom = 5
        header_text = ""
        footer_text = "Thank you for shopping with us!"
        auto_print = True

    sales_items = list(
        SalesItem.objects.filter(transaction=transaction).select_related("product")
    )

    exchange_rate = _dec(transaction.exchange_rate) or Decimal("1")

    inclusive_total = Decimal("0")
    exclusive_total = Decimal("0")
    inclusive_tax_total = Decimal("0")
    exclusive_tax_total = Decimal("0")

    for item in sales_items:
        price = _dec(item.price)
        line_total = price * item.qty                      # base currency
        inc_rate = _dec(item.product.inclusive_tax)
        exc_rate = _dec(item.product.exclusive_tax)

        item.display_price = price * exchange_rate
        item.line_total = line_total * exchange_rate

        if inc_rate > 0:
            item.tax_type = "inclusive"
            item.tax_amount = line_total * inc_rate / 100
            inclusive_tax_total += item.tax_amount
            inclusive_total += line_total
        elif exc_rate > 0:
            item.tax_type = "exclusive"
            item.tax_amount = line_total * exc_rate / 100
            exclusive_tax_total += item.tax_amount
            exclusive_total += line_total
        else:
            item.tax_type = "none"
            item.tax_amount = Decimal("0")
            inclusive_total += line_total

    # Price total is the final amount (what the customer pays)
    price_total_base = inclusive_total + exclusive_total

    # Exclusive tax is carved out of the price:
    # Sub Total = price total - exclusive tax, Grand Total = price total
    sub_total_base = price_total_base - exclusive_tax_total
    grand_total_base = sub_total_base + exclusive_tax_total

    sub_total = sub_total_base * exchange_rate
    grand_total = grand_total_base * exchange_rate

    barcode_image = generate_barcode(transaction.code)

    context = {
        "transaction": transaction,
        "salesItems": sales_items,
        "sale": transaction,
        "shop_name": shop.name,
        "branch": branch.name if hasattr(branch, "name") else branch,
        "business_type": business_type,
        "barcode_image": barcode_image,
        "printer_settings": printer_settings,
        "paper_width": paper_width,
        "margin_top": margin_top,
        "margin_bottom": margin_bottom,
        "header_text": header_text,
        "footer_text": footer_text,
        "auto_print": auto_print,
        "receipt_number": transaction.receipt_number,
        "user": transaction.user or request.user,       # the cashier who made the sale
        "sub_total": sub_total,
        "grand_total": grand_total,
        "receipt_settings": receipt_settings,
        "fmt": fmt,
        "currency": transaction.currency,
        "inclusive_tax_total": inclusive_tax_total * exchange_rate,
        "exclusive_tax_total": exclusive_tax_total * exchange_rate,
        "now": timezone.now(),
    }

    return render(request, "posApp/receipt.html", context)

@login_required
def receipt_view(request, transaction_id):
    shop, _ = get_user_shop_and_branch(request)
    if not Transaction.objects.filter(id=transaction_id, shop=shop).exists():
        messages.error(request, "Transaction not found!")
        return redirect("sales-page")
    return receipt(request, transaction_id)


import textwrap
from decimal import Decimal

from django.utils import timezone


PAPER_COLUMNS = {"58": 32, "80": 42, "A4": 80}

ESC_INIT = b"\x1b@"
BOLD_ON = b"\x1bE\x01"
BOLD_OFF = b"\x1bE\x00"
CUT = b"\x1dV\x01"          


def _dec(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def _money(value):
    return f"{value:,.2f}"


def _row(left, right, width):
    """left text, right text, padded to the line width."""
    left, right = str(left), str(right)
    space = width - len(left) - len(right)
    if space < 1:
        left = left[: max(0, width - len(right) - 1)]
        space = 1
    return left + " " * space + right


def build_receipt_bytes(transaction, items, user, printer_settings, receipt_settings):
    paper = str(getattr(printer_settings, "paper_size", "80") or "80")
    thermal = paper in ("58", "80")
    width = PAPER_COLUMNS.get(paper, 42)

    rate = _dec(transaction.exchange_rate) or Decimal("1")
    currency = transaction.currency or ""

    parts = []

    def text(line="", bold=False):
        if bold and thermal:
            parts.append(BOLD_ON)
        parts.append((line + "\n").encode("cp437", errors="replace"))
        if bold and thermal:
            parts.append(BOLD_OFF)

    def center(line, bold=False):
        text(line.center(width).rstrip(), bold)

    def rule():
        text("-" * width)

    if thermal:
        parts.append(ESC_INIT)

    # ---- header -------------------------------------------------------
    header = getattr(receipt_settings, "custom_header_name", "") or \
        f"{transaction.shop.name} - {transaction.branch.name}"
    center(header.upper(), bold=True)
    extra_header = getattr(printer_settings, "header_text", "") or ""
    for line in extra_header.splitlines():
        center(line.strip())
    rule()

    cashier = (f"{user.first_name} {user.last_name}").strip() or user.get_username()
    text(f"Cashier: {cashier.title()}")
    if transaction.customer_name:
        text(f"Customer: {str(transaction.customer_name).title()}")
    when = transaction.date_added
    if timezone.is_aware(when):
        when = timezone.localtime(when)
    text(f"Date: {when.strftime('%d %b %Y %H:%M')}")
    text(f"Code: {transaction.code}")
    text(f"Receipt No: {transaction.receipt_number or 'N/A'}")
    text(f"Currency: {currency}")
    rule()

    # ---- items + tax (same rules as the receipt view) -----------------
    inclusive_total = exclusive_total = Decimal("0")
    inclusive_tax_total = exclusive_tax_total = Decimal("0")

    text(_row("Item", "Total", width), bold=True)
    for item in items:
        price = _dec(item.price)
        line_total = price * item.qty
        inc_rate = _dec(item.product.inclusive_tax)
        exc_rate = _dec(item.product.exclusive_tax)

        if inc_rate > 0:
            inclusive_tax_total += line_total * inc_rate / 100
            inclusive_total += line_total
        elif exc_rate > 0:
            exclusive_tax_total += line_total * exc_rate / 100
            exclusive_total += line_total
        else:
            inclusive_total += line_total

        for name_line in textwrap.wrap(str(item.product.name), width) or [""]:
            text(name_line)
        text(_row(f"  {item.qty} x {_money(price * rate)}", _money(line_total * rate), width))

    rule()

    sub_total = (inclusive_total + exclusive_total) * rate
    grand_total = (inclusive_total + exclusive_total + exclusive_tax_total) * rate

    # ---- totals -------------------------------------------------------
    text(_row("Subtotal:", f"{currency} {_money(sub_total)}", width))
    if inclusive_tax_total > 0:
        text(_row("Inclusive Tax:", f"{currency} {_money(inclusive_tax_total * rate)}", width))
    if exclusive_tax_total > 0:
        text(_row("Exclusive Tax:", f"{currency} {_money(exclusive_tax_total * rate)}", width))
    text(_row("GRAND TOTAL:", f"{currency} {_money(grand_total)}", width), bold=True)
    text(_row("Tendered:", f"{currency} {_money(_dec(transaction.tendered_amount))}", width))
    text(_row("Change:", f"{currency} {_money(_dec(transaction.amount_change))}", width))
    rule()

    # ---- footer -------------------------------------------------------
    footer = getattr(receipt_settings, "footer_text", "") or \
        getattr(printer_settings, "footer_text", "") or "Thank you for shopping with us!"
    for line in footer.splitlines():
        center(line.strip())
    if getattr(receipt_settings, "show_timestamp", False):
        center(timezone.localtime().strftime("%d %b %Y %H:%M"))

    if thermal:
        parts.append(b"\n\n\n\n")
        parts.append(CUT)
    else:
        parts.append(b"\f")          # eject the page on A4 printers

    return b"".join(parts)


def _printer_name(printer_settings):
    """Printer chosen on the Printer Settings page, else the Windows default."""
    for field in ("printer_name", "printer", "default_printer"):
        value = getattr(printer_settings, field, None)
        if value:
            return str(value)
    return None


SERIAL_BAUD = 9600          


def list_printers():
    
    names = []
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names += [p[2] for p in win32print.EnumPrinters(flags)]
    except ImportError:
        pass
    try:
        from serial.tools import list_ports          # pip install pyserial
        names += [port.device for port in list_ports.comports()]
    except ImportError:
        pass
    return sorted(set(names))


def _send_serial(port, data):
    import time
    import serial                                   

    try:
        conn = serial.Serial(port, SERIAL_BAUD, timeout=5, write_timeout=10)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open {port}. Is the printer switched on and paired with this PC? ({exc})"
        )
    try:
        for i in range(0, len(data), 512):           
            conn.write(data[i:i + 512])
            conn.flush()
            time.sleep(0.05)
        time.sleep(1)                                
    finally:
        conn.close()


def send_raw_to_printer(printer_name, data):
    if printer_name and str(printer_name).upper().startswith("COM"):
        return _send_serial(str(printer_name).upper(), data)

    import win32print          

    name = printer_name or win32print.GetDefaultPrinter()
    try:
        handle = win32print.OpenPrinter(name)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open printer '{name}'. Check the printer name in Printer Settings "
            f"and that the printer is on and installed in Windows. ({exc})"
        )
    try:
        win32print.StartDocPrinter(handle, 1, ("POS Receipt", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def print_transaction_receipt(transaction, user):
    from .models import SalesItem, PrinterSettings, ReceiptSettings

    printer_settings = PrinterSettings.objects.filter(shop=transaction.shop).first()
    if printer_settings is not None:
        if not getattr(printer_settings, "auto_print", True):
            return False
       
        if str(getattr(printer_settings, "connection_type", "system")).lower() == "ble":
            return False

    receipt_settings, _ = ReceiptSettings.objects.get_or_create(branch=transaction.branch)
    items = list(SalesItem.objects.filter(transaction=transaction).select_related("product"))

    data = build_receipt_bytes(transaction, items, user, printer_settings, receipt_settings)
    send_raw_to_printer(_printer_name(printer_settings), data)
    return True

from .receipt_printing import (
    print_transaction_receipt, list_printers, print_test_page,
    ensure_receipt_format, get_receipt_format,
)

@login_required
def printer_test(request):
    if request.method != "POST":
        return JsonResponse({"status": "failed", "msg": "Invalid request method."})
    shop, _ = get_user_shop_and_branch(request)
    ps = PrinterSettings.objects.filter(shop=shop).first()
    try:
        print_test_page(ps)
        return JsonResponse({"status": "success"})
    except Exception as e:
        print_error = None
    try:
        ensure_receipt_format(transaction)      
        print_transaction_receipt(transaction, request.user)
    except Exception as e:
        print_error = str(e)

    return JsonResponse({"status": "success", "sale_id": transaction.id, "print_error": print_error})


@login_required
def reprint_receipt(request, transaction_id):
    if request.method != "POST":
        return JsonResponse({"status": "failed", "msg": "Invalid request method."})
    shop, _ = get_user_shop_and_branch(request)
    transaction = Transaction.objects.filter(id=transaction_id, shop=shop).first()
    if not transaction:
        return JsonResponse({"status": "failed", "msg": "Transaction not found."})
    try:
        printed = print_transaction_receipt(transaction, transaction.user or request.user, reprint=True)
    except Exception as e:
        return JsonResponse({"status": "failed", "msg": str(e)})
    if not printed:
        return JsonResponse({"status": "failed", "msg": "This printer type cannot be printed from the server."})
    return JsonResponse({"status": "success"})


@login_required
def receipt_settings_view(request):
    shop, user_branch = get_user_shop_and_branch(request)

    branch = user_branch
    if request.user.is_superuser:
        branch_id = request.GET.get("branch_id")
        if branch_id:
            branch = Branch.objects.filter(id=branch_id, shop=shop).first()

    if not branch:
        messages.error(request, "No branch found for your account.")
        return redirect("home")

    settings_obj, _ = ReceiptSettings.objects.get_or_create(branch=branch)

    if request.method == "POST":
        form = ReceiptSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, f"Receipt format updated for {branch.name}!")
            return redirect("receipt-settings")
    else:
        form = ReceiptSettingsForm(instance=settings_obj)

    return render(request, "posApp/receipt_settings.html", {
        "form": form,
        "branch": branch,
    })

@login_required
def printer_settings(request):
    shop, _ = get_user_shop_and_branch(request)
    settings, created = PrinterSettings.objects.get_or_create(shop=shop)
    if request.method == "POST":
        settings.printer_name = request.POST.get("printer_name", "")
        settings.paper_size = request.POST.get("paper_size", "80")
        settings.auto_print = bool(request.POST.get("auto_print"))
        settings.margin_top = int(request.POST.get("margin_top") or 5)
        settings.margin_bottom = int(request.POST.get("margin_bottom") or 5)
        settings.header_text = request.POST.get("header_text", "")
        settings.footer_text = request.POST.get("footer_text", "")
        settings.connection_type = request.POST.get("connection_type", "system")
        settings.ble_service_uuid = request.POST.get("ble_service_uuid", "").strip()
        settings.ble_characteristic_uuid = request.POST.get("ble_characteristic_uuid", "").strip()
        settings.last_updated_by = request.user
        settings.save()
        messages.success(request, "Printer settings saved successfully!")
        return redirect("printer-settings")
    return render(request, "posApp/printer_settings.html", {
        "settings": settings,
        "printers": list_printers(),      # Windows printers + Bluetooth COM ports
    })


@login_required
def delete_sale(request):
    resp = {'status': 'failed', 'msg': ''}
    sale_id = request.POST.get('id')
    shop, _ = get_user_shop_and_branch(request)

    try:
        transaction = Transaction.objects.filter(id=sale_id, shop=shop).first()
        if transaction:
            products = transaction.items.all() if hasattr(transaction, 'items') else []
            product_details = ", ".join([f"{item.product.name}({item.qty})" for item in products])

            total_amount = transaction.total if hasattr(transaction, 'total') else None

            
            SaleDeletionLog.objects.create(
                user=request.user,
                transaction_id=transaction.id,
                product_details=product_details,
                total_amount=total_amount,
                reason="Deleted via POS interface"
            )

            transaction.delete()

            resp['status'] = 'success'
            messages.success(request, 'Sale record deleted.')
        else:
            resp['msg'] = "Sale not found or unauthorized"
    except Exception as e:
        resp['msg'] = str(e)

    return JsonResponse(resp)

@login_required
def deleted_sales_report(request):
    
    shops = Shop.objects.all()
    branches = Branch.objects.all()

    logs = SaleDeletionLog.objects.select_related('user', 'transaction').order_by('-deleted_at')

    shop_id = request.GET.get('shop')
    branch_id = request.GET.get('branch')

    if shop_id:
        logs = [log for log in logs if log.transaction and log.transaction.shop_id == int(shop_id)]
    if branch_id:
        logs = [log for log in logs if log.transaction and log.transaction.branch_id == int(branch_id)]

    context = {
        'logs': logs,
        'shops': shops,
        'branches': branches,
        'selected_shop': shop_id,
        'branch_id': branch_id,
    }
    return render(request, 'posApp/deleted_sales_report.html', context)

def base_context(request):
    if request.user.is_authenticated:
        try:
            sub = request.user.usersubscription
            return {
                "remaining_days": sub.remaining_days(),
                "subscription_active": sub.has_active_subscription()
            }
        except:
            return {}
    return {}

@login_required
def product_list(request):
    shop, _ = get_user_shop_and_branch(request)
    products = Products.objects.filter(shop=shop)
    return render(request, 'posApp/product_list.html', {'products': products})

@login_required
def shop_list(request):
    profile = UserProfile.objects.get(user=request.user)
    shops = Shop.objects.filter(owner=profile)
    return render(request, 'posApp/shop_list.html', {'shops': shops})


@login_required
def change_password_first_login(request):
    if request.method == "POST":
        new_password1 = request.POST.get("new_password1")
        new_password2 = request.POST.get("new_password2")
        if new_password1 != new_password2:
            messages.error(request, "Passwords do not match")
        else:
            request.user.set_password(new_password1)
            request.user.must_change_password = False
            request.user.save()
            messages.success(request, "Password changed successfully")
            return redirect("home-page")
    return render(request, "posApp/change_password.html")


@login_required
def stock_page(request):
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        return redirect("dashboard")  

    products = Products.objects.filter(shop=shop, branch=user_branch).order_by("name")

    return render(request, "posApp/stock.html", {
        "products": products,
        "branch": user_branch,
        "shop": shop
    })

def department_required(page_name):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            profile = getattr(request.user, 'userprofile', None)
            if profile and profile.department and page_name in profile.department.allowed_pages:
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("You don't have access to this page")
        return _wrapped_view
    return decorator

@login_required
def manage_departments(request):
    departments = Department.objects.all()

    if request.method == "POST":
        form = DepartmentForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('manage-departments')
    else:
        form = DepartmentForm()

    return render(request, "posApp/manage_departments.html", {
        "departments": departments,
        "form": form
    })
@login_required
def update_department_users(request, dept_id):
    dept = Department.objects.get(id=dept_id)
    if request.method == "POST":
        selected_users = request.POST.getlist('users')
        
        for profile in UserProfile.objects.filter(department=dept):
            profile.department = None
            profile.save()
        
        for user_id in selected_users:
            user = User.objects.get(id=user_id)
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.department = dept
            profile.save()
    return redirect("manage-departments") 

@login_required
def update_user_rights(request, user_id):
    user = User.objects.get(id=user_id)
    profile, created = UserProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        rights = {
            "can_view_sales": bool(request.POST.get("can_view_sales")),
            "can_manage_stock": bool(request.POST.get("can_manage_stock")),
            "can_view_reports": bool(request.POST.get("can_view_reports")),
        }
        profile.access_rights = rights
        profile.save()
        return redirect("manage-departments")

    return render(request, "posApp/update_user_rights.html", {"user": user, "profile": profile})

from functools import wraps
from django.http import HttpResponseForbidden

from django.core.exceptions import PermissionDenied
from functools import wraps

def department_required(page_name=None, required_right=None):
    
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user

           
            if not user.is_authenticated:
                raise PermissionDenied("User must be logged in.")
            if page_name:
                if not hasattr(user, 'department') or not user.department.has_access_to(page_name):
                    raise PermissionDenied(f"Access to {page_name} denied for department.")

            if required_right:
                if not user.has_perm(required_right):
                    raise PermissionDenied(f"Missing required permission: {required_right}")

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

@login_required
@require_POST
def log_expense(request):
    try:
        shop, branch = get_user_shop_and_branch(request)
        if not shop or not branch:
            return JsonResponse({"status": "error", "message": "Shop or branch not found for this user"})

        data = json.loads(request.body)

        currency = (data.get("currency") or "USD").strip()
        if currency not in shop_currencies(shop, branch):
            return JsonResponse({"status": "error", "message": f"Unknown currency: {currency}"})

        try:
            amount = parse_amount(data.get("amount"))
        except ValueError as e:
            return JsonResponse({"status": "error", "message": str(e)})
        if amount <= 0:
            return JsonResponse({"status": "error", "message": "Amount must be more than 0."})

        reason = (data.get("reason") or "").strip()
        if not reason:
            return JsonResponse({"status": "error", "message": "Reason is required."})

        Expense.objects.create(
            amount=amount,
            reason=reason,
            currency=currency,
            created_by=request.user,
            shop=shop,
            branch=branch,
        )
        return JsonResponse({"status": "success"})

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})

def sync_shift_currencies(shift):
    existing = set(shift.currency_lines.values_list("currency", flat=True))
    missing = [c for c in shop_currencies(shift.shop, shift.branch) if c not in existing]
    if missing:
        ShiftCurrency.objects.bulk_create(
            [ShiftCurrency(shift=shift, currency=c, opening_amount=Decimal("0.00")) for c in missing],
            ignore_conflicts=True,   # safe if two requests race
        )

@login_required
@subscription_required
@require_POST
def open_shift(request):
    try:
        shop, branch = get_user_shop_and_branch(request)
        if not shop or not branch:
            return JsonResponse({"status": "error", "message": "Shop or branch not found for this user"})

        if Shift.objects.filter(user=request.user, shop=shop, branch=branch, is_closed=False).exists():
            return JsonResponse({
                "status": "error",
                "message": "You already have an open shift. Close it before opening a new one.",
            })

        data = json.loads(request.body)
        amounts = data.get("amounts") or {}

        required = shop_currencies(shop, branch)
        missing = [c for c in required if str(amounts.get(c, "")).strip() == ""]
        if missing:
            return JsonResponse({
                "status": "error",
                "message": "Enter an opening amount for: " + ", ".join(missing) + " (use 0 if none).",
            })

        parsed = {}
        for cur in required:
            try:
                parsed[cur] = parse_amount(amounts[cur])
            except ValueError as e:
                return JsonResponse({"status": "error", "message": f"{cur}: {e}"})

        with db_transaction.atomic():
            shift = Shift.objects.create(
                user=request.user,
                shop=shop,
                branch=branch,
                money_left=parsed.get("USD", Decimal("0")),   
            )

            try:
                with db_transaction.atomic():
                    take_stock_snapshot(shift, "opening")
            except Exception:
                logger.exception("Opening stock snapshot failed")

            for cur, amt in parsed.items():
                ShiftCurrency.objects.get_or_create(
                    shift=shift, currency=cur, defaults={"opening_amount": amt}
                )

        return JsonResponse({"status": "success", "message": "Shift opened", "shift_id": shift.id})

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})



ARCHIVE_DAYS = 7       
CENT = Decimal("0.01")


@login_required
@subscription_required
@require_POST
def close_shift(request):
    try:
        shop, branch = get_user_shop_and_branch(request)
        data = json.loads(request.body)
        counted = data.get("amounts") or {}

        with db_transaction.atomic():
            shift = (
                Shift.objects.select_for_update()
                .filter(user=request.user, shop=shop, branch=branch, is_closed=False)
                .order_by("-opened_at")
                .first()
            )
            if not shift:
                return JsonResponse({"status": "error", "message": "No open shift found."})

            now = timezone.now()
            sales_by_cur = {
                row["currency"]: row["total"] or Decimal("0.00")
                for row in Transaction.objects.filter(shift=shift)
                .values("currency").annotate(total=Sum("grand_total"))
            }
            expenses_by_cur = {
                row["currency"]: row["total"] or Decimal("0.00")
                for row in Expense.objects.filter(
                    shop=shop, branch=branch, created_by=request.user,
                    created_at__range=(shift.opened_at, now),
                ).values("currency").annotate(total=Sum("amount"))
            }

            lines = {l.currency: l for l in shift.currency_lines.all()}
            for cur in set(sales_by_cur) | set(expenses_by_cur):
                if cur not in lines:
                    lines[cur] = ShiftCurrency(shift=shift, currency=cur, opening_amount=Decimal("0.00"))
            required = set(shift_close_currencies(shift))
            missing = [c for c in required if str(counted.get(c, "")).strip() == ""]
            if missing:
                return JsonResponse({
                    "status": "error",
                    "message": "Enter the counted amount for: " + ", ".join(sorted(missing, key=currency_sort_key)),
                })

            for cur, line in lines.items():
                if cur in required:
                    try:
                        line.closing_amount = parse_amount(counted[cur])
                    except ValueError as e:
                        return JsonResponse({"status": "error", "message": f"{cur}: {e}"})
                else:
                    line.closing_amount = Decimal("0.00")

                line.sales_total = sales_by_cur.get(cur, Decimal("0.00"))
                line.expenses_total = expenses_by_cur.get(cur, Decimal("0.00"))
                line.expected_amount = line.opening_amount + line.sales_total - line.expenses_total
                line.difference = line.closing_amount - line.expected_amount
                line.save()

            shift.actual_money = lines["USD"].closing_amount if "USD" in lines else Decimal("0.00")
            shift.total_sales = Transaction.objects.filter(shift=shift).aggregate(t=Sum("grand_total_base"))["t"] or 0
            shift.total_expenses = expenses_by_cur.get("USD", Decimal("0.00"))
            shift.closed_at = now
            shift.is_closed = True
            shift.save()

            try:
                take_stock_snapshot(shift, "closing")
            except Exception:
                logger.exception("Closing stock snapshot failed")

        return JsonResponse({"status": "success", "message": "Shift closed successfully!"})

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)})


def _legacy_line(shift):
    opening = shift.money_left or Decimal("0.00")
    sales = shift.total_sales or Decimal("0.00")
    expenses = shift.total_expenses or Decimal("0.00")
    expected = opening + sales - expenses
    closing = shift.actual_money or Decimal("0.00")
    return SimpleNamespace(
        currency="USD", opening_amount=opening, sales_total=sales, expenses_total=expenses,
        expected_amount=expected, closing_amount=closing, difference=closing - expected,
    )

from collections import OrderedDict  

def _parse_date(value, default):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def _statement_branch(request, shop, user_branch, branch_id):
    branch = user_branch
    if request.user.is_superuser and branch_id:
        branch = Branch.objects.filter(id=branch_id, shop=shop).first()
    return branch


@login_required
@permission_required('view_income_statement')
def income_statement(request):
    shop, user_branch = get_user_shop_and_branch(request)
    if not shop:
        messages.error(request, "Your account is missing business details.")
        return redirect("login")

    today = timezone.localdate()
    start = _parse_date(request.GET.get("start_date"), today.replace(day=1))
    end = _parse_date(request.GET.get("end_date"), today)
    if start > end:
        start, end = end, start

    selected_branch_id = request.GET.get("branch_id") if request.user.is_superuser else None
    branch = _statement_branch(request, shop, user_branch, selected_branch_id)

    statement = build_income_statement(shop, branch, start, end, today)

    entries = IncomeEntry.objects.filter(shop=shop, date__range=(start, end))
    if branch:
        entries = entries.filter(branch=branch)

    context = {
        "s": statement,
        "entries": entries.select_related("created_by")[:200],
        "entry_types": IncomeEntry.TYPES,
        "currencies": shop_currencies(shop, branch or user_branch),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "branch_name": branch.name if branch else "All branches",
        "selected_branch_id": branch.id if (branch and request.user.is_superuser) else "",
        "all_branches": Branch.objects.filter(shop=shop) if request.user.is_superuser else None,
    }
    return render(request, "posApp/income_statement.html", context)


@login_required
@require_POST
@permission_required('view_income_statement')
def add_income_entry(request):
    shop, user_branch = get_user_shop_and_branch(request)
    branch = _statement_branch(request, shop, user_branch, request.POST.get("branch_id"))

    entry_type = request.POST.get("entry_type", "")
    labels = dict(IncomeEntry.TYPES)
    if entry_type not in labels:
        return JsonResponse({"status": "failed", "msg": "Choose what you are adding."})

    name = (request.POST.get("name") or "").strip() or labels[entry_type]

    try:
        amount = Decimal(request.POST.get("amount") or "0").quantize(Decimal("0.01"))
    except Exception:
        return JsonResponse({"status": "failed", "msg": "Enter a valid amount."})
    if amount <= 0:
        return JsonResponse({"status": "failed", "msg": "Amount must be greater than 0."})

    currency = (request.POST.get("currency") or "USD").upper().strip()
    entry_date = _parse_date(request.POST.get("date"), timezone.localdate())
    rate = rate_at(shop, branch, currency, end_of_day(entry_date))
    if rate is None:
        return JsonResponse({"status": "failed", "msg": f"No exchange rate found for {currency}."})

    IncomeEntry.objects.create(
        shop=shop,
        branch=branch,
        entry_type=entry_type,
        name=name,
        amount=amount,
        currency=currency,
        amount_usd=(amount / rate).quantize(Decimal("0.01")),
        date=entry_date,
        note=(request.POST.get("note") or "").strip()[:255],
        created_by=request.user,
    )
    return JsonResponse({"status": "success"})


@login_required
@require_POST
@permission_required('view_income_statement')
def delete_income_entry(request):
    shop, _ = get_user_shop_and_branch(request)
    deleted, _ = IncomeEntry.objects.filter(id=request.POST.get("id"), shop=shop).delete()
    if not deleted:
        return JsonResponse({"status": "failed", "msg": "Entry not found."})
    return JsonResponse({"status": "success"})

def _is_zero_line(l):
    """True when every figure on a shift currency line is zero."""
    return all(
        (v or 0) == 0
        for v in (
            l.opening_amount, l.sales_total, l.expenses_total,
            l.expected_amount, l.closing_amount, l.difference,
        )
    )


@login_required
@permission_required('view_shift_report')
def shift_report(request):
    shop, branch = get_user_shop_and_branch(request)
    username_filter = request.GET.get("username", "").strip()
    view_all = request.GET.get("view") == "all"

    shifts = (
        Shift.objects.filter(shop=shop,branch=branch, is_closed=True)
        .select_related("user")
        .prefetch_related("currency_lines")
        .order_by("-opened_at")
    )
    if branch:
        shifts = shifts.filter(branch=branch)
    if username_filter:
        shifts = shifts.filter(user__username__icontains=username_filter)

    rate_filter = {"shop": shop, "is_active": True}
    if branch:
        rate_filter["branch"] = branch
    active_currencies = {
        c.upper() for c in ExchangeRate.objects.filter(**rate_filter).values_list("currency", flat=True)
    }
    active_currencies.add("USD")

    days = OrderedDict()
    for shift in shifts:
        day = timezone.localtime(shift.opened_at).date()
        bucket = days.setdefault(day, {"date": day, "shifts": [], "totals": {}})

        lines = sorted(shift.currency_lines.all(), key=lambda l: currency_sort_key(l.currency))
        lines = [
            l for l in lines
            if l.currency.upper() in active_currencies or not _is_zero_line(l)
        ]
        shift.lines = lines or [_legacy_line(shift)]
        bucket["shifts"].append(shift)

        for l in shift.lines:
            t = bucket["totals"].setdefault(l.currency, {
                "currency": l.currency, "opening": Decimal("0"), "sales": Decimal("0"),
                "expenses": Decimal("0"), "expected": Decimal("0"),
                "closing": Decimal("0"), "difference": Decimal("0"),
            })
            t["opening"] += l.opening_amount
            t["sales"] += l.sales_total
            t["expenses"] += l.expenses_total
            t["expected"] += l.expected_amount
            t["closing"] += l.closing_amount or 0
            t["difference"] += l.difference or 0

    today = timezone.localdate()
    day_list = []
    for bucket in days.values():
        bucket["totals"] = sorted(bucket["totals"].values(), key=lambda t: currency_sort_key(t["currency"]))
        bucket["is_today"] = bucket["date"] == today
        day_list.append(bucket)

    today_day = next((d for d in day_list if d["is_today"]), None)
    past_days = [d for d in day_list if not d["is_today"]]
    hidden_count = 0
    if not view_all and len(past_days) > ARCHIVE_DAYS:
        hidden_count = len(past_days) - ARCHIVE_DAYS
        past_days = past_days[:ARCHIVE_DAYS]

    return render(request, "posApp/shift_report.html", {
        "today": today,
        "today_day": today_day,
        "past_days": past_days,
        "hidden_count": hidden_count,
        "view_all": view_all,
        "username_filter": username_filter,
    })

@login_required
def add_shop(request):
    profile = UserProfile.objects.get(user=request.user)
    if request.method == "POST":
        form = ShopForm(request.POST)
        if form.is_valid():
            shop = form.save(commit=False)
            shop.owner = profile
            shop.save()
            return redirect('shop_list')
    else:
        form = ShopForm()
    return render(request, 'posApp/add_shop.html', {'form': form})

@login_required
def toggle_shop_status(request, shop_id):
    shop = get_object_or_404(Shop, id=shop_id, owner__user=request.user)
    shop.is_active = not shop.is_active
    shop.save()
    return redirect('shop_list')

@login_required
def branch_list(request):
    # Get the shop for the current user
    shop, _ = get_user_shop_and_branch(request)
    if shop is None:
        messages.error(request, "You don't have a shop.")
        return redirect('shop-list')

    branches = Branch.objects.filter(shop=shop)

    # Branch form for the modal
    branch_form = BranchForm()
    branch_form.fields.pop('shop', None)  # hide shop field

    return render(request, 'posApp/branch_list.html', {
        'shop': shop,
        'branches': branches,
        'branch_form': branch_form
    })


@login_required
@subscription_required
def create_branch(request):
    shop, _ = get_user_shop_and_branch(request)
    if shop is None:
        return JsonResponse({'status': 'error', 'message': "No shop assigned."})

    if request.method == "POST":
        form = BranchForm(request.POST)
        form.fields.pop('shop', None)  # ignore shop field
        if form.is_valid():
            branch = form.save(commit=False)
            branch.shop = shop
            branch.save()
            return JsonResponse({
                'status': 'success',
                'message': "Branch created successfully.",
                'branch': {'id': branch.id, 'name': branch.name, 'location': branch.location, 'is_active': branch.is_active}
            })
        else:
            return JsonResponse({'status': 'error', 'message': form.errors})
    return JsonResponse({'status': 'error', 'message': 'Invalid request'})


def _branch_error_text(form):
    """Turn form errors into one readable line."""
    parts = []
    for field, errs in form.errors.items():
        parts.append(f"{field}: " + ", ".join(errs))
    return " | ".join(parts) or "Invalid data."


@login_required
@permission_required('view_branch')
@subscription_required
def edit_branch(request, branch_id):
    """GET returns the branch's current values (used to pre-fill the edit form),
    POST saves the changes."""
    shop, _ = get_user_shop_and_branch(request)
    if shop is None:
        return JsonResponse({'status': 'error', 'message': "No shop assigned."}, status=400)

    branch = get_object_or_404(Branch, pk=branch_id, shop=shop)

    if request.method == "GET":
        return JsonResponse({
            'status': 'success',
            'branch': {
                'id': branch.id,
                'name': branch.name,
                'location': branch.location,
                'is_active': branch.is_active,
            }
        })

    if request.method == "POST":
        name = (request.POST.get('name') or '').strip()
        if not name:
            return JsonResponse({'status': 'error', 'message': "Branch name is required."})

        if Branch.objects.filter(shop=shop, name__iexact=name).exclude(pk=branch.pk).exists():
            return JsonResponse({'status': 'error', 'message': f"Another branch is already called '{name}'."})

        branch.name = name
        branch.location = (request.POST.get('location') or '').strip()
        branch.is_active = request.POST.get('is_active') in ('on', 'true', 'True', '1')
        branch.save()

        return JsonResponse({
            'status': 'success',
            'message': "Branch updated successfully.",
            'branch': {'id': branch.id, 'name': branch.name,
                       'location': branch.location, 'is_active': branch.is_active},
        })

    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=405)


_BRANCH_CASCADE_OK = {"receiptsettings", "receiptsequence", "exchangerate"}


@login_required
@permission_required('view_branch')
@subscription_required
@require_POST
def delete_branch(request, branch_id):
    """Delete a branch only when nothing important depends on it.
    A branch that has products, sales, users, etc. must be deactivated instead,
    because deleting it would wipe all of that history."""
    shop, _ = get_user_shop_and_branch(request)
    if shop is None:
        return JsonResponse({'status': 'error', 'message': "No shop assigned."}, status=400)

    branch = get_object_or_404(Branch, pk=branch_id, shop=shop)

    if Branch.objects.filter(shop=shop).count() <= 1:
        return JsonResponse({'status': 'error',
                             'message': "You cannot delete the only branch of a shop."})

    blockers = []
    for rel in Branch._meta.related_objects:
        model = rel.related_model
        if model._meta.model_name in _BRANCH_CASCADE_OK:
            continue
        count = model._default_manager.filter(**{rel.field.name: branch}).count()
        if count:
            blockers.append(f"{count} {model._meta.verbose_name_plural}")

    if blockers:
        return JsonResponse({
            'status': 'error',
            'message': ("This branch cannot be deleted because it still has "
                        + ", ".join(blockers)
                        + ". Edit the branch and untick Active to deactivate it instead."),
        })

    name = branch.name
    with transaction.atomic():
        ReceiptSettings.objects.filter(branch=branch).delete()
        ReceiptSequence.objects.filter(branch=branch).delete()
        ExchangeRate.objects.filter(branch=branch).delete()
        branch.delete()

    return JsonResponse({'status': 'success', 'message': f"Branch '{name}' deleted."})


from django.http import JsonResponse

from django.http import JsonResponse
from django.urls import reverse
from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required


def export_excel(request, page):
    shop, _ = get_user_shop_and_branch(request)

    if page == 'products':
        data = Products.objects.filter(shop=shop).values(
            'id', 'category__name', 'name','stock', 'unit', 'description', 'shop__name', 'status'
        )
        columns = {
            'id': 'Product ID',
            'category__name': 'Category',
            'name': 'Product',
            'stock': 'Quantity',
            'unit': 'Sold By',
            'description': 'Description',
            'shop__name': 'Shop',
            'status': 'Status'
        }
        filename = 'products.xlsx'

        df = pd.DataFrame(data)
        df.rename(columns=columns, inplace=True)

        unit_map = dict(Products.UNIT_CHOICES)
        df['Sold By'] = df['Sold By'].map(unit_map)
        df['Status'] = df['Status'].map({True: 'Active', False: 'Inactive'})
        
        filename = 'products.xlsx'
    elif page == 'branches':
        data = Branch.objects.filter(shop=shop).values('name', 'location', 'shop__name', 'is_active')
        columns = {'name': 'Branch Name', 'location': 'Location', 'shop__name': 'Shop', 'is_active': 'Status'}
        filename = 'branches.xlsx'
    elif page == 'categories':
        data = Category.objects.filter(shop=shop).values('name', 'description', 'status')
        columns = {'name': 'Category Name', 'description': 'Description', 'status': 'Status'}
        filename = 'categories.xlsx'
    elif page == 'supplies':
        data = Supplier.objects.filter(shop=shop).values('id', 'name', 'contact', 'email', 'address')
        columns = {'id': '#', 'name': 'Name', 'contact': 'Contact', 'email': 'Email', 'address': 'Address'}
        filename = 'supplies.xlsx'

    else:
        return HttpResponse("Invalid export type", status=400)

    df = pd.DataFrame(data)
    df.rename(columns=columns, inplace=True)

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename={filename}'
    df.to_excel(response, index=False)
    return response

def export_pdf(request, page):
    shop, _ = get_user_shop_and_branch(request)

    if page == 'products':
        items = Products.objects.filter(shop=shop)
        headers = ['Product ID', 'Category', 'Product', 'Sold By', 'Description', 'Shop', 'Status']
        unit_map = dict(Products.UNIT_CHOICES)
        rows = [[
            p.id,
            p.category.name if p.category else 'Not Set',
            p.name,
            unit_map.get(p.unit, p.unit),
            p.description,
            p.shop.name,
            'Active' if p.status else 'Inactive'
        ] for p in items]
        filename = 'products.pdf'
    elif page == 'branches':
        items = Branch.objects.filter(shop=shop)
        headers = ['Branch Name', 'Location', 'Shop', 'Status']
        rows = [[b.name, b.location, b.shop.name, 'Active' if b.is_active else 'Inactive'] for b in items]
        filename = 'branches.pdf'
    elif page == 'supplies':
        items = Supplier.objects.filter(shop=shop)
        headers = ['Supplier Name', 'Contact', 'Email', 'Address']
        rows = [[s.name, s.contact, s.email, s.address] for s in items]
        filename =  'suppliers.pdf'


    elif page == 'categories':
        items = Category.objects.filter(shop=shop)
        headers = ['Category Name', 'Description', 'Status']
        rows = [[c.name, c.description, 'Active' if c.status else 'Inactive'] for c in items]
        filename = 'categories.pdf'
    else:
        return HttpResponse("Invalid export type", status=400)

    class PDF(FPDF):
        def header(self):
            self.set_font('Arial', 'B', 12)
            self.cell(0, 10, f'{page.capitalize()} List', ln=True, align='C')
            self.ln(10)

        def table(self, headers, rows):
            self.set_font('Arial', 'B', 10)
            for header in headers:
                self.cell(45, 10, header, 1)
            self.ln()
            self.set_font('Arial', '', 10)
            for row in rows:
                for cell in row:
                    self.cell(45, 10, str(cell), 1)
                self.ln()

    pdf = PDF()
    pdf.add_page()
    pdf.table(headers, rows)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename={filename}'
    response.write(pdf.output(dest='S').encode('latin1'))
    return response
from django.shortcuts import render, redirect
from openpyxl import load_workbook
from .forms import ExcelUploadForm
from .models import Products, Category, Shop

@login_required
def import_products_excel(request):
    if request.method == 'POST':
        form = ExcelUploadForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['excel_file']
            wb = load_workbook(excel_file)
            ws = wb.active

            for row in ws.iter_rows(min_row=2, values_only=True):
                product_id, category_name, name, unit, description, shop_name, status = row

                category, _ = Category.objects.get_or_create(name=category_name)
                shop = Shop.objects.filter(name=shop_name).first()

                if shop:
                    Products.objects.update_or_create(
                        id=product_id,
                        defaults={
                            'category': category,
                            'name': name,
                            'unit': unit,
                            'description': description,
                            'shop': shop,
                            'status': status,
                        }
                    )
            return redirect('product-list')
    else:
        form = ExcelUploadForm()
    return render(request, 'import_products.html', {'forms': form})


from .models import SubscriptionPlan, Subscription


@login_required
def subscription_plans(request):
    plans = SubscriptionPlan.objects.all()
    return render(request, "posApp/plans.html", {"plans": plans})

@login_required
def subscribe(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, id=plan_id)
    user = request.user
    today = timezone.now().date()

    # Fetch the latest subscription
    subscription = Subscription.objects.filter(user=user).last()

    if subscription:
       
        subscription.plan = plan
        subscription.start_date = today
        subscription.end_date = today + timedelta(days=plan.duration_days)
        subscription.next_billing_date = subscription.end_date
        subscription.is_active = True
        subscription.save()
        messages.success(request, f" Your subscription has been updated to {plan.name}!")
    else:
        Subscription.objects.create(
            user=user,
            plan=plan,
            start_date=today,
            end_date=today + timedelta(days=plan.duration_days),
            next_billing_date=today + timedelta(days=plan.duration_days),
            is_active=True
        )
        messages.success(request, f" You have subscribed to {plan.name} successfully!")

    return redirect("my-subscription")

@login_required
def my_subscription(request):
   
    subscription = Subscription.objects.filter(user=request.user).order_by("-end_date").first()
    return render(request, "posApp/my_subscription.html", {"subscription": subscription})

from .models import BillingMethod


@login_required
def add_billing_method(request):
    if request.method == "POST":
        provider = request.POST.get("provider")
        account_number = request.POST.get("account_number")

        BillingMethod.objects.create(
            user=request.user,
            provider=provider,
            account_number=account_number,
            is_default=True,
        )
        messages.success(request, "✅ Billing method added successfully!")

        next_url = request.GET.get("next", "subscription-plans")
        return redirect(next_url)

    return render(request, "posApp/add_billing.html")
from django.views.decorators.http import require_POST, require_http_methods

@require_POST
@login_required
def toggle_auto_renew(request):
    subscription = Subscription.objects.filter(user=request.user).last()
    if subscription:
        subscription.auto_renew = not subscription.auto_renew
        subscription.save()
        if subscription.auto_renew:
            messages.success(request, "✅ Auto-renew enabled")
        else:
            messages.info(request, "🔄 Auto-renew disabled")
    return redirect("my-subscription")


@staff_member_required
def manage_subscriptions(request):
    subscriptions = Subscription.objects.select_related("user", "plan").all()
    users = UserProfile.objects.select_related('shop', 'branch').all()
    return render(request, "posApp/manage_subscriptions.html", {"subscriptions": subscriptions})

@staff_member_required
def edit_subscription(request, subscription_id):  # <- match the URL
    subscription = get_object_or_404(Subscription, pk=subscription_id)
    if request.method == "POST":
        custom_name = request.POST.get("custom_name", "").strip()
        custom_days = request.POST.get("custom_duration_days")
        custom_price = request.POST.get("custom_price")
        start_date = request.POST.get("start_date")
        end_date = request.POST.get("end_date")

        if not start_date or not end_date:
            return JsonResponse({"success": False, "message": "Start and end date are required."}, status=400)

        try:
            subscription.custom_name = custom_name or None
            subscription.custom_duration_days = int(custom_days) if custom_days else None
            subscription.custom_price = float(custom_price) if custom_price else None
            subscription.start_date = parse_date(start_date)
            subscription.end_date = parse_date(end_date)
            subscription.next_billing_date = subscription.end_date
            subscription.is_active = True
            subscription.save()
            return JsonResponse({"success": True, "message": "Subscription updated successfully!"})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"Error updating subscription: {e}"}, status=400)

    return JsonResponse({"success": False, "message": "Invalid request."}, status=400)



PAID_STATES = {"paid", "awaiting delivery", "delivered"}
FAILED_STATES = {"cancelled", "failed", "refunded", "disputed"}


@login_required
def subscribe(request, plan_id):
   
    plan = get_object_or_404(SubscriptionPlan, id=plan_id)
    return redirect("checkout_plan", plan_id=plan.id)     

@login_required
def checkout_plan(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, id=plan_id)
    return render(request, "posApp/checkout_plan.html", {
        "plan": plan,
        "fake_mode": getattr(settings, "PAYNOW_FAKE", False),
    })

@staff_member_required
def plan_payments_list(request):
    payments = PlanPayment.objects.select_related("user", "plan").order_by("-created_at")[:300]
    return render(request, "posApp/plan_payments.html", {"payments": payments})

def _activate(payment):
    """Give the plan. Safe to call twice: only the first call does anything."""
    with db_transaction.atomic():
        p = PlanPayment.objects.select_for_update().get(pk=payment.pk)
        if p.activated:
            return
        today = timezone.now().date()
        end = today + timedelta(days=p.plan.duration_days)
        sub = Subscription.objects.filter(user=p.user).last()
        if sub:
            sub.plan = p.plan
            sub.start_date = today
            sub.end_date = end
            sub.next_billing_date = end
            sub.is_active = True
            sub.save()
        else:
            Subscription.objects.create(
                user=p.user, plan=p.plan, start_date=today,
                end_date=end, next_billing_date=end, is_active=True,
            )
        p.status = "paid"
        p.activated = True
        p.paid_at = timezone.now()
        p.save(update_fields=["status", "activated", "paid_at"])


def _refresh(payment):
    """Ask the payment provider what happened and update our record."""
    if payment.status != "pending" or not payment.poll_url:
        return payment
    try:
        state = check_status(payment.poll_url)
    except Exception:
        logger.exception("Payment status check failed for %s", payment.reference)
        return payment
    if state in PAID_STATES:
        _activate(payment)
    elif state in FAILED_STATES:
        payment.status = "cancelled" if state == "cancelled" else "failed"
        payment.save(update_fields=["status"])
    payment.refresh_from_db()
    return payment


def _new_payment(request, plan, method, phone=""):
    return PlanPayment.objects.create(
        user=request.user, plan=plan, method=method, phone=phone,
        amount=plan.price, reference=f"PLAN-{uuid.uuid4().hex[:12].upper()}",
    )


@login_required
@require_POST
def pay_ecocash(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, id=plan_id)
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        body = {}
    phone = normalise_phone(body.get("phone"))
    if not phone:
        return JsonResponse({"status": "failed", "msg": "Enter a valid EcoCash number, e.g. 0771234567."})

    payment = _new_payment(request, plan, "ecocash", phone)
    result = start_mobile_payment(request.user, payment.reference, plan.price, phone, "ecocash")
    if not result["success"]:
        payment.status = "failed"
        payment.save(update_fields=["status"])
        return JsonResponse({"status": "failed", "msg": f"Payment failed: {result['error']}"})

    payment.poll_url = result["poll_url"]
    payment.save(update_fields=["poll_url"])
    return JsonResponse({"status": "success", "payment_id": payment.id,
                         "msg": "Check your phone and enter your EcoCash PIN."})


@login_required
@require_POST
def pay_card(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, id=plan_id)
    payment = _new_payment(request, plan, "card")
    result = start_card_payment(request.user, payment.reference, plan.price)
    if not result["success"]:
        payment.status = "failed"
        payment.save(update_fields=["status"])
        return JsonResponse({"status": "failed", "msg": f"Payment failed: {result['error']}"})

    payment.poll_url = result["poll_url"]
    payment.redirect_url = result["redirect_url"]
    payment.save(update_fields=["poll_url", "redirect_url"])
    return JsonResponse({"status": "success", "redirect_url": result["redirect_url"]})


@login_required
def payment_status(request, payment_id):
    """The checkout page asks this every few seconds."""
    payment = get_object_or_404(PlanPayment, pk=payment_id, user=request.user)
    payment = _refresh(payment)
    return JsonResponse({"status": payment.status})


@login_required
def payment_return(request):
    """Where the browser lands after a card payment."""
    payment = PlanPayment.objects.filter(
        reference=request.GET.get("ref", ""), user=request.user).first()
    if not payment:
        messages.error(request, "Payment record not found.")
        return redirect("subscription-plans")
    payment = _refresh(payment)
    if payment.status == "paid":
        messages.success(request, f"🎉 Payment confirmed! You are now on {payment.plan.name}.")
        return redirect("my-subscription")
    if payment.status == "pending":
        messages.info(request, "⌛ Payment is still being processed. Refresh this page in a minute.")
        return redirect("my-subscription")
    messages.error(request, "❌ Payment was not successful. Please try again.")
    return redirect("subscription-plans")


@csrf_exempt
@require_POST
def paynow_result(request):
    """Paynow server-to-server notice. Its content is never trusted:
    we only use the reference, then ask Paynow ourselves for the real status."""
    payment = PlanPayment.objects.filter(reference=request.POST.get("reference", "")).first()
    if payment:
        _refresh(payment)
    return HttpResponse("ok")


@login_required
def fake_card_page(request, ref):
    """Stand-in for Paynow's card page. Only works while PAYNOW_FAKE is True."""
    if not getattr(settings, "PAYNOW_FAKE", False):
        raise Http404
    payment = get_object_or_404(PlanPayment, reference=ref, user=request.user)

    result = request.GET.get("result")
    if result in ("paid", "cancelled", "failed"):
        cache.set(f"fakepay:{ref}", result, 3600)
        return redirect(f"{reverse('payment-return')}?ref={ref}")

    return HttpResponse(f"""
        <div style="font-family:sans-serif;max-width:480px;margin:60px auto;text-align:center">
          <h3>Fake card page (testing only)</h3>
          <p>{payment.plan.name}: ${payment.amount}</p>
          <p>
            <a href="?result=paid" style="padding:10px 18px;background:#2e7d32;color:#fff;text-decoration:none">Pay successfully</a>
            <a href="?result=failed" style="padding:10px 18px;background:#c62828;color:#fff;text-decoration:none">Card declined</a>
            <a href="?result=cancelled" style="padding:10px 18px;background:#616161;color:#fff;text-decoration:none">Cancel</a>
          </p>
        </div>""")

def terms(request):
    return render(request, 'posApp/terms.html')

from rest_framework import viewsets
from .models import Item
from .serializers import ItemSerializer

class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.all()
    serializer_class = ItemSerializer

    from .models import Products, Transaction, SalesItem, UserProfile
from .serializers import ProductsSerializer, TransactionSerializer, SalesItemSerializer, UserProfileSerializer

class ProductsViewSet(viewsets.ModelViewSet):
    queryset = Products.objects.all()
    serializer_class = ProductsSerializer

class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer

class SalesItemViewSet(viewsets.ModelViewSet):
    queryset = SalesItem.objects.all()
    serializer_class = SalesItemSerializer

class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer


@login_required
@require_POST
def dismiss_po_alerts(request):
    """Hide today's purchase-order banner. It comes back automatically tomorrow."""
    from django.utils import timezone as dj_timezone
    request.session["po_alert_dismissed_on"] = dj_timezone.localdate().isoformat()
    return JsonResponse({"status": "success"})
