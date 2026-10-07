# Automated Academics for a shared server: sign-in on, one container, data in a volume.
#   docker compose up -d          (then open http://localhost:8000 and create the administrator)

FROM node:24-slim AS screen
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build:app

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    AA_AUTH=1 \
    AA_DB=/data/automated_academics.db \
    AA_STATIC=/app/screen
WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/src backend/src
RUN pip install --no-cache-dir ./backend
COPY --from=screen /src/frontend/dist-app /app/screen
RUN useradd --system --uid 10001 aa && mkdir /data && chown aa /data
USER aa
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
# One process on purpose: jobs run in a thread pool and the database is a single SQLite file.
CMD ["python", "-m", "uvicorn", "automated_academics.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
