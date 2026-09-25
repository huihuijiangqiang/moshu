FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache

WORKDIR /app

COPY pyproject.toml /tmp/pyproject.toml
RUN python - <<'PY'
import subprocess
import sys
import tomllib

with open('/tmp/pyproject.toml', 'rb') as source:
    dependencies = tomllib.load(source)['project']['dependencies']
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *dependencies])

# Token budgeting is required during module import. Bake the encoding into the
# image so API/worker startup never depends on a later public-network download.
import os
os.makedirs(os.environ['TIKTOKEN_CACHE_DIR'], exist_ok=True)
import tiktoken
tiktoken.get_encoding('cl100k_base')
PY

COPY . /app

# Keep local Compose as close to the production image as possible: API,
# migrations and Celery must not run as root. The mounted asset directory is
# created and owned here. Linux bind mounts retain host ownership; see the
# README for preparing that directory when using local Compose.
RUN addgroup --system --gid 10001 moshu \
    && adduser --system --uid 10001 --ingroup moshu --home /nonexistent --no-create-home moshu \
    && mkdir -p /app/.local/production-assets \
    && chown -R moshu:moshu /app /opt/tiktoken-cache

USER moshu

CMD ["celery", "-A", "celery_app:celery_app", "worker", "--loglevel=INFO"]
