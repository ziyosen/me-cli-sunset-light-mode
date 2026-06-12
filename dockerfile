FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Buat symlink agar data disimpan di volume
RUN mkdir -p /data/webui_data && ln -s /data/webui_data /app/webui_data

ENV WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=8080

EXPOSE 8080

CMD ["python", "run-web.py"]
