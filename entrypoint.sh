#!/bin/sh
# Boot the portal: migrate, gather static, seed the fixtures, then serve.
# Everything runs offline. Nothing here reaches the network.
set -e

cd /app/src

echo "migrating database"
python manage.py migrate --noinput

echo "collecting static files"
python manage.py collectstatic --noinput >/dev/null

echo "seeding fixtures"
python manage.py seed

echo "starting gunicorn on 0.0.0.0:8080"
exec gunicorn dogfood.wsgi:application \
    --bind 0.0.0.0:8080 \
    --workers 2 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
