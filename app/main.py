import json
import os
import uuid
import time
import logging
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
NEXUS_API_SECRET = os.getenv("NEXUS_API_SECRET", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
logger = logging.getLogger("nexus-ai")
GUEST_PER_MINUTE = int(os.getenv("GUEST_PER_MINUTE", "6"))
GUEST_PER_DAY = int(os.getenv("GUEST_PER_DAY", "40"))
_rate_windows = defaultdict(deque)
_rate_daily = defaultdict(lambda: [int(time.time() // 86400), 0])

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL, connect_timeout=10)

def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
          id UUID PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New chat',
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
          id UUID PRIMARY KEY,
          conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
          role TEXT NOT NULL CHECK (role IN ('user','assistant')),
          content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        conn.execute("CREATE INDEX IF NOT EXISTS messages_conversation_created_idx ON messages(conversation_id, created_at)")

@asynccontextmanager
async def lifespan(app: FastAPI):
    if DATABASE_URL:
        init_db()
    yield

app = FastAPI(title="NEXUS AI API", version="1.0.0", lifespan=lifespan)
origins = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins or ["https://headless-cqnayqcealk-syadav39d-1409.wix-site-host.com"], allow_credentials=False, allow_methods=["GET","POST","OPTIONS"], allow_headers=["Content-Type"])

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: Optional[str] = None

def authorize(secret: Optional[str]):
    if not NEXUS_API_SECRET:
        raise HTTPException(status_code=503, detail="Private API access is not configured")
    if secret != NEXUS_API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API credentials")

def enforce_guest_limits(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    minute = _rate_windows[ip]
    while minute and now - minute[0] >= 60:
        minute.popleft()
    if len(minute) >= GUEST_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many messages. Please wait a minute and try again.")
    day = int(now // 86400)
    daily = _rate_daily[ip]
    if daily[0] != day:
        daily[:] = [day, 0]
    if daily[1] >= GUEST_PER_DAY:
        raise HTTPException(status_code=429, detail="Guest daily message limit reached for this network. Please try again tomorrow.")
    minute.append(now)
    daily[1] += 1

@app.get("/")
def root():
    return {"name":"NEXUS AI API","status":"running","version":"1.0.0"}

@app.get("/health")
def health():
    result = {"status":"ok","database":"not_configured","openai":"configured" if OPENAI_API_KEY else "not_configured"}
    if DATABASE_URL:
        try:
            with db() as conn:
                conn.execute("SELECT 1")
            result["database"] = "connected"
        except Exception:
            result.update(status="degraded", database="unavailable")
    if not OPENAI_API_KEY:
        result["status"] = "degraded"
    return result

@app.post("/v1/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    enforce_guest_limits(request)
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Conversation storage is not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="The AI response service is not configured")
    try:
        conversation_id = uuid.UUID(body.conversation_id) if body.conversation_id else uuid.uuid4()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation_id")
    with db() as conn:
        exists = conn.execute("SELECT id FROM conversations WHERE id=%s", (conversation_id,)).fetchone()
        if not exists:
            title = body.message.strip().replace("\n"," ")[:60] or "New chat"
            conn.execute("INSERT INTO conversations (id,title) VALUES (%s,%s)", (conversation_id,title))
        history_rows = conn.execute("SELECT role,content FROM messages WHERE conversation_id=%s ORDER BY created_at ASC LIMIT 40", (conversation_id,)).fetchall()
        conn.execute("INSERT INTO messages (id,conversation_id,role,content) VALUES (%s,%s,'user',%s)", (uuid.uuid4(),conversation_id,body.message))
        conn.execute("UPDATE conversations SET updated_at=NOW() WHERE id=%s", (conversation_id,))
    messages = [{"role":"system","content":"You are NEXUS AI, a helpful, accurate assistant. Be clear, useful, and safe. If uncertain, say so. Do not claim to browse or use tools unless available."}]
    messages.extend({"role":role,"content":content} for role,content in history_rows)
    messages.append({"role":"user","content":body.message})
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    async def events():
        answer_parts = []
        try:
            stream = await client.chat.completions.create(model=OPENAI_MODEL,messages=messages,stream=True,max_tokens=700)
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    answer_parts.append(delta)
                    yield "data: " + json.dumps({"type":"delta","text":delta,"conversation_id":str(conversation_id)}) + "\n\n"
            answer = "".join(answer_parts)
            if answer:
                with db() as conn:
                    conn.execute("INSERT INTO messages (id,conversation_id,role,content) VALUES (%s,%s,'assistant',%s)", (uuid.uuid4(),conversation_id,answer))
                    conn.execute("UPDATE conversations SET updated_at=NOW() WHERE id=%s", (conversation_id,))
            yield "data: " + json.dumps({"type":"done","conversation_id":str(conversation_id)}) + "\n\n"
        except Exception as exc:
            logger.exception("Guest chat generation failed (%s)", type(exc).__name__)
            # Return a useful, non-sensitive error to the guest while keeping secrets out of the response.
            from openai import AuthenticationError, RateLimitError, APIConnectionError, APIStatusError
            if isinstance(exc, AuthenticationError):
                public_message = "The AI provider rejected the server API key. Please contact the site administrator."
            elif isinstance(exc, RateLimitError):
                public_message = "The AI service has reached its usage limit. Please try again later."
            elif isinstance(exc, APIConnectionError):
                public_message = "The AI provider could not be reached. Please try again."
            elif isinstance(exc, APIStatusError):
                public_message = "The AI provider returned an error. Please try again in a moment."
            else:
                public_message = "The response could not be completed. Please try again."
            yield "data: " + json.dumps({"type":"error","message":public_message}) + "\n\n"
        finally:
            await client.close()
    return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache, no-transform","X-Accel-Buffering":"no"})

@app.get("/v1/conversations")
def conversations(x_nexus_secret: Optional[str] = Header(default=None)):
    authorize(x_nexus_secret)
    with db() as conn:
        rows = conn.execute("SELECT id,title,created_at,updated_at FROM conversations ORDER BY updated_at DESC LIMIT 100").fetchall()
    return [{"id":str(r[0]),"title":r[1],"created_at":r[2].isoformat(),"updated_at":r[3].isoformat()} for r in rows]

@app.get("/v1/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str, x_nexus_secret: Optional[str] = Header(default=None)):
    authorize(x_nexus_secret)
    try:
        cid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400,detail="Invalid conversation_id")
    with db() as conn:
        rows = conn.execute("SELECT id,role,content,created_at FROM messages WHERE conversation_id=%s ORDER BY created_at ASC",(cid,)).fetchall()
    return [{"id":str(r[0]),"role":r[1],"content":r[2],"created_at":r[3].isoformat()} for r in rows]
