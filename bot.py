import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))

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

# ---------- GEMINI SETUP ----------
genai.configure(api_key=GEMINI_API_KEY)

# In-memory per-chat state
chat_histories = {}   # {chat_id: [ {role, parts}, ... ]}
chat_personas = {}    # {chat_id: "custom system prompt"}


def get_persona(chat_id):
    return chat_personas.get(chat_id, DEFAULT_SYSTEM_PROMPT)


def get_model_for_chat(chat_id):
    # Gemini's system_instruction is set per-model, so we build a model
    # instance using whatever persona is currently active for this chat.
    return genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=get_persona(chat_id),
    )


def get_history(chat_id):
    return chat_histories.setdefault(chat_id, [])


def trim_history(chat_id):
    history = chat_histories[chat_id]
    if len(history) > MAX_HISTORY_MESSAGES:
        chat_histories[chat_id] = history[-MAX_HISTORY_MESSAGES:]


# ---------- TELEGRAM HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm your AI assistant. Mention me or reply to my messages and I'll respond.\n\n"
        "/reset - clear my memory of this chat\n"
        "/persona <description> - change my personality for this chat\n"
        "/persona - show my current personality\n"
        "/persona reset - go back to default personality"
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
        chat_histories[chat_id] = []  # clear memory so old personality doesn't bleed through
        await update.message.reply_text("Personality reset to default. Memory cleared too.")
        return

    chat_personas[chat_id] = new_persona
    chat_histories[chat_id] = []  # clear memory so the new personality applies cleanly
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

    # In groups, only respond when mentioned or replied to.
    # In private chats, always respond.
    if is_group and not (is_mentioned or is_reply_to_bot):
        return

    user_text = message.text.replace(f"@{bot_username}", "").strip()
    if not user_text:
        return

    chat_id = chat.id
    history = get_history(chat_id)

    try:
        model = get_model_for_chat(chat_id)
        chat_session = model.start_chat(history=history)
        response = chat_session.send_message(user_text)
        reply_text = response.text

        history.append({"role": "user", "parts": [user_text]})
        history.append({"role": "model", "parts": [reply_text]})
        trim_history(chat_id)

        await message.reply_text(reply_text)
    except Exception as e:
        logger.exception("Error generating response")
        await message.reply_text(f"Sorry, something went wrong: {e}")


# ---------- TINY HEALTH-CHECK SERVER (keeps Render happy) ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # silence default request logging


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# ---------- MAIN ----------
def main():
    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("persona", persona))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
