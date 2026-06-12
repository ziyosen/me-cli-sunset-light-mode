FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Buat direktori untuk data persisten
RUN mkdir -p /data/webui_data

# Set environment variables default (akan di-override oleh fly secrets)
ENV WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=8080

EXPOSE 8080

CMD ["python", "run-web.py"]
