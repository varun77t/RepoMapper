FROM python:3.12-slim

# git is a runtime dependency, not a build one: GitPython shells out to it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first so the dependency layer caches independently of source.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Shell form so $PORT expands: managed hosts (Render, Fly, Cloud Run) assign the
# port at runtime and route to it. Falls back to 8000, which is what Compose
# publishes.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
