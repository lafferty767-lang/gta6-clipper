FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY clipper ./clipper

ENV PYTHONUNBUFFERED=1 \
    CLIPPER_CONFIG=/app/config.yaml \
    CLIPPER_DATA_DIR=/data \
    HF_HOME=/data/hf-cache

VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["tini", "--", "python", "-m", "clipper"]
CMD ["run"]
