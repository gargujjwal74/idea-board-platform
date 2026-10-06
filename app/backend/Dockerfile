FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .

# Run as a non-root user
RUN useradd --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
