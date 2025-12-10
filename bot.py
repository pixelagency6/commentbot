import os
import logging
import time
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import tweepy

# --- CONFIGURATION ---
# Secrets included as defaults for testing. 
# REGENERATE THESE if you share this code publicly.
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8342514965:AAHiDBgnA-pGrYGdPRUsO_IhRFHFO9xNeTU')

# X (Twitter) API Credentials (APP ID)
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY', 'OoOrE7dhXkfpIx8X34pbzLhEO')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET', 'WuZoacqmGnLsyOenZeD0ZIIVybxTUszg9vjoEDt5wfcIHarOVG')

# X (Twitter) Access Tokens (USER ID)
# These allow the bot to post as YOU.
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN', '1776988097680453633-ZzfpvSYi1J5VdEy6Hcxr20htpau8pQ')
TWITTER_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_SECRET', '9IN8pzdbaa8LcoL8OXyFkjBdRv2f8duIMTatbE03sHviH')

# Port is required by Render
PORT = int(os.environ.get('PORT', 5000))

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- FLASK SERVER (Keep-Alive for Render) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "X Comment Bot is running!", 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

# --- X (TWITTER) LOGIC ---
def get_twitter_client():
    # Check for missing tokens before trying to connect
    if not TWITTER_ACCESS_TOKEN or not TWITTER_ACCESS_SECRET:
        logger.error("Missing Access Tokens! Bot cannot post.")
        return None

    return tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET
    )

def extract_tweet_id(url):
    try:
        # Handles https://x.com/user/status/123456?s=20
        # Split by /status/ and take the right side, then split by ? and take the left side
        return url.split('/status/')[1].split('?')[0]
    except IndexError:
        return None

def process_batch_comments(chat_id, urls, comment_text, count, application):
    """
    Background task to process the list of URLs.
    """
    client = get_twitter_client()
    
    async def send_update(text):
        await application.bot.send_message(chat_id=chat_id, text=text)

    # Use a separate event loop for the async send_message call inside this thread
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if not client:
        loop.run_until_complete(send_update("❌ Error: Missing Twitter Access Tokens. Please set TWITTER_ACCESS_TOKEN and TWITTER_ACCESS_SECRET."))
        loop.close()
        return

    total_ops = len(urls) * count
    current_op = 0

    loop.run_until_complete(send_update(f"🏁 Starting batch job: {len(urls)} posts, {count} comments each."))

    for url in urls:
        tweet_id = extract_tweet_id(url.strip())
        if not tweet_id:
            loop.run_until_complete(send_update(f"❌ Skipped invalid URL: {url}"))
            continue

        for i in range(count):
            current_op += 1
            try:
                # Post the comment
                response = client.create_tweet(text=comment_text, in_reply_to_tweet_id=tweet_id)
                logger.info(f"Commented on {tweet_id}: {response.data['id']}")
                
                # Check if it's the last one to avoid unnecessary waiting
                if current_op < total_ops:
                    wait_time = 60  # 60 seconds delay to reduce ban risk
                    loop.run_until_complete(send_update(
                        f"✅ ({current_op}/{total_ops}) Commented on {url}\n"
                        f"⏳ Waiting {wait_time}s to avoid spam detection..."
                    ))
                    time.sleep(wait_time)
                else:
                    loop.run_until_complete(send_update(f"✅ ({current_op}/{total_ops}) Commented on {url}"))

            except tweepy.TooManyRequests:
                loop.run_until_complete(send_update("⚠️ Rate Limit Hit! Pausing for 15 minutes..."))
                time.sleep(900) # Wait 15 mins
            except Exception as e:
                loop.run_until_complete(send_update(f"❌ Error on {url}: {str(e)}"))
                # If error is fatal (auth), break? For now, we continue to next.

    loop.run_until_complete(send_update("🎉 Batch job completed!"))
    loop.close()

# --- TELEGRAM BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐦 **X (Twitter) Engagement Bot**\n\n"
        "Usage:\n"
        "`/batch <count> <comment text>`\n"
        "`<url1>`\n"
        "`<url2>`\n\n"
        "**Example:**\n"
        "/batch 2 Great post!\n"
        "https://x.com/user/status/123\n"
        "https://x.com/user/status/456",
        parse_mode='Markdown'
    )

async def handle_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    lines = message_text.split('\n')
    
    # Parse the first line: /batch <count> <comment...>
    first_line_parts = lines[0].split(' ')
    
    if len(first_line_parts) < 3:
        await update.message.reply_text("❌ Format error. Usage:\n/batch <count> <text>\n<url1>\n<url2>")
        return

    try:
        count = int(first_line_parts[1])
    except ValueError:
        await update.message.reply_text("❌ Count must be a number.")
        return

    comment_text = ' '.join(first_line_parts[2:])
    
    # The rest of the lines are URLs
    urls = [line.strip() for line in lines[1:] if line.strip().startswith('http')]

    if not urls:
        await update.message.reply_text("❌ No URLs found in the message.")
        return

    await update.message.reply_text(f"📋 Received {len(urls)} URLs. Processing in background...")

    # Run in a separate thread so we don't block the bot from responding to other commands
    t = threading.Thread(
        target=process_batch_comments, 
        args=(update.effective_chat.id, urls, comment_text, count, context.application)
    )
    t.start()

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Start Flask (for Render)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram Bot
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_TOKEN not found.")
    else:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('batch', handle_batch))
        
        print("Bot is polling...")
        application.run_polling()
