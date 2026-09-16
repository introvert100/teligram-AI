import os
import logging
import threading
import asyncio
import random
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# All AI provider keys are optional EXCEPT you must set at least one.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

PORT = int(os.environ.get("PORT", 10000))

# Models used per provider (free-tier friendly). Override via env vars so you
# can switch models without touching code or redeploying from GitHub.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Default personality. Used until someone changes it with /persona in a given chat.
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, friendly AI assistant inside a Telegram group chat. "
    "Keep replies concise and conversational unless asked for detail."
)

MAX_HISTORY_MESSAGES = 12  # how many past messages to remember per chat

# ---------- LOGGING ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

if not (GEMINI_API_KEY or GROQ_API_KEY or OPENROUTER_API_KEY):
    raise RuntimeError(
        "Set at least one of GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY."
    )

# In-memory per-chat state
# history format is unified across providers: [{"role": "user"/"assistant", "content": "..."}]
chat_histories = {}
chat_personas = {}


def get_persona(chat_id):
    return chat_personas.get(chat_id, DEFAULT_SYSTEM_PROMPT)


def get_history(chat_id):
    return chat_histories.setdefault(chat_id, [])


def trim_history(chat_id):
    history = chat_histories[chat_id]
    if len(history) > MAX_HISTORY_MESSAGES:
        chat_histories[chat_id] = history[-MAX_HISTORY_MESSAGES:]


# ---------- PROVIDER CALLS ----------
# Each function takes (system_prompt, history, user_text) and returns reply text,
# or raises an exception on failure (quota, network error, bad response, etc).

def call_gemini(system_prompt, history, user_text):
    model = genai.GenerativeModel(model_name=GEMINI_MODEL, system_instruction=system_prompt)
    gemini_history = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
        for m in history
    ]
    chat_session = model.start_chat(history=gemini_history)
    response = chat_session.send_message(user_text)
    return response.text


def _call_openai_compatible(url, api_key, model, system_prompt, history, user_text, extra_headers=None):
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    messages.append({"role": "user", "content": user_text})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    resp = requests.post(
        url,
        headers=headers,
        json={"model": model, "messages": messages},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_groq(system_prompt, history, user_text):
    return _call_openai_compatible(
        "https://api.groq.com/openai/v1/chat/completions",
        GROQ_API_KEY,
        GROQ_MODEL,
        system_prompt,
        history,
        user_text,
    )


def call_openrouter(system_prompt, history, user_text):
    return _call_openai_compatible(
        "https://openrouter.ai/api/v1/chat/completions",
        OPENROUTER_API_KEY,
        OPENROUTER_MODEL,
        system_prompt,
        history,
        user_text,
        extra_headers={"HTTP-Referer": "https://render.com", "X-Title": "Telegram AI Bot"},
    )


# Providers tried in this order. Only ones with a key set are actually used.
PROVIDERS = [
    ("Gemini", GEMINI_API_KEY, call_gemini),
    ("Groq", GROQ_API_KEY, call_groq),
    ("OpenRouter", OPENROUTER_API_KEY, call_openrouter),
]


def generate_reply(system_prompt, history, user_text):
    """Try each configured provider in order until one succeeds."""
    last_error = None
    for name, key, fn in PROVIDERS:
        if not key:
            continue
        try:
            reply = fn(system_prompt, history, user_text)
            logger.info(f"Reply generated using {name}")
            return reply
        except Exception as e:
            logger.warning(f"{name} failed: {e}")
            last_error = e
            continue
    raise RuntimeError(f"All providers failed. Last error: {last_error}")


def ask_once(system_prompt, user_text):
    """One-off AI call with no chat history/memory — used for fun commands."""
    return generate_reply(system_prompt, [], user_text)


# ---------- TELEGRAM HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm your AI assistant. Mention me or reply to my messages and I'll respond.\n\n"
        "General:\n"
        "/reset - clear my memory of this chat\n"
        "/persona <description> - change my personality for this chat\n"
        "/persona - show my current personality\n"
        "/persona reset - go back to default personality\n\n"
        "Fun:\n"
        "/roll [NdM] - roll dice, e.g. /roll 2d6\n"
        "/flip - flip a coin\n"
        "/8ball <question> - ask the magic 8-ball\n"
        "/joke - tell a random joke\n"
        "/fact - random interesting fact\n"
        "/wyr - would-you-rather question\n"
        "/roast - reply to someone's message with this to roast it (playfully)"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    await update.message.reply_text("Memory cleared for this chat.")


async def persona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    new_persona = " ".join(context.args).strip() if context.args else ""

    if not new_persona:
        current = get_persona(chat_id)
        await update.message.reply_text(
            "Current personality:\n"
            f"{current}\n\n"
            "To change it, send:\n"
            "/persona <description of how the bot should act>\n\n"
            "Example:\n"
            "/persona You are a sarcastic pirate who reluctantly gives good advice.\n\n"
            "Send /persona reset to go back to default."
        )
        return

    if new_persona.lower() == "reset":
        chat_personas.pop(chat_id, None)
        chat_histories[chat_id] = []
        await update.message.reply_text("Personality reset to default. Memory cleared too.")
        return

    chat_personas[chat_id] = new_persona
    chat_histories[chat_id] = []
    await update.message.reply_text(f"Personality updated for this chat:\n{new_persona}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    chat = update.effective_chat
    bot_username = context.bot.username

    is_group = chat.type in ("group", "supergroup")
    is_mentioned = bot_username and f"@{bot_username}" in message.text
    is_reply_to_bot = (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == context.bot.id
    )

    if is_group and not (is_mentioned or is_reply_to_bot):
        return

    user_text = message.text.replace(f"@{bot_username}", "").strip()
    if not user_text:
        return

    chat_id = chat.id
    history = get_history(chat_id)

    try:
        reply_text = generate_reply(get_persona(chat_id), history, user_text)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        trim_history(chat_id)
        await message.reply_text(reply_text)
    except Exception:
        logger.exception("Error generating response")
        await message.reply_text(
            "Sorry, all AI providers are unavailable right now (rate limits or an error). "
            "Try again in a bit."
        )


# ---------- FUN COMMANDS ----------
async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = context.args[0] if context.args else "1d6"
    match = re.match(r"^(\d+)d(\d+)$", arg.lower())
    if not match:
        await update.message.reply_text("Usage: /roll 2d6  (rolls two 6-sided dice)")
        return

    count, sides = int(match.group(1)), int(match.group(2))
    if count < 1 or count > 20 or sides < 2 or sides > 1000:
        await update.message.reply_text("Keep it reasonable: 1-20 dice, 2-1000 sides.")
        return

    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    text = f"🎲 {rolls} = {total}" if count > 1 else f"🎲 {total}"
    await update.message.reply_text(text)


async def flip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = random.choice(["Heads", "Tails"])
    await update.message.reply_text(f"🪙 {result}!")


async def eightball(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text("Ask me something! Usage: /8ball Will it rain tomorrow?")
        return
    try:
        reply = ask_once(
            "You are a magic 8-ball. Reply to the yes/no question with a short, "
            "classic magic-8-ball-style answer (e.g. 'It is certain', 'Ask again later', "
            "'Don't count on it'). One line only, a bit mysterious/fun. No explanation.",
            question,
        )
        await update.message.reply_text(f"🎱 {reply.strip()}")
    except Exception:
        logger.exception("8ball failed")
        await update.message.reply_text("The magic 8-ball is out of batteries right now. Try again later.")


async def joke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reply = ask_once(
            "You tell short, clean, witty jokes suitable for a group chat. "
            "Give exactly one joke, no preamble.",
            "Tell me a random joke.",
        )
        await update.message.reply_text(reply.strip())
    except Exception:
        logger.exception("joke failed")
        await update.message.reply_text("Couldn't think of a joke right now — all providers are busy.")


async def fact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reply = ask_once(
            "You share short, genuinely interesting, true random facts. "
            "Give exactly one fact in 1-2 sentences, no preamble.",
            "Give me a random interesting fact.",
        )
        await update.message.reply_text(f"💡 {reply.strip()}")
    except Exception:
        logger.exception("fact failed")
        await update.message.reply_text("Couldn't fetch a fact right now — all providers are busy.")


async def wyr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reply = ask_once(
            "You generate fun, creative 'would you rather' questions for a group chat to debate. "
            "Give exactly one, formatted as: Would you rather ... or ...? No preamble, no explanation.",
            "Give me a would-you-rather question.",
        )
        await update.message.reply_text(f"🤔 {reply.strip()}")
    except Exception:
        logger.exception("wyr failed")
        await update.message.reply_text("Couldn't come up with one right now — all providers are busy.")


async def roast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    target_text = " ".join(context.args).strip()

    if message.reply_to_message and message.reply_to_message.text:
        target_text = message.reply_to_message.text

    if not target_text:
        await update.message.reply_text(
            "Reply to a message with /roast, or use /roast <text>."
        )
        return

    try:
        reply = ask_once(
            "You give short, funny, PLAYFUL roasts — witty and light, never actually mean, "
            "never about protected characteristics (race, religion, gender, etc), "
            "never about appearance in a hurtful way. Keep it to 1-2 sentences, clearly in good fun.",
            f"Playfully roast this message: {target_text}",
        )
        await update.message.reply_text(reply.strip())
    except Exception:
        logger.exception("roast failed")
        await update.message.reply_text("Couldn't roast that right now — all providers are busy.")


# ---------- TINY HEALTH-CHECK SERVER (keeps Render happy) ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# ---------- MAIN ----------
def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("persona", persona))
    app.add_handler(CommandHandler("roll", roll))
    app.add_handler(CommandHandler("flip", flip))
    app.add_handler(CommandHandler("8ball", eightball))
    app.add_handler(CommandHandler("joke", joke))
    app.add_handler(CommandHandler("fact", fact))
    app.add_handler(CommandHandler("wyr", wyr))
    app.add_handler(CommandHandler("roast", roast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    active_providers = [name for name, key, _ in PROVIDERS if key]
    logger.info(f"Bot starting... active providers (in fallback order): {active_providers}")
    app.run_polling()


if __name__ == "__main__":
    main()
