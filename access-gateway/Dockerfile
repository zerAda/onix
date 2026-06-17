# =============================================================================
# onix access-gateway — proxy RBAC identity-aware (cloisonnement par groupe/
# Document Set) devant Onyx. Image slim ÉPINGLÉE, utilisateur non-root, aucun
# binaire superflu. Aucune dépendance cloud (Entra joint via httpx).
# =============================================================================
FROM python:3.11.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GATEWAY_MAPPING_PATH=/config/group_map.json

# curl uniquement pour le healthcheck interne.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Mapping monté en lecture seule à /config (un exemple est fourni hors image).
RUN useradd --create-home --uid 10002 gateway \
    && mkdir -p /config \
    && chown -R gateway:gateway /app /config
USER gateway

EXPOSE 8200

# Healthcheck interne (pas de port hôte requis) : interroge /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8200/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
