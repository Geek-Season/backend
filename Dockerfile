FROM python:3.12-slim

WORKDIR /app

# Сначала только зависимости: Docker закеширует этот шаг,
# и при правке кода они не будут скачиваться заново.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# При старте: применить миграции → наполнить базу → запустить сервер.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
