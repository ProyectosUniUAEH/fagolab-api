FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "for i in 1 2 3 4 5 6 7 8 9 10 11 12; do python db/migrate.py && break; echo waiting for postgres...; sleep 5; done; exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
