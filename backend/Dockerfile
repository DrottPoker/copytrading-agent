FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml constraints.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -c constraints.txt .

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY --chown=app:app app ./app
COPY --chown=app:app config ./config
COPY --chown=app:app alembic.ini ./

USER app

EXPOSE 8000
