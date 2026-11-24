# NEXUS AI backend

FastAPI backend for the NEXUS AI guest-mode website. Streams OpenAI chat responses and stores conversations in Neon PostgreSQL.

## Deployment

The service is deployed on Render from this repository's `main` branch. Configure these values in Render's private environment settings:

- `DATABASE_URL`: Neon connection string; keep private and use SSL.
- `OPENAI_API_KEY`: OpenAI API key; enter it directly in Render and never commit it or send it in chat.
- `NEXUS_API_SECRET`: long random secret used only for private conversation-management endpoints.
- `ALLOWED_ORIGINS`: exact Wix site origin, comma-separated if more than one.
- `OPENAI_MODEL`: defaults to `gpt-4.1-mini`.
- `GUEST_PER_MINUTE`: guest chat limit per IP per minute; defaults to 6.
- `GUEST_PER_DAY`: guest chat limit per IP per day; defaults to 40.

## Endpoints

- `GET /`: service identity.
- `GET /health`: checks database connectivity and whether the OpenAI key is configured.
- `POST /v1/chat/stream`: public no-login SSE chat endpoint. The browser sends a UUID conversation ID; the API stores user and assistant messages in Neon.
- `GET /v1/conversations`: private conversation listing; requires `X-Nexus-Secret`.
- `GET /v1/conversations/{conversation_id}/messages`: private message retrieval; requires `X-Nexus-Secret`.

## Guest-mode security and limitations

The public chat endpoint does not expose the OpenAI key or the private shared secret to the browser. It validates message length, limits output tokens, and applies per-IP rate limits. The current rate limiter is in-process and intended for the current single Render instance; use a shared rate-limit store before horizontal scaling.

Guest conversations are associated with unguessable UUIDs and browser-side local history. There is no sign-in, cross-device sync, or account recovery. The private conversation-management endpoints must only be called from a trusted server that holds `NEXUS_API_SECRET`.

For a larger public launch, add a persistent distributed rate limiter, conversation ownership/session binding, abuse monitoring, retention controls, and a spending budget/alert in the OpenAI project.
