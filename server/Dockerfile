FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /tmp/pyproject.toml
RUN python - <<'PY'
import subprocess
import sys
import tomllib

with open('/tmp/pyproject.toml', 'rb') as source:
    dependencies = tomllib.load(source)['project']['dependencies']
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *dependencies])
PY

COPY . /app

CMD ["celery", "-A", "celery_app:celery_app", "worker", "--loglevel=INFO"]
