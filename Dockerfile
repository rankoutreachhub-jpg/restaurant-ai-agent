# Production image for the FastAPI backend.
#
# Build from the repository root:
#   docker build -t restaurant-ai-agent .
#
# Run (all configuration comes from real environment variables at
# runtime — no .env file is ever baked into this image; see
# backend/.env.example for the full list):
#   docker run -p 8000:8000 \
#     -e GEMINI_API_KEY=your-real-key \
#     -e ADMIN_API_KEY=your-real-admin-secret \
#     restaurant-ai-agent
#
# See README.md ("Docker") for the full walkthrough.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runs as a non-root user rather than the image's default root.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

WORKDIR /app

# Install dependencies first so this layer is only rebuilt when
# requirements.txt actually changes, not on every code change.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application code and Alembic migrations only — no tests, dev-only
# dependencies, or a local .env file (see .dockerignore).
COPY --chown=appuser:appuser backend/app ./app
COPY --chown=appuser:appuser backend/alembic ./alembic
COPY --chown=appuser:appuser backend/alembic.ini ./alembic.ini

# app/config.py resolves the SQLite file and the rotating log
# directory relative to this WORKDIR at runtime (backend/restaurant.db
# and backend/logs/ locally become /app/restaurant.db and /app/logs/
# here) — appuser needs permission to create both directly under /app.
RUN chown appuser:appuser /app

USER appuser

EXPOSE 8000

# Applies pending migrations before every start — the single schema-
# management path for every environment (see app/main.py and README.md,
# "Database migrations"). If a migration fails, the container fails to
# start rather than serving traffic against a stale/broken schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
