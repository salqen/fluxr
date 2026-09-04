FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=5000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_server.py ig_publisher.py dashboard.html login.html ./

# Bežíme ako neprivilegovaný používateľ; /data je perzistentný volume
RUN useradd --system --uid 10001 --no-create-home fluxr \
    && mkdir -p /data && chown fluxr:fluxr /data
USER fluxr
VOLUME ["/data"]

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=4).status==200 else 1)"

# --workers 1: scheduler a stav bota žijú v pamäti procesu (viď README)
CMD ["gunicorn", "bot_server:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120"]
