FROM python:3.13.11-slim-bookworm@sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml constraints-dev.txt README.md ./
COPY agent_service/ ./agent_service/
COPY sandbox_api/ ./sandbox_api/

# Reuse the CI resolution without installing development extras or build isolation.
RUN python -m pip install --no-cache-dir setuptools==80.9.0 \
    && python -m pip install --no-cache-dir --no-build-isolation -c constraints-dev.txt . \
    && useradd --no-log-init --create-home --uid 10001 apilens \
    && mkdir /data \
    && chown apilens:apilens /data

USER apilens
CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
