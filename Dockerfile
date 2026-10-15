FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /service
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock ./
COPY app ./app
ARG INSTALL_AUDIO=false
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && if [ "$INSTALL_AUDIO" = "true" ]; then pip install --no-cache-dir '.[audio]'; fi \
    && useradd --create-home --uid 10001 service \
    && mkdir -p /service/data/covers && chown -R service:service /service/data
USER service
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
