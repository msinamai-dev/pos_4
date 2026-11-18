from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Holds anyone flagged must_change_password on the change-password page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        profile = getattr(user, "userprofile", None)
        if not profile or not profile.must_change_password:
            return None

        from . import views
        if view_func in (views.change_password, views.logoutuser, views.login_user):
            return None                                   # the only pages they may use

        url = reverse(views.change_password)              # finds the URL from the view itself
        wants_json = (request.headers.get("x-requested-with") == "XMLHttpRequest"
                      or "application/json" in request.headers.get("accept", ""))
        if wants_json:
            return JsonResponse({"status": "failed", "msg": "You must change your password first.",
                                 "redirect_url": url}, status=403)
        return redirect(url)