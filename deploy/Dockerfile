# Railway cron image for the dashboard data refresh. Separate from the
# integracion_F1 image on purpose: this one only needs git + the refresh_data
# dependencies, and pulls the code from the repo at runtime.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Exactly what scripts/refresh_data.py imports (see its header).
RUN pip install --no-cache-dir \
    pandas requests shapely openpyxl \
    google-api-python-client google-auth

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

CMD ["/usr/local/bin/entrypoint.sh"]
