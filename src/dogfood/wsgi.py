"""WSGI entry point for the DOGFOOD portal."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dogfood.settings")

application = get_wsgi_application()
