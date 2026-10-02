# One image: the API also serves the built frontend. Not yet exercised on this machine
# (Docker is not installed here); treat as a starting point and test before relying on it.

FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --no-cache-dir "./backend[postgres]"
COPY backend/alembic.ini backend/alembic.ini
COPY backend/alembic backend/alembic
COPY --from=frontend /build/dist frontend/dist

WORKDIR /app/backend
EXPOSE 8000
# Apply migrations, then serve. Seed separately with: python -m app.datagen && python -m app.cycle --retrain
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
