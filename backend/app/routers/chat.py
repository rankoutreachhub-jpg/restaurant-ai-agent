"""
Chat API endpoint.

This is what the frontend widget calls every time the customer sends
a message. It:
  1. Looks up the restaurant's real data (knowledge.py)
  2. Sends it + the conversation to Claude (llm.py)
  3. Returns the reply
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import schemas, knowledge, llm
from ..database import get_db
from ..rate_limit import chat_rate_limiter

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(chat_rate_limiter)],
)


@router.post("", response_model=schemas.ChatResponse)
def chat(request: schemas.ChatRequest, db: Session = Depends(get_db)):
    try:
        restaurant_context = knowledge.build_restaurant_context(db, request.restaurant_id)

        if restaurant_context == "NO RESTAURANT DATA FOUND FOR THIS ID.":
            raise HTTPException(status_code=404, detail="Restaurant not found")

        reply = llm.generate_reply(
            user_message=request.message,
            history=request.history,
            restaurant_context=restaurant_context,
        )
        return schemas.ChatResponse(reply=reply)

    except HTTPException:
        raise
    except Exception as e:
        # In production you'd log this properly. For MVP, surface a clear error.
        raise HTTPException(status_code=500, detail=f"Something went wrong: {str(e)}")
