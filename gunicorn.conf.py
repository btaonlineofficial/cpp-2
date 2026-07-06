import os

port = os.environ.get("PORT", "8080")
bind = f"0.0.0.0:{port}"
workers = 2
threads = 4
loglevel = "debug"
accesslog = "-"
errorlog = "-"
timeout = 120
