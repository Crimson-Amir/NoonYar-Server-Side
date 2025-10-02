FROM python:3.11-slim

WORKDIR /app

# Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY application ./application
COPY alembic ./alembic

# Expose FastAPI port
EXPOSE 80

# Command to run FastAPI
CMD ["uvicorn", "application.server_side:app", "--host", "0.0.0.0", "--port", "80"]
