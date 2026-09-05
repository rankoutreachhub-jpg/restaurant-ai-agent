"""
LLM integration layer.

This is where we call the Gemini API via the Google GenAI SDK
(google-genai). All the "never invent facts" behaviour comes from the
SYSTEM_PROMPT_TEMPLATE below — this is prompt engineering, not code
logic, because that's genuinely the right tool for the job: we want
natural, flexible conversation, but boxed in by strict rules about what
it's allowed to claim as fact.
"""

from google import genai
from google.genai import types

from . import config

client = genai.Client(api_key=config.GEMINI_API_KEY)

MODEL_NAME = "gemini-2.5-flash"

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
    Sends the conversation to Gemini along with the restaurant's real
    data baked into the system instruction, and returns the assistant's
    reply text.
    """
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(restaurant_context=restaurant_context)

    # Gemini's "contents" list uses role "model" for prior assistant turns
    # (there is no "assistant" role in this API, unlike Claude/OpenAI).
    contents = []
    for turn in history:
        role = "model" if turn.role == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn.content)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=500,
        ),
    )

    return (response.text or "").strip()
