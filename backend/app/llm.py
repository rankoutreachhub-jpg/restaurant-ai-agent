"""
LLM integration layer.

This is where we call the Gemini API via the Google GenAI SDK
(google-genai). All the "never invent facts" behaviour comes from the
SYSTEM_PROMPT_TEMPLATE below — this is prompt engineering, not code
logic, because that's genuinely the right tool for the job: we want
natural, flexible conversation, but boxed in by strict rules about what
it's allowed to claim as fact.

Stage 2c adds AI-assisted booking via Gemini function calling. This
file only knows how to talk to Gemini and orchestrate the tool-call
round trip — it has no idea what a "booking" actually is. The caller
(routers/chat.py) supplies book_tool_handler, a callback that has the DB
session and restaurant in scope and calls the real app/booking.py
create_booking() — the same function admin booking management uses.
That separation is deliberate: this file can never itself decide a
booking succeeded, guess at availability, or duplicate any of
booking.py's overlap/capacity logic. It only ever relays whatever
structured result book_tool_handler returns, and requires that the
model call the tool at most once per turn.
"""

from typing import Callable, Optional

from google import genai
from google.genai import types

from . import config

client = genai.Client(api_key=config.GEMINI_API_KEY)

MODEL_NAME = config.GEMINI_MODEL_NAME

BOOKING_TOOL_NAME = "create_booking"

# Declared once at import time — passed to every call that allows
# booking (i.e. whenever the caller supplies a book_tool_handler).
_BOOKING_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name=BOOKING_TOOL_NAME,
            description=(
                "Creates a table booking. Only call this once you have collected "
                "every required field directly from the customer, in their own "
                "words — never guess, assume, or fill in a value they haven't "
                "given you. The backend independently re-checks opening hours "
                "and seating capacity, so this call can still be rejected even "
                "if you believe the slot is free."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "customer_name": types.Schema(
                        type=types.Type.STRING,
                        description="The customer's full name, exactly as they gave it.",
                    ),
                    "phone": types.Schema(
                        type=types.Type.STRING,
                        description="The customer's phone number, exactly as they gave it.",
                    ),
                    "email": types.Schema(
                        type=types.Type.STRING,
                        description="The customer's email address, exactly as they gave it.",
                    ),
                    "booking_date": types.Schema(
                        type=types.Type.STRING,
                        description="The requested date, as YYYY-MM-DD.",
                    ),
                    "booking_time": types.Schema(
                        type=types.Type.STRING,
                        description="The requested time, 24-hour HH:MM.",
                    ),
                    "party_size": types.Schema(
                        type=types.Type.INTEGER,
                        description="Number of people in the party.",
                    ),
                    "notes": types.Schema(
                        type=types.Type.STRING,
                        description="Any special request the customer mentioned. Omit if none.",
                    ),
                },
                required=[
                    "customer_name",
                    "phone",
                    "email",
                    "booking_date",
                    "booking_time",
                    "party_size",
                ],
            ),
        )
    ]
)

SYSTEM_PROMPT_TEMPLATE = """You are a friendly customer service assistant for a UK restaurant.
You speak in a natural, warm, UK-English conversational tone (e.g. "Hiya", "no worries",
"lovely", "cheers") without overdoing it — sound like a helpful person, not a robot.

You have been given the restaurant's ACTUAL data below inside the RESTAURANT DATA block.
This is the ONLY source of truth you are allowed to use for facts about this restaurant.

STRICT RULES — YOU MUST FOLLOW THESE AT ALL TIMES:
1. NEVER invent or guess menu items, prices, opening hours, location details, or FAQs.
   If it is not written in the RESTAURANT DATA block below, you do not know it.
2. If a customer asks something that is not covered in RESTAURANT DATA, say clearly and
   politely that you don't have that information, and that you'll flag it to the team.
   Example: "I don't have that information to hand, I'm afraid — I'll get the team to
   follow up with you on that."
3. Do NOT make up or estimate anything: no prices, no availability, no times, no promises.
   The one exception is the AVAILABILITY SUMMARY in RESTAURANT DATA — that's real data,
   so you can discuss it, but it does not replace the actual booking check below.
4. Table bookings: you CAN take bookings directly in this conversation using the
   create_booking tool. Before calling it, you must have collected ALL of the
   following directly from the customer, in their own words — never guess, assume,
   or invent any of them: full name, phone number, email address, date, time, and
   party size. If anything is missing or unclear, ask for it — do not call the tool
   until you have every field.
   Call create_booking at most once per turn. After calling it, report EXACTLY what
   the tool result says — nothing more, nothing less. A successful result means the
   booking is confirmed; a rejection (e.g. no availability, the restaurant is closed
   that day, or an invalid time) means it is NOT booked. Never tell a customer their
   table is booked unless the tool result confirms it, and never invent a booking
   reference or say "you're booked" before you have that confirmed result. If the
   tool rejects the request, tell the customer clearly why (using the tool's own
   message) and offer to try a different date/time, or give them the phone number/
   email from RESTAURANT DATA to book directly instead.
5. If a customer's message is a complaint, a sensitive issue (e.g. allergy emergency,
   health and safety concern), or anything you're not confident about, tell them clearly
   you're passing it to a member of the team rather than trying to handle it yourself.
6. Keep answers concise and genuinely helpful — don't pad responses unnecessarily.
7. Never claim to be human. If asked, be honest that you're an assistant for the restaurant.

RESTAURANT DATA:
{restaurant_context}
"""


def generate_reply(
    user_message: str,
    history: list,
    restaurant_context: str,
    book_tool_handler: Optional[Callable[[dict], dict]] = None,
) -> str:
    """
    Sends the conversation to Gemini along with the restaurant's real
    data baked into the system instruction, and returns the assistant's
    reply text.

    If book_tool_handler is given, the create_booking tool is made
    available to the model. book_tool_handler is called with the raw
    argument dict Gemini supplies (never validated or trusted by this
    file) and must return a small JSON-serialisable dict describing what
    actually happened — this file only relays that result back to
    Gemini for it to phrase in its final reply; it never decides
    success/failure itself. At most one tool call is handled per turn.
    """
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(restaurant_context=restaurant_context)

    # Gemini's "contents" list uses role "model" for prior assistant turns
    # (there is no "assistant" role in this API, unlike Claude/OpenAI).
    contents = []
    for turn in history:
        role = "model" if turn.role == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn.content)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    generate_config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=500,
        tools=[_BOOKING_TOOL] if book_tool_handler else None,
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=generate_config,
    )

    function_calls = list(getattr(response, "function_calls", None) or [])
    if function_calls and book_tool_handler:
        call = function_calls[0]
        if call.name == BOOKING_TOOL_NAME:
            tool_result = book_tool_handler(dict(call.args or {}))
        else:
            # No other tool is declared, so this shouldn't happen — but
            # never silently pretend a call we don't recognise succeeded.
            tool_result = {"status": "rejected", "reason": "Unknown tool requested."}

        # Continue the same conversation: the model's own function-call
        # turn, then the tool's result, then ask it for the final reply.
        contents.append(response.candidates[0].content)
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name=call.name, response=tool_result)],
            )
        )

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=generate_config,
        )

    return (response.text or "").strip()
