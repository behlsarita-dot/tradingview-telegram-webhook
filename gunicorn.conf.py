import os
bind = "0.0.0.0:" + os.getenv("PORT", "5000")

# FIX (2026-07-28): was workers=2. app.py starts its own DatabaseManager(),
# recover_stuck_webhooks() call, and a _webhook_worker polling thread at
# *module import time* - which happens independently in every gunicorn
# worker process. With workers=2 that meant TWO independent polling loops,
# each opening a fresh (unpooled, by design - see database.py) Neon
# connection every 0.5s during market hours (09:00-15:30 IST), ~6.5 hours
# a day. That's ~93,000+ connections/day just from polling, which kept the
# Neon compute endpoint continuously active and prevented autosuspend for
# effectively the entire trading day - twice over. Confirmed as the likely
# driver of the "compute time quota exceeded" crash on 2026-07-28.
#
# threads=4 (gthread) still gives plenty of concurrency for simultaneous
# webhook bursts from a single worker process; this workload does not need
# a second worker process. If you ever do need >1 worker again, the
# _webhook_worker thread-start in app.py must first be gated behind a
# leader-election (e.g. a Postgres advisory lock) so only one worker
# actually polls pending_webhooks.
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
keepalive = 5
max_requests = 5000
max_requests_jitter = 300
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"
