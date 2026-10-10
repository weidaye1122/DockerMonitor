FROM python:3.12-slim
LABEL org.opencontainers.image.title="Docker Monitor"
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_PATH=/data/monitor.sqlite3 TZ=Asia/Shanghai
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY web/index.html web/app.css web/app.js ./web/
COPY web/login.html web/login.css web/login.js ./web/
COPY web/vendor ./web/vendor
COPY web/icons ./web/icons
COPY web/favicon.ico web/manifest.webmanifest ./web/
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["uvicorn","backend.app:app","--host","0.0.0.0","--port","8080","--no-access-log"]
