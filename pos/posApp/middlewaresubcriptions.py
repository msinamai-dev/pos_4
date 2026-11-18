from django.shortcuts import redirect
from django.utils.timezone import now
from .models import UserSubscription

class SubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                sub = UserSubscription.objects.get(user=request.user)
                if not sub.has_active_subscription():
                    # Lock them out or restrict access
                    if not request.path.startswith("/view-data/"):  
                        return redirect("subscription-expired")
            except UserSubscription.DoesNotExist:
                return redirect("subscribe")
        return self.get_response(request)

