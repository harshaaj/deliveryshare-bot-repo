import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token - REPLACE WITH YOUR TOKEN
TOKEN = '8242502848:AAGbwSFVpo0JpJvE099oyXCR9vHp0CfB_i4'

# Store user data
users = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    user_id = str(update.effective_user.id)
    users[user_id] = {'step': 'started'}
    
    keyboard = [
        [
            InlineKeyboardButton("📱 Open App", callback_data="open_app"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        '👋 Welcome to DeliveryShare Bot!\n\n'
        'I can help you share delivery costs with others.\n'
        'How can I help you today?',
        reply_markup=reply_markup
    )
    logger.info(f"New user started: {user_id}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a help message when the command /help is issued."""
    help_text = (
        '🤖 *DeliveryShare Bot Help*\n\n'
        '*/start* - Start the bot\n'
        '*/help* - Show this help message\n'
        '*/end* - End the current session\n\n'
        'Simply follow the prompts to find someone to share delivery costs with!'
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button presses."""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'open_app':
        keyboard = [
            [
                InlineKeyboardButton("Zepto", callback_data="app_zepto"),
                InlineKeyboardButton("Swiggy", callback_data="app_swiggy")
            ],
            [
                InlineKeyboardButton("Zomato", callback_data="app_zomato"),
                InlineKeyboardButton("Other", callback_data="app_other")
            ]
        ]
        await query.edit_message_text(
            '📱 *Select your delivery app*',
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    elif query.data == 'help':
        await help_command(update, context)
    elif query.data.startswith('app_'):
        app_name = query.data[4:].capitalize()
        await query.edit_message_text(f'✅ You selected: {app_name}\n\nPlease share your cart total:')
        users[query.from_user.id]['step'] = 'sharing_cart'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages."""
    user_id = str(update.effective_user.id)
    
    if user_id not in users:
        await update.message.reply_text('Please start the bot with /start')
        return
    
    text = update.message.text
    user_data = users[user_id]
    
    if user_data.get('step') == 'sharing_cart':
        try:
            # Try to convert the message to a number
            amount = float(text)
            user_data['cart_total'] = amount
            user_data['step'] = 'sharing_location'
            await update.message.reply_text(
                f'📝 Your cart total: ₹{amount:.2f}\n\n'
                'Now, please share your delivery location.',
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📍 Share Location", request_location=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
            )
        except ValueError:
            await update.message.reply_text('Please enter a valid number for your cart total.')
    else:
        await update.message.reply_text(
            'I\'m not sure what you\'re trying to do. '
            'Use /start to begin or /help for assistance.'
        )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle location sharing."""
    user_id = str(update.effective_user.id)
    if user_id in users and users[user_id].get('step') == 'sharing_location':
        location = update.message.location
        users[user_id]['location'] = (location.latitude, location.longitude)
        users[user_id]['step'] = 'searching'
        
        await update.message.reply_text(
            '🔍 Searching for potential matches...\n\n'
            'I\'ll notify you when I find someone to share delivery with!',
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text('Please share your cart details first!')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    
    # Notify the user
    if update and hasattr(update, 'effective_chat') and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An error occurred. Please try again or use /start to begin a new session."
            )
        except Exception as e:
            logger.error("Error sending error message: %s", e)

def main() -> None:
    """Start the bot."""
    print("=== Starting DeliveryShare Bot ===")
    
    # Create the Application
    application = Application.builder().token(TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # Handle button presses
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Handle messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Handle location sharing
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    
    # Log all errors
    application.add_error_handler(error_handler)
    
    # Start the Bot
    print("Bot is starting...")
    print("Press Ctrl+C to stop the bot")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"\nError: {str(e)}")
        print("\nPlease check:")
        print("1. Your internet connection")
        print("2. The bot token is correct")
        print("3. Required packages are installed")
        print("\nTry running: pip install python-telegram-bot --upgrade")
        input("\nPress Enter to exit...")
