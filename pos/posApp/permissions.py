from .models import UserProfile

def filter_queryset_for_user(user, queryset, right_key=None):
    """
    Filters a queryset based on the user's department and access rights.
    
    :param user: Django User object
    :param queryset: queryset to filter
    :param right_key: optional key in access_rights to check (e.g. "can_manage_stock")
    :return: filtered queryset
    """
    profile = getattr(user, 'userprofile', None)
    if not profile:
        return queryset.none()  # No profile, no access

    # If a specific right is required but user doesn’t have it → empty
    if right_key and not profile.access_rights.get(right_key, False):
        return queryset.none()

    # If the model has a `department` field, filter by user’s department
    if hasattr(queryset.model, "department") and profile.department:
        return queryset.filter(department=profile.department)

    # Otherwise, return everything if allowed
    return queryset
