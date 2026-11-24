# NEXUS AI backend

FastAPI backend for the NEXUS AI Wix frontend. Uses OpenAI for streamed chat completions and Neon PostgreSQL for conversation persistence.

## Deploy on Render

This repository includes `render.yaml` for a Blueprint deployment. Set the following secrets/environment variables in the Render dashboard:

- `DATABASE_URL`: Neon connection string (keep private; require SSL).
- `OPENAI_API_KEY`: OpenAI API key. Create and enter it directly in Render; never commit it or send it in chat.
- `NEXUS_API_SECRET`: long random shared secret. This is a temporary service gate, not user authentication.
- `ALLOWED_ORIGINS`: exact published Wix site origin(s), comma-separated.
- `OPENAI_MODEL`: defaults to `gpt-4.1-mini`.

## Endpoints

- `GET /` service identity
- `GET /health` liveness and database connectivity
- `POST /v1/chat/stream` SSE chat stream; requires `X-Nexus-Secret`
- `GET /v1/conversations` list conversations; requires `X-Nexus-Secret`
- `GET /v1/conversations/{conversation_id}/messages` read messages; requires `X-Nexus-Secret`

## Important security note

The shared `X-Nexus-Secret` is only a temporary backend access gate. Before public multi-user launch, implement per-user authentication, verify signed user tokens on every request, and enforce conversation ownership. Do not expose the shared secret in browser-side Wix code. A Wix backend function should call this API server-to-server.
