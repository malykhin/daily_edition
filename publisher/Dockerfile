FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY publisher/ /opt/publisher/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 1000:1000
ENTRYPOINT ["python3", "/opt/publisher/publisher.py"]
