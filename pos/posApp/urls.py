from . import views
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic.base import RedirectView
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from posApp import views
from posApp import api_views
from posApp.api_views import dashboard_api, login_api

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ItemViewSet, ProductsViewSet, TransactionViewSet, SalesItemViewSet, UserProfileViewSet

router = DefaultRouter()
router.register(r'items', ItemViewSet)
router.register(r'products', ProductsViewSet)
router.register(r'transactions', TransactionViewSet)
router.register(r'sales', SalesItemViewSet)
router.register(r'profiles', UserProfileViewSet)

urlpatterns = [
    path('redirect-admin', RedirectView.as_view(url="/admin"),name="redirect-admin"),
    path('', views.home, name="home-page"),
    path('login/', auth_views.LoginView.as_view(template_name = 'posApp/login.html',redirect_authenticated_user=True), name="login"),
    path('erp-link/authorize/', views.erp_link_authorize, name='erp-link-authorize'),
    path('erp-link/requests/', views.erp_link_requests, name='erp-link-requests'),
    path('userlogin', views.login_user, name="login-user"),
    path('api/login/', views.login_use, name='api-login'),
    path('forgot-password/', views.forgot_password, name='forgot-password'),
    path('change-password/', views.change_password, name='change-password'),
    path('stock-attention/', views.stock_attention_view, name='stock_attention'),
    path('credits/<int:pk>/pay/', views.pay_credit, name='credit-pay'),
    path('logout', views.logoutuser, name="logout"),
    path('monthlysales', views.monthlysales, name="monthlysales"),
    path('print-job-status/', views.print_job_status, name='print-job-status'),
    path('print-job-retry/', views.print_job_retry, name='print-job-retry'),
    path('category', views.category, name="category-page"),
    path('manage_category', views.manage_category, name="manage_category-page"),
    path('save_category', views.save_category, name="save-category-page"),
    path('save_categry', views.save_categry, name="save-categry-page"),
    path('delete_category', views.delete_category, name="delete-category"),
    path('products', views.products, name="product-page"),
    path('manage_products', views.manage_products, name="manage_products-page"),
    path('save_product', views.save_product, name="save-product-page"),
    path('products/<int:product_id>/delete/', views.delete_product, name="delete-product"),
    path('api/login/', views.login_api, name='login_api'),
    path('pos', views.pos, name="pos-page"),
    path("api/exchange-rates/toggle/", views.toggle_exchange_rate, name="toggle-exchange-rate"),
    path("api/exchange-rates/delete/", views.delete_exchange_rate, name="delete-exchange-rate"),
    path('adjust-stock/', views.adjust_stock, name='adjust-stock'),
    path('stock-count/', views.stock_count_reports, name='stock-count-reports'),
    path('stock-summary/', views.stock_summary, name='stock-summary'),
    path('stock-count/report/<int:report_id>/', views.view_stock_count_report, name='view-stock-count-report'),
    path('stock_count/', views.stock_count, name='stock-count'),
    path('supplier/delete/<int:supplier_id>/', views.delete_supplier, name='delete_supplier'),
    path("stock/modal/", views.stock_modal_form, name="stock_modal_form"),
    path("stock/update/", views.update_stock, name="update_stock"),
    path('checkout-modal', views.checkout_modal, name="checkout-modal"),
    path('register/', views.register_user, name='register-user'),
    path('open-shift/', views.open_shift, name='open-shift'),
    path('close-shift/', views.close_shift, name='close-shift'),
    path('log-expense/', views.log_expense, name='log-expense'),
    path('shift-report/', views.shift_report, name='shift-report'),
    path('add-shop/', views.add_shop, name='add-shop'),
    path('printer-test/', views.printer_test, name='printer-test'),
    path('products/', views.product_list, name='product-list'),
    path('transfer_product',views.transfer_product, name='transfer_product'),
    path("import-products/", views.import_products, name="import-products"),
    path('product_transfer_report',views.product_transfer_report, name='product_transfer_report'),
    path('shops/', views.shop_list, name='shop_list'),
    path('shops/toggle/<int:shop_id>/', views.toggle_shop_status, name='toggle-shop-status'),
    path('branches/', views.branch_list, name='branch-list'),
    path('save-pos', views.save_pos, name="save-pos"),
    path('sales', views.salesList, name="sales-page"),
    path('receipt', views.receipt, name="receipt-modal"),
    path("receipt-settings/", views.receipt_settings_view, name="receipt-settings"),
    path('delete_sale', views.delete_sale, name="delete-sale"),
    path('reprint-receipt/<int:transaction_id>/', views.reprint_receipt, name='reprint-receipt'),
    path('deleted_sales_report/', views.deleted_sales_report, name="deleted_sales_report"),
    path("stock_report/", views.stock_report, name="stock-report"),
    path('trackproductreport/', views.stocktracker, name='stock-tracker-page'),
    path('users/', views.user_list, name='user-list'),
    path("receipt-lookup/", views.receipt_lookup, name="receipt_lookup"),
    path("strategy/bcg/", views.bcg_matrix, name="bcg_matrix"),
    path("strategy/ansoff/", views.ansoff_matrix, name="ansoff_matrix"),      
    path('stock-adjustment-report/', views.stock_adjustment_report, name='stock_adjustment_report'),
    path("api/income-statement/", views.income_statement, name="income-statement"),
    path("api/income-statement/add/", views.add_income_entry, name="add-income-entry"),
    path("api/income-statement/delete/", views.delete_income_entry, name="delete-income-entry"),
    path('users/<int:user_id>/edit/', views.edit_user, name='edit-user'),
    path('create-user-page/', views.create_user_page, name='create-user-page'),
    path('api/create-user/', views.create_user, name='create-user'),
    path('api/users/<int:user_id>/delete/', views.user_delete, name='user-delete'),
    path('departments/', views.manage_departments, name='manage-departments'),
    path('departments/update-users/<int:dept_id>/', views.update_department_users, name='update-department-users'),
    path("departments/update-rights/<int:user_id>/", views.update_user_rights, name="update-user-rights"),
    path('toggle-theme/', views.toggle_theme, name='toggle_theme'),
    path('export/excel/<str:page>/', views.export_excel, name='export_excel'),
    path('export/pdf/<str:page>/', views.export_pdf, name='export_pdf'),
    path('products/import/', views.import_products_excel, name='import-products-excel'),
    path('branches/add/', views.create_branch, name='create-branch-modal'),
    path('branches/<int:branch_id>/edit/', views.edit_branch, name='edit-branch'),
    path('branches/<int:branch_id>/delete/', views.delete_branch, name='delete-branch'),
    path('po-alerts/dismiss/', views.dismiss_po_alerts, name='dismiss-po-alerts'),
    path('', include(router.urls)),
    path("stock/", views.stock_page, name="stock_page"),
    path("api/import-stock-update/", views.import_stock_update, name="import-stock-update"),
    path('api/stock/bulk-upload/', views.bulk_stock_upload, name='bulk_stock_upload'),
    path('api/stock/download/', views.download_stock_excel, name='download_stock_excel'),
    path("suppliers/", views.supplier_page, name="supplier"),
    path("suppliers/add/", views.add_supplier, name="add_supplier"),
    path("product-report/", views.product_report, name="product-report"),
    path("purchase-orders/", views.purchase_orders, name="purchase_orders"),
    path("product-report/pdf/", views.export_product_report_pdf, name="export-product-report-pdf"),
    path("product-report/excel/", views.export_product_report_excel, name="export-product-report-excel"),
    path("cancel-transaction/", views.cancel_transaction, name="cancel_transaction"),
    path('api/get-exchange-rates/', views.api_get_exchange_rates, name='api-get-exchange-rates'),
    path("exchange-rates/", views.exchange_rate_list, name="exchange-rates"),
    path("exchange-rates-report/", views.exchange_rate_audit_log, name="exchange-rate-audit"),
    path("exchange-rates/update/", views.update_exchange_rate, name="update-exchange-rate"),
    path("exchange-rates/add/", views.add_exchange_rate, name="add-exchange-rate"),
    path('stock-history/', views.stock_update_history, name='stock-history'),
    path('credits/', views.credit_list, name='credit-list'),
    path('credits/modal/', views.credit_modal_form, name='credit-modal-form'),
    path('credits/pay/<int:pk>/', views.pay_credit, name='credit-pay'),
    path('api/stock-history/', views.stock_update_history, name='stock_update_history'),
    path("api/home/", views.home_api, name="home_api"),
    path('api-token-auth/', obtain_auth_token, name='api_token_auth'),
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('suspicious/short-sessions/', views.suspicious_short_sessions_view, name='suspicious_short_sessions'),
    path('suspicious/multiple-ips/', views.suspicious_multiple_ips_view, name='suspicious_multiple_ips'),
    path("api/mobile/login/", login_api),
    path("api/mobile/dashboard/", dashboard_api),
    path('api/mobile/categories/',          api_views.categories),
    path('api/mobile/categories/<int:pk>/', api_views.category_detail),

    path('admin/todays-logins/', views.todays_logins, name='todays_logins'),
    path('purchase-order/view/', views.purchase_order_view, name='purchase-order-view'),
    path('purchase/pdf/<int:order_id>/', views.download_purchase_order_pdf, name='purchasepdf'),
    path('terms/',views.terms, name="terms"),
    path("plans/", views.subscription_plans, name="subscription-plans"),
    path("subscribe/<int:plan_id>/", views.subscribe, name="subscribe"),
    path("my-subscription/", views.my_subscription, name="my-subscription"),
    path("billing/add/", views.add_billing_method, name="add-billing-method"),
    path("subscription/toggle-auto-renew/", views.toggle_auto_renew, name="toggle-auto-renew"),
    path("manage-subscriptions/", views.manage_subscriptions, name="manage_subscriptions"),
    path('api/category1/', views.category1, name='category-list'),
    path('api/products_api/', views.products_api, name='products_api'),
    path("api/pos/", views.pos_api, name="pos_api"),
    path("api/pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("printer-settings/", views.printer_settings, name="printer-settings"),
    path("edit-subscription/<int:subscription_id>/", views.edit_subscription, name="edit_subscription"), 
    path("subscribe/<int:plan_id>/", views.subscribe, name="subscribe"),
    path("checkout/<int:plan_id>/", views.checkout_plan, name="checkout_plan"),
    path("pay/ecocash/<int:plan_id>/", views.pay_ecocash, name="pay-ecocash"),
    path("pay/card/<int:plan_id>/", views.pay_card, name="pay-card"),
    path("pay/status/<int:payment_id>/", views.payment_status, name="payment-status"),
    path("pay/return/", views.payment_return, name="payment-return"),
    path("pay/paynow-result/", views.paynow_result, name="paynow-result"),
    path("product-history/<int:product_id>/", views.product_history, name="product_history"),
    path('users/<int:user_id>/reset-password/', views.reset_user_password, name='reset-user-password'),
    path("plan-payments/", views.plan_payments_list, name="plan_payments"),
    path('users/<int:user_id>/set-active/', views.set_user_active, name='set-user-active'),
    path("pay/fake-card/<str:ref>/", views.fake_card_page, name="fake-card"),
     



    #path("user/<int:user_id>/edit/", views.edit_user, name="user-edit"),

    #path('download/stock/excel/', views.download_stock_excel, name='download-stock-excel'),
    #path('download/stock/pdf/', views.download_stock_pdf, name='download-stock-pdf'),


    #path('subscription/', views.subscription_view, name='subscription'),

    # path('employees', views.employees, name="employee-page"),
    # path('manage_employees', views.manage_employees, name="manage_employees-page"),
    # path('save_employee', views.save_employee, name="save-employee-page"),
    # path('delete_employee', views.delete_employee, name="delete-employee"),
    # path('view_employee', views.view_employee, name="view-employee-page"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)