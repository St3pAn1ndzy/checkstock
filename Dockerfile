FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    POETRY_HOME=/opt/poetry \
    PATH=/opt/poetry/bin:$PATH

RUN pip install --no-cache-dir poetry==1.8.3

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
