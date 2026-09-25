# One image, one container. Deps are installed at build time, so nothing
# is pulled when the container boots. The portal runs fully offline.
FROM python:3.12-slim

# The commit that built this image, named in every signed results bundle. Pass it at
# build time: docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) .
ARG GIT_COMMIT=unknown

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=dogfood.settings \
    DJANGO_DEBUG=0 \
    DJANGO_DB_PATH=/data/db.sqlite3 \
    DOGFOOD_SIGNING_KEY_PATH=/data/signing_key.pem \
    GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY spec/ /app/spec/
COPY verify.py /app/verify.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /data

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
