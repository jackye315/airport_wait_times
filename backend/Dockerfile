FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-cache-dir .

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data /artifacts /backups \
    && chown -R app:app /app /data /artifacts /backups \
    && chmod -R a+rX /app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir ".[dev]"
COPY --chown=app:app tests ./tests
USER app
CMD ["pytest"]

FROM runtime AS production
