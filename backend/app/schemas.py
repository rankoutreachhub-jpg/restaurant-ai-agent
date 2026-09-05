"""
Pydantic schemas define the shape of data going in and out of the API.
FastAPI uses these to validate requests and to auto-generate the
interactive API docs at /docs.
"""

from pydantic import BaseModel, Field
from typing import List, Optional


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        description="The customer's message"
    )
    history: Optional[List[ChatMessage]] = Field(
        default=[],
        description="Previous turns in this conversation, oldest first"
    )
    restaurant_id: int = Field(
        default=1,
        description="Which restaurant this chat belongs to"
    )


class ChatResponse(BaseModel):
    reply: str


class RestaurantUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    map_link: Optional[str] = None
    parking_notes: Optional[str] = None


class MenuItemCreate(BaseModel):
    category: str
    name: str
    description: Optional[str] = None
    price: float
    dietary_tags: Optional[str] = None


class MenuItemUpdate(BaseModel):
    category: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    dietary_tags: Optional[str] = None


class OpeningHoursUpdate(BaseModel):
    day_of_week: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    is_closed: Optional[bool] = None