# SilkVault forum image: Flask app served by gunicorn on :5000 (its own Tor exposes it)

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

# Seed the DB on first boot, then start gunicorn
CMD ["sh", "-c", "if [ ! -f \"$VAULT_DB\" ]; then python seed_data.py --reset --count 55 --seed 73; fi && exec gunicorn -w 2 -b 0.0.0.0:5000 --access-logfile - app:app"]
