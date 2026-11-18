# POS changes: verified ERP shop linking and consistent tables

## Configure the shared owner-approval handshake

Set these environment variables in the POS terminal before starting Django:

```powershell
$env:POS_LINK_SHARED_SECRET = "PASTE_THE_SAME_LONG_RANDOM_SECRET_HERE"
$env:ERP_PUBLIC_BASE_URL = "http://127.0.0.1:8000"
$env:POS_PUBLIC_BASE_URL = "http://172.18.6.25:8000"
```

The `POS_LINK_SHARED_SECRET` must match the ERP setting exactly. Generate a strong value once with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use an ERP URL reachable by the browser that approves the request. For another workstation, do not use `127.0.0.1`; use the ERP computer's LAN hostname/IP. Configure the same `ERP_PUBLIC_BASE_URL` in the ERP process.

## Apply the POS migration

Use the POS project's own virtual environment and its existing `posApp` database:

```powershell
python manage.py migrate posApp
```

New tables:

- `posApp_erpshoplinkrequest` — request, company, shop, expiry and owner decision.
- `posApp_erpshoplinkaudit` — append-oriented audit events for approvals and rejections.

The owner approval page is `/erp-link/authorize/`. It requires a signed short-lived token, an authenticated user, and that user must be the registered owner of the requested shop. It never asks for the owner's password on the ERP website.

## Table UI

The shared table enhancement styles tables consistently and adds search plus 10/15-row pagination to tables across POS pages. Search is performed against the rows currently rendered by each page. Existing business forms and APIs are not intended to be changed by the table control.

## Security

The POS project previously had payment/email credentials and a Django secret key in settings. This package moves those values to environment variables. Configure `DJANGO_SECRET_KEY`, `SMILE_PAY_MERCHANT_ID`, `SMILE_PAY_API_KEY`, `EMAIL_HOST_USER`, and `EMAIL_HOST_PASSWORD` as required for your deployment. Rotate credentials that were ever stored in source files or shared archives.


`\.env.example` is a reference template only; these current settings read operating-system environment variables and do not automatically load that file. In PowerShell, set the variables in each terminal before launching `python manage.py runserver`, or configure them in Windows Environment Variables.
