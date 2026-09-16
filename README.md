# Telegram AI Bot (Gemini + Groq + OpenRouter, 100% free)

## What this does
A Telegram bot that joins your group chat and replies using AI whenever someone
mentions it (`@yourbotname`) or replies to one of its messages. In a private chat
with the bot, it replies to everything. It remembers the last few messages per
chat so it has short-term context.

It supports **three free AI providers with automatic fallback**: Gemini, Groq,
and OpenRouter. If Gemini hits its rate limit, the bot automatically tries Groq,
then OpenRouter, before giving up. You only need to set up ONE of these to get
started, but adding all three makes the bot much less likely to ever hit "sorry,
I'm rate limited."

It also has some fun extras: `/roll`, `/flip`, `/8ball`, `/joke`, `/fact`,
`/wyr`, `/roast`, plus `/persona` to change its personality per chat.

## Files
- `bot.py` — the bot
- `requirements.txt` — Python dependencies
- `runtime.txt` — pins the Python version Render uses (important, see Step 4)

## Step 1: Create your Telegram bot
1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a name and a username (must end in "bot", e.g. `mygroup_ai_bot`).
3. BotFather gives you a **token** like `123456789:ABCdefGhIJKlmNoPQRstuVWXyz`. Save it.
4. Send `/setprivacy` to BotFather, select your bot, choose **Disable**.
   - This lets the bot read all group messages (needed to detect mentions/replies).
   - If you skip this, the bot will only ever see messages sent as commands.
5. Add your bot to your group chat like any other member.

## Step 2: Get free AI API keys
You need **at least one** of these. Getting all three gives the bot automatic
fallback so one provider's rate limit never fully stops it.

**Gemini (you already have this)**
1. Go to https://aistudio.google.com/app/apikey
2. Sign in with a Google account, click **Create API key**, copy it.

**Groq (free, very fast, Llama models)**
1. Go to https://console.groq.com/keys
2. Sign up / log in, click **Create API Key**, copy it.

**OpenRouter (free tier models available)**
1. Go to https://openrouter.ai/keys
2. Sign up / log in, click **Create Key**, copy it.
3. Note: OpenRouter's free models (like the one this bot uses by default) have
   their own separate rate limits — check https://openrouter.ai/docs#limits if
   you want to swap the model used in `bot.py` (`OPENROUTER_MODEL` near the top).

## Step 3: Put the code on GitHub
1. Create a free GitHub account if you don't have one: https://github.com
2. Create a new repository (e.g. `telegram-ai-bot`).
3. Upload `bot.py`, `requirements.txt`, and `runtime.txt` to it (via the GitHub web UI "Add file" → "Upload files", or `git push` if you know git).

## Step 4: Deploy on Render (free)
1. Go to https://render.com and sign up (free, can use GitHub login).
2. Click **New +** → **Web Service**.
3. Connect your GitHub account and select the repo you just created.
4. Fill in:
   - **Name**: anything, e.g. `telegram-ai-bot`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: Free
5. Under **Environment Variables**, add:
   - `TELEGRAM_BOT_TOKEN` = the token from BotFather
   - `GEMINI_API_KEY` = your Gemini key (optional if you set Groq/OpenRouter instead)
   - `GROQ_API_KEY` = your Groq key (optional)
   - `OPENROUTER_API_KEY` = your OpenRouter key (optional)
   - `PYTHON_VERSION` = `3.11.9` (belt-and-suspenders alongside `runtime.txt` — Render sometimes ignores one or the other)

   You need at least one of the three AI keys set. The bot tries them in this
   order: **Gemini → Groq → OpenRouter**, falling through automatically if one
   fails or is rate-limited.

   Optionally, you can also set which specific model each provider uses,
   without touching any code:
   - `GEMINI_MODEL` (default: `gemini-2.0-flash`)
   - `GROQ_MODEL` (default: `llama-3.3-70b-versatile`)
   - `OPENROUTER_MODEL` (default: `meta-llama/llama-3.1-8b-instruct:free`)

   To change a model, just edit the env var on Render and hit **Manual Deploy →
   Restart service** — no repo changes or rebuild needed.
6. Click **Create Web Service**. Render will build and start it — watch the logs; you should see `Bot starting...`.

   **If you already created the service before adding `runtime.txt`:** Render caches the Python version it picked at first build. After uploading `runtime.txt` to your repo, go to your service on Render → **Manual Deploy** → **Clear build cache & deploy** so it picks up Python 3.11 instead of reusing the old one.

## Step 5: Test it
In your Telegram group, type `@yourbotname hello` — it should reply within a few seconds.
Use `/reset` in a chat to wipe that chat's memory.

## Changing models later
Since `GEMINI_MODEL`, `GROQ_MODEL`, and `OPENROUTER_MODEL` are environment
variables, you can swap models anytime from the Render dashboard — no GitHub
push, no rebuild. Just update the env var and restart the service. Where to
find valid model names:
- Gemini: https://ai.google.dev/gemini-api/docs/models
- Groq: https://console.groq.com/docs/models
- OpenRouter (filter by "Free"): https://openrouter.ai/models

## Fun commands
- `/roll 2d6` — roll dice (any NdM, e.g. `1d20`, `3d8`)
- `/flip` — flip a coin
- `/8ball <question>` — ask the magic 8-ball a yes/no question
- `/joke` — random joke
- `/fact` — random interesting fact
- `/wyr` — a "would you rather" question to spark group debate
- `/roast` — reply to someone's message with `/roast` (or `/roast <text>`) for a playful, non-mean roast

These use whichever AI provider is currently working (same fallback chain as
normal chat), except `/roll` and `/flip` which are instant and don't use AI at all.

## Changing the bot's personality
Each chat can have its own personality, changed on the fly with `/persona`:

- `/persona` — shows the current personality
- `/persona You are a sarcastic pirate who reluctantly gives good advice.` — sets a new one
- `/persona reset` — goes back to the default personality

Changing the personality also clears that chat's memory, so the new personality
starts clean instead of half-mixed with the old one. The personality is stored
in RAM (like chat memory), so it resets to default if the bot restarts/redeploys —
if you want a persona to always come back after a restart, set it as the
`DEFAULT_SYSTEM_PROMPT` in `bot.py` instead.

## Notes & limits
- **Free Render web services sleep after 15 minutes of no web traffic.** This bot runs a tiny built-in health-check server to keep Render satisfied, but Render may still spin it down when idle — the first message after a period of inactivity might take ~30-60 seconds to get a reply while it wakes up. If you want zero sleep, look into Oracle Cloud's always-free VM tier instead, or a Render paid instance ($7/mo).
- Chat memory is stored in RAM only — it resets whenever the bot restarts/redeploys.
- Gemini's free tier has rate limits (requests per minute/day). Fine for a normal group chat; heavy use may hit limits.
- Keep your tokens/keys secret — never post them publicly or commit them into your repo. They're set as environment variables specifically so they stay out of your code.
