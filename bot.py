import os
import logging
import time
import threading
import asyncio
from flask import Flask
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import tweepy

# --- CONFIGURATION ---
# It is highly recommended to set these in Render Dashboard -> Environment Variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8342514965:AAHiDBgnA-pGrYGdPRUsO_IhRFHFO9xNeTU')

# X (Twitter) API Credentials
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY', 'OoOrE7dhXkfpIx8X34pbzLhEO')
TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET', 'WuZoacqmGnLsyOenZeD0ZIIVybxTUszg9vjoEDt5wfcIHarOVG')

# X (Twitter) Access Tokens
TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN', '1776988097680453633-rqPgtFFFdDwPNMhKGLCRpOZh1Jj5p2')
TWITTER_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_SECRET', 'nRPNuNtQDylIQiHP5k1lSlotBEcwn6WpAsMq4VbDl4EGf')

# Port is required by Render for Web Services
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
    # Use 0.0.0.0 to allow external access from Render's load balancer
    app.run(host='0.0.0.0', port=PORT)

# --- X (TWITTER) LOGIC ---
def get_twitter_client():
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
        return url.split('/status/')[1].split('?')[0]
    except IndexError:
        return None

def process_batch_comments(chat_id, urls, comment_text, count, token):
    """
    Background task to process the list of URLs.
    """
    client = get_twitter_client()
    bot = Bot(token=token)

    async def send_update(text):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error(f"Failed to send Telegram update: {e}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if not client:
        loop.run_until_complete(send_update("❌ Error: Missing Twitter Access Tokens."))
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
                response = client.create_tweet(text=comment_text, in_reply_to_tweet_id=tweet_id)
                logger.info(f"Commented on {tweet_id}: {response.data['id']}")
                
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
                time.sleep(900)
            except Exception as e:
                loop.run_until_complete(send_update(f"❌ Error on {url}: {str(e)}"))

    loop.run_until_complete(send_update("🎉 Batch job completed!"))
    loop.close()

# --- TELEGRAM BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐦 **X (Twitter) Engagement Bot**\n\n"
        "Usage:\n"
        " `/batch <count> <comment text>`\n"
        " `<url1>`\n"
        " `<url2>`\n\n"
        "**Example:**\n"
        "/batch 2 Great post!\n"
        "https://x.com/user/status/123\n"
        "https://x.com/user/status/456",
        parse_mode='Markdown'
    )

async def handle_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    lines = message_text.split('\n')
    
    first_line_parts = lines[0].split(' ')
    
    if len(first_line_parts) < 3:
        await update.message.reply_text("❌ Format error. Usage: /batch <count> <text>")
        return

    try:
        count = int(first_line_parts[1])
    except ValueError:
        await update.message.reply_text("❌ Count must be a number.")
        return

    comment_text = ' '.join(first_line_parts[2:])
    urls = [line.strip() for line in lines[1:] if line.strip().startswith('http')]

    if not urls:
        await update.message.reply_text("❌ No URLs found.")
        return

    await update.message.reply_text(f"📋 Received {len(urls)} URLs. Processing...")

    t = threading.Thread(
        target=process_batch_comments, 
        args=(update.effective_chat.id, urls, comment_text, count, TELEGRAM_TOKEN)
    )
    t.start()

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Start Flask (for Render Health Checks)
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
        
        print("Bot is starting...")
        application.run_polling()
