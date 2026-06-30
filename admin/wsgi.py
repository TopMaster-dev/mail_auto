"""Gunicorn entry point:  gunicorn admin.wsgi:app -b 0.0.0.0:5000"""
from admin.app import create_app

app = create_app()
