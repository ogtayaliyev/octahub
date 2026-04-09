#!/bin/sh

# Seed delay to ensure network is fully stable
sleep 2

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Create superuser automatically if not exists
echo "Creating superuser..."
python manage.py shell <<EOF
from django.contrib.auth import get_user_model
User = get_user_model()
username = 'admin'
email = 'ogtay.a@outlook.com'
password = 'Ogtay2003.'
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print(f'Superuser "{username}" created.')
else:
    print(f'Superuser "{username}" already exists.')
EOF

# Start server
echo "Starting server..."
exec python manage.py runserver 0.0.0.0:8000
