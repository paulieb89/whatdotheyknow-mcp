FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Pinned to exactly what prod ran before 0.1.5 (pip list on the Fly machine,
# 2026-11-18). The unpinned ">=" list would have pulled FastMCP 4.x into a release
# meant only to remove _HttpGuard. Moving the image to uv.lock (and the FastMCP
# bump) is separate work.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "fastmcp==3.4.4" \
        "mcp==1.28.1" \
        "httpx==0.28.1" \
        "pydantic==2.13.4" \
        "curl-cffi==0.15.0" \
        "prometheus-client==0.24.1" \
        "starlette==1.3.1" \
        "uvicorn==0.51.0"

COPY . .

EXPOSE 8080

CMD ["python", "server.py"]
