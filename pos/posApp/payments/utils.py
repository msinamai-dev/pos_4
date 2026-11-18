import re
import time

from django.conf import settings
from django.core.cache import cache


def _fake():
    return getattr(settings, "PAYNOW_FAKE", False)


def _client(return_url=None):
    # imported here so fake mode works without `pip install paynow`
    from paynow import Paynow
    return Paynow(
        settings.PAYNOW_INTEGRATION_ID,
        settings.PAYNOW_INTEGRATION_KEY,
        return_url or settings.PAYNOW_RETURN_URL,
        settings.PAYNOW_RESULT_URL,
    )


def normalise_phone(raw):
    """'+263 77 123 4567' -> '0771234567'. Returns None if not a valid number."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("263"):
        digits = "0" + digits[3:]
    return digits if re.fullmatch(r"07\d{8}", digits) else None


def _auth_email(user):
    # In Paynow test mode the email MUST be your own merchant email
    if settings.PAYNOW_TEST_MODE:
        return settings.PAYNOW_AUTH_EMAIL
    return user.email or settings.PAYNOW_AUTH_EMAIL


# ---------------------------------------------------------------- start payment
def start_mobile_payment(user, reference, amount, phone, method="ecocash"):
    """Sends the PIN prompt to the customer's phone."""
    if _fake():
        return {"success": True,
                "poll_url": f"fake:{reference}:{phone}:{int(time.time())}",
                "instructions": "Fake payment: no real prompt is sent."}

    # ---------- REAL PAYNOW ----------
    client = _client()
    payment = client.create_payment(reference, _auth_email(user))
    payment.add("Subscription", float(amount))
    resp = client.send_mobile(payment, phone, method)
    if resp.success:
        return {"success": True, "poll_url": resp.poll_url,
                "instructions": getattr(resp, "instructions", "")}
    return {"success": False, "error": getattr(resp, "error", "Payment could not be started.")}


def start_card_payment(user, reference, amount):
    """Returns the page where the customer pays by card."""
    if _fake():
        return {"success": True,
                "poll_url": f"fake-card:{reference}",
                "redirect_url": f"/pay/fake-card/{reference}/"}

    # ---------- REAL PAYNOW ----------
    client = _client(f"{settings.PAYNOW_RETURN_URL}?ref={reference}")
    payment = client.create_payment(reference, _auth_email(user))
    payment.add("Subscription", float(amount))
    resp = client.send(payment)
    if resp.success:
        return {"success": True, "poll_url": resp.poll_url, "redirect_url": resp.redirect_url}
    return {"success": False, "error": getattr(resp, "error", "Payment could not be started.")}


# ---------------------------------------------------------------- check status
def _fake_status(poll_url):
    parts = poll_url.split(":")
    if parts[0] == "fake-card":
        return cache.get(f"fakepay:{parts[1]}", "sent")      # set by the fake card page

    # fake:<reference>:<phone>:<started_at>
    phone, started = parts[2], int(parts[3])
    if phone == "0773333333":
        return "cancelled"                       # customer cancels
    if phone == "0774444444":
        return "failed"                          # insufficient balance
    if phone == "0772222222" and time.time() - started < 10:
        return "sent"                            # delayed success: paid after 10 seconds
    return "paid"                                # 0771111111 and any other number


def check_status(poll_url):
    """Returns a lowercase status such as 'paid' or 'cancelled'."""
    if poll_url.startswith("fake"):
        return _fake_status(poll_url)

    # ---------- REAL PAYNOW ----------
    status = _client().check_transaction_status(poll_url)
    return (status.status or "").lower()