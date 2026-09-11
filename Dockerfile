FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

COPY app ./app

RUN useradd --system --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"

# create_app(settings) is not a zero-arg factory, so the module entrypoint
# (app/__main__.py) owns settings loading and the uvicorn.run call.
CMD ["python", "-m", "app"]
