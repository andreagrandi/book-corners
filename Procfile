web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
worker: python manage.py db_worker
