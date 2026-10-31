FROM python:3.12-slim
WORKDIR /app
COPY backend /app/backend
COPY contracts /app/contracts
COPY templates /app/templates
RUN pip install --no-cache-dir -r backend/requirements-worker.txt
ENV PYTHONPATH=/app/backend:/app PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
