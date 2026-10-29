FROM python:3.12-slim

WORKDIR /app

# Сначала только зависимости: Docker закеширует этот шаг,
# и при правке кода они не будут скачиваться заново.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# На сервере запросы приходят через прокси хостинга (HTTPS → контейнер).
# Uvicorn должен брать настоящий адрес ученика из заголовка X-Forwarded-For —
# иначе у всех будет адрес прокси и лимит «5 попыток кода в минуту» станет общим.
ENV FORWARDED_ALLOW_IPS="*"

# Логи (print и logging) сразу попадают в вывод контейнера, а не копятся в буфере.
ENV PYTHONUNBUFFERED=1

# При старте: применить миграции → наполнить базу → запустить сервер.
# exec — чтобы сигнал остановки от Docker дошёл до uvicorn и он завершился аккуратно.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
