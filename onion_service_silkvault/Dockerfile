# SilkVault — synthetic darknet forum #2 image.
# Slim Python base; gunicorn serves the Flask app on :5000 inside the container.
# A dedicated Tor container reverse-proxies SilkVault's own .onion address here.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VAULT_DB=/data/vault.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is a volume so the seeded DB persists across container restarts.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 5000

# Seed the DB on first boot if it's missing, then hand off to gunicorn.
# `exec` so gunicorn becomes PID 1 and signals (SIGTERM) propagate cleanly.
CMD ["sh", "-c", "if [ ! -f \"$VAULT_DB\" ]; then python seed_data.py --reset --count 55 --seed 73; fi && exec gunicorn -w 2 -b 0.0.0.0:5000 --access-logfile - app:app"]
