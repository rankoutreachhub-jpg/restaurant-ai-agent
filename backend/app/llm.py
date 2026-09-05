"""
LLM integration layer.

This is where we call the Claude API. All the "never invent facts"
behaviour comes from the SYSTEM_PROMPT_TEMPLATE below — this is prompt
engineering, not code logic, because that's genuinely the right tool
for the job: we want natural, flexible conversation, but boxed in by
strict rules about what it's allowed to claim as fact.
"""

from anthropic import Anthropic
from . import config

client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

MODEL_NAME = "claude-sonnet-4-6"

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
4. Table bookings are NOT handled yet in this version — if a customer tries to book a
   table, politely tell them booking isn't available through the chat yet, and give them
   the restaurant's phone number or email (from RESTAURANT DATA) to book directly.
5. If a customer's message is a complaint, a sensitive issue (e.g. allergy emergency,
   health and safety concern), or anything you're not confident about, tell them clearly
   you're passing it to a member of the team rather than trying to handle it yourself.
6. Keep answers concise and genuinely helpful — don't pad responses unnecessarily.
7. Never claim to be human. If asked, be honest that you're an assistant for the restaurant.

RESTAURANT DATA:
{restaurant_context}
"""


def generate_reply(user_message: str, history: list, restaurant_context: str) -> str:
    """
    Sends the conversation to Claude along with the restaurant's real
    data baked into the system prompt, and returns the assistant's reply text.
    """
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(restaurant_context=restaurant_context)

    # Build the message list: prior turns + the new user message
    messages = []
    for turn in history:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": user_message})

    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=500,
        system=system_prompt,
        messages=messages,
    )

    # response.content is a list of content blocks; for a plain text
    # reply there will be one block of type "text".
    reply_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    return reply_text.strip()
