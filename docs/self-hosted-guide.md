# Alfred Self-Hosted Deployment Guide

## Prerequisites

- Docker + Docker Compose installed
- A Frappe v15+ site with `alfred_client` app installed
- At least 8GB RAM (16GB recommended for Ollama)
- GPU optional (CPU works for smaller models, GPU recommended for production)

## Step 1: Clone and Configure

```bash
git clone <your-alfred-processing-repo>
cd alfred_processing
cp .env.example .env
```

Edit `.env`:
```env
API_SECRET_KEY=<generate-a-strong-random-key>
REDIS_URL=redis://redis:6379/0
FALLBACK_LLM_MODEL=ollama/llama3.1
FALLBACK_LLM_BASE_URL=http://ollama:11434
```

## Step 2: Start Services

```bash
# With local Ollama:
docker compose --profile local-llm up -d

# Or without Ollama (if using remote Ollama or cloud LLM):
docker compose up -d
```

This starts:
- **Processing App** (port 8001) — the AI agent service
- **Redis** (port 6379) — state store
- **Ollama** (port 11434) — local LLM server

## Step 3: Pull an LLM Model

```bash
docker exec -it alfred_processing-ollama-1 ollama pull llama3.1
```

For smaller hardware, use `llama3.2:3b` or `mistral:7b`.

## Step 4: Verify Health

```bash
curl http://localhost:8001/health
# Expected: {"status": "ok", "version": "0.1.0", "redis": "connected"}
```

## Step 5: Configure Alfred Settings

On your Frappe site, go to `/app/alfred-settings`:

| Field | Value |
|-------|-------|
| Processing App URL | `ws://localhost:8001` (or your server's IP) |
| API Key | Same value as `API_SECRET_KEY` in `.env` |
| LLM Provider | `ollama` |
| LLM Model | `ollama/llama3.1` |
| LLM Base URL | `http://localhost:11434` |

## Step 6: Test

Navigate to `/app/alfred-chat` and type: "Create a DocType called Book with title and author fields"

## Troubleshooting

**Ollama out of memory**: Use a smaller model (`llama3.2:3b`) or add GPU support with `docker compose --profile local-llm --profile gpu up -d`.

**Processing App unreachable**: Check that your Frappe site can reach `localhost:8001`. If running in Docker, use the host's IP instead of `localhost`.

**Redis connection refused**: Ensure the Redis container is running: `docker ps | grep redis`.

**WebSocket connection fails**: Check firewall allows port 8001. If behind nginx, add WebSocket proxy:
```nginx
location /ws/ {
    proxy_pass http://localhost:8001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
}
```
