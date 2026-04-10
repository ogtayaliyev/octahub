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
    """Create superuser if it doesn't exist."""
    print(">>> Creating superuser...")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'octascraper.settings')
    django.setup()
    
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    username = 'admin'
    email = 'ogtay.a@outlook.com'
    password = 'Ogtay2003.'
    
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
    print("Starting Django development server...")
    print("=" * 60 + "\n")
    os.system("python manage.py runserver 0.0.0.0:8000")

if __name__ == "__main__":
    main()
