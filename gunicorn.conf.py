import os
bind = "0.0.0.0:" + os.getenv("PORT", "5000")
workers = 2
worker_class = "sync"
threads = 4
timeout = 120
keepalive = 5
max_requests = 5000
max_requests_jitter = 300
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"