#!/usr/bin/env python
"""
Docker entrypoint script for OctaScraper Django application.
Handles database migrations, static files collection, and superuser creation.
"""
import os
import sys
import time
import django

def run_command(command):
    """Execute a Django management command."""
    print(f">>> {command}")
    os.system(f"python manage.py {command}")

def create_superuser():
    """Create superuser if env variables are provided and the user doesn't exist."""
    username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
    email = os.environ.get('DJANGO_SUPERUSER_EMAIL')
    password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

    if not all([username, email, password]):
        print(">>> Skipping superuser creation (env vars not provided).")
        return

    print(">>> Creating superuser...")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'octascraper.settings')
    django.setup()

    from django.contrib.auth import get_user_model
    User = get_user_model()

    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username, email, password)
        print(f'✓ Superuser "{username}" created successfully.')
    else:
        print(f'✓ Superuser "{username}" already exists.')

def main():
    """Main entrypoint function."""
    print("=" * 60)
    print("OctaScraper Django Application - Starting...")
    print("=" * 60)
    
    # Wait for database
    print("\n>>> Waiting for database...")
    time.sleep(2)
    
    # Collect static files
    print("\n>>> Collecting static files...")
    run_command("collectstatic --noinput")
    
    # Run migrations
    print("\n>>> Running database migrations...")
    run_command("migrate --noinput")
    
    # Create superuser
    print()
    create_superuser()
    
    # Start server
    print("\n" + "=" * 60)
    debug_mode = os.environ.get('DEBUG', 'False').lower() in ('1', 'true', 'yes', 'on')
    if debug_mode:
        print("Starting Django development server...")
        print("=" * 60 + "\n")
        os.system("python manage.py runserver 0.0.0.0:8000")
    else:
        workers = os.environ.get('GUNICORN_WORKERS', '3')
        timeout = os.environ.get('GUNICORN_TIMEOUT', '120')
        print(f"Starting Gunicorn ({workers} workers, timeout={timeout}s)...")
        print("=" * 60 + "\n")
        os.system(f"gunicorn octascraper.wsgi:application --bind 0.0.0.0:8000 --workers {workers} --timeout {timeout} --access-logfile - --error-logfile -")

if __name__ == "__main__":
    main()
