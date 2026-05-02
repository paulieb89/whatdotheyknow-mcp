FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "fastmcp>=3.2.4" \
        "httpx>=0.28.1" \
        "pydantic>=2.13.3" \
        "curl-cffi>=0.15.0" \
        "prometheus-client==0.24.1"

COPY . .

EXPOSE 8080

CMD ["python", "server.py"]
