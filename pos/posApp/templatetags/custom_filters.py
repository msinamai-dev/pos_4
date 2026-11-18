from django import template

register = template.Library()

@register.filter
def pluck(objects, key):
    """
    Extracts a list of values for a given key from a list of dicts or objects.
    Example: {{ queryset|pluck:"field_name" }}
    """
    result = []
    for obj in objects:
        if isinstance(obj, dict):
            result.append(obj.get(key))
        else:
            result.append(getattr(obj, key, None))
    return result


from django import template

register = template.Library()

@register.filter
def abs(value):
    try:
        return abs(value)
    except Exception:
        return value
