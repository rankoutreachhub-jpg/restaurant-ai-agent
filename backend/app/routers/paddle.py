"""
Paddle webhook endpoint (Jantar SaaS Phase 4 — subscription
provisioning; see app/paddle_webhooks.py for signature verification and
the actual event-processing logic this router delegates to).

One route, POST /webhooks/paddle — Paddle has no GET verification
handshake equivalent to Meta's (see app/routers/whatsapp.py); Paddle
verifies a webhook destination simply by it responding 200 to real
notifications.

Security model, mirroring app/routers/whatsapp.py's POST handler:
  1. The raw request body's Paddle-Signature header must verify against
     PADDLE_WEBHOOK_SECRET (app/paddle_webhooks.py:verify_paddle_signature)
     BEFORE any JSON parsing or database access.
  2. Only after that succeeds does anything look at the payload's
     contents — and even then, restaurant identity is never taken from
     the payload directly; see app/paddle_webhooks.py's module docstring
     for the only two links this system ever trusts.

Processing happens synchronously (unlike the WhatsApp webhook's
BackgroundTasks pattern) — it's a handful of local DB writes, no
outbound network call, so there is no reason to ack before it's durably
recorded; doing it inline also means Paddle's own retry-on-non-2xx
behavior is the only retry mechanism ever needed for a transient DB
error, with nothing left half-done.

Never logs the raw webhook payload, the signature header, or any secret
— see app/paddle_webhooks.py's own logging calls, which log structured
facts (event_id/event_type/outcome) only.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..paddle_webhooks import process_paddle_webhook_event, verify_paddle_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/paddle", tags=["Paddle"])


@router.post("")
async def receive_paddle_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    signature_header = request.headers.get("paddle-signature", "")

    if not verify_paddle_signature(raw_body, signature_header):
        logger.warning("Paddle webhook rejected: invalid or missing signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except ValueError:
        logger.warning("Paddle webhook rejected: invalid JSON payload")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    result = process_paddle_webhook_event(db, payload)
    logger.info("Paddle webhook processed (outcome=%s)", result.get("outcome"))
    return Response(status_code=status.HTTP_200_OK)
