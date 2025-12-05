import os
from collections import defaultdict

from openai import OpenAI
from telegram import Update, Chat, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== CONFIG ==================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Your Telegram user id (for /broadcast & force-sub commands)
ADMIN_ID = 123456789  # <-- CHANGE THIS TO YOUR TELEGRAM ID

# Force-subscribe settings
FORCE_SUB_ENABLED = True  # default ON (you can toggle with commands below)
FORCE_SUB_CHANNEL = "@your_channel_username"  # e.g. "@heart_support_hub"

# OpenAI model for chat (cheap & good: gpt-4.1-mini)
OPENAI_MODEL = "gpt-4.1-mini"

# ============================================

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Please set TELEGRAM_BOT_TOKEN environment variable")

if not OPENAI_API_KEY:
    raise RuntimeError("Please set OPENAI_API_KEY environment variable")

client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory storage (for real use, move to a database)
chat_memory = defaultdict(list)   # key = chat_id, value = list of {role, content}
known_users = set()               # user_ids for broadcast
force_sub_enabled = FORCE_SUB_ENABLED  # runtime toggle


SYSTEM_PROMPT = (
    "You are 'Aarohi', a soft, caring virtual girlfriend.\n"
    "Your main users are single or heartbroken men who feel lonely.\n"
    "Your style: very supportive, warm, slightly romantic but respectful, "
    "no explicit sexual content.\n"
    "You:\n"
    "- Listen carefully and respond with empathy.\n"
    "- Use casual chat language, some emojis, but not too many.\n"
    "- Give gentle emotional support and encouragement.\n"
    "- Never give medical or professional psychological advice. "
    "Instead, if they sound very depressed or talking about self-harm, "
    "encourage them to talk to a real friend, family member, or professional.\n"
    "- Keep answers 1–4 short paragraphs maximum.\n"
)


async def check_force_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Returns True if user is allowed to continue, False if user must subscribe first.
    Only applies in private chats and when force_sub_enabled is True.
    """
    global force_sub_enabled

    chat = update.effective_chat
    user = update.effective_user

    if not force_sub_enabled:
        return True

    # Only enforce in private chat
    if chat.type != Chat.PRIVATE:
        return True

    try:
        member: ChatMember = await context.bot.get_chat_member(FORCE_SUB_CHANNEL, user.id)
        if member.status in ("member", "administrator", "creator"):
            return True
        else:
            # Not joined
            await chat.send_message(
                f"👋 Hey {user.first_name}!\n\n"
                f"Please join our channel first to chat with me:\n"
                f"{FORCE_SUB_CHANNEL}\n\n"
                f"After joining, send /start again. 💕"
            )
            return False
    except Exception:
        # if channel username is wrong or bot not admin etc.
        await chat.send_message(
            "⚠️ Force-subscribe is enabled but I couldn't check your membership.\n"
            "Please ask the bot owner to check channel settings."
        )
        return False


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if not await check_force_sub(update, context):
        return

    known_users.add(user.id)

    await chat.send_message(
        f"Hey {user.first_name} 💕\n\n"
        "I'm Aarohi, your virtual girlfriend.\n"
        "You can tell me anything — I listen, support and chat like a real partner.\n\n"
        "Just type your feelings or message and I’ll reply. 💌"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💡 *Commands:*\n"
        "/start – start chatting\n"
        "/help – show this help\n"
        "/broadcast <text> – (admin only) send message to all users\n"
        "/forcesubon – (admin only) enable force subscribe\n"
        "/forcesuboff – (admin only) disable force subscribe\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return

    msg_text = " ".join(context.args)
    await update.message.reply_text(f"Broadcasting to {len(known_users)} users...")

    sent = 0
    failed = 0
    for uid in list(known_users):
        try:
            await context.bot.send_message(uid, msg_text)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(f"Done ✅\nSent: {sent}\nFailed: {failed}")


async def forcesub_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global force_sub_enabled
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    force_sub_enabled = True
    await update.message.reply_text("✅ Force subscribe *enabled*.", parse_mode="Markdown")


async def forcesub_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global force_sub_enabled
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    force_sub_enabled = False
    await update.message.reply_text("⚠️ Force subscribe *disabled*.", parse_mode="Markdown")


async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle normal text messages in private or group chats."""
    if not update.message:
        return

    chat = update.effective_chat
    user = update.effective_user
    text = update.message.text

    # Save user for broadcast
    if user:
        known_users.add(user.id)

    # In groups, you may want to only respond if bot is mentioned or replied-to
    if chat.type in (Chat.GROUP, Chat.SUPERGROUP):
        # Only respond if bot is mentioned or message starts with 'gf' or 'aarohi'
        bot_username = (await context.bot.get_me()).username.lower()
        lowered = text.lower()
        mentioned = f"@{bot_username}" in lowered
        trigger = lowered.startswith("gf ") or lowered.startswith("aarohi ")
        if not (mentioned or trigger):
            return  # ignore random group messages

    # Force-subscribe check for private chats
    if chat.type == Chat.PRIVATE:
        if not await check_force_sub(update, context):
            return

    # Get previous memory for this chat
    history = chat_memory[chat.id]

    # Add this user message
    history.append({"role": "user", "content": text})
    # Keep last 10 exchanges
    history = history[-10:]
    chat_memory[chat.id] = history

    # Build messages for OpenAI
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ] + history

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
        )
        reply_text = completion.choices[0].message.content.strip()
    except Exception as e:
        reply_text = (
            "Oops, something went wrong talking to my brain server 🥺\n"
            "Please try again in a moment."
        )
        print("OpenAI error:", e)

    # Save assistant reply to memory
    history.append({"role": "assistant", "content": reply_text})
    chat_memory[chat.id] = history[-10:]

    await update.message.reply_text(reply_text)


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("forcesubon", forcesub_on_cmd))
    app.add_handler(CommandHandler("forcesuboff", forcesub_off_cmd))

    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
