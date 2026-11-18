from django.apps import AppConfig

class PosAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "posApp"

    def ready(self):
        import posApp.signals # only if you’re using signals

    
from django.apps import AppConfig

class PosAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "posApp"

    def ready(self):
        from . import signals  # noqa