"""Gunicorn configuration for production deployment.
Tunes workers, threads, and recycling for a small-to-medium VPS."""

import multiprocessing

workers = 2
threads = 2
timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
