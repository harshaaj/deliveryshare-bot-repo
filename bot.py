import asyncio
import logging
import os
import re
import traceback
import sys
import subprocess
import time
from math import radians, sin, cos, sqrt, asin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
import random
import string

# Check for apscheduler
try:
    import apscheduler
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

# Load environment variables from .env file
load_dotenv()

# Get the bot token from environment variable
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN environment variable not set!")
    print("Please create a .env file with TELEGRAM_BOT_TOKEN=your_token_here")
    exit(1)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler('bot.log', encoding='utf-8')]
)
logger = logging.getLogger(__name__)

# Global variables
users = {}  # {'user_id': {'step': str, 'pseudonym': str, 'app': str, 'location': tuple, 'cart_total': float, 'items': str, 'min_for_free': float, 'matched_with': str, 'chat_active': bool, 'chat_requested': bool, 'chat_id': str}}
carts = []  # {'cart_id': str, 'user_id': str, 'pseudonym': str, 'app': str, 'location': tuple, 'cart_total': float, 'items': str, 'min_for_free': float}
active_chats = {}  # {'user_id': 'partner_id'} for active anonymous chats

def generate_pseudonym():
    """Generate a random pseudonym for anonymous chat."""
    return f"Shopper_{''.join(random.choices(string.ascii_letters + string.digits, k=6))}"

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user_id = str(update.effective_user.id)
    pseudonym = generate_pseudonym()
    users[user_id] = {'step': 'started', 'pseudonym': pseudonym, 'chat_id': str(update.effective_chat.id)}
    
    keyboard = [
        [
            InlineKeyboardButton("📱 Open App", callback_data="open_app"),
            InlineKeyboardButton("ℹ️ Help", callback_data="help")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f'👋 Hi, {pseudonym}! Welcome to DeliveryShare Bot!\n\n'
        'I can help you share delivery costs with others.\n'
        'How can I help you today?',
        reply_markup=reply_markup
    )
    logger.info(f"New user started: {user_id}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = (
        '🤖 *DeliveryShare Bot Help*\n\n'
        '*/start* - Start the bot\n'
        '*/help* - Show this help message\n'
        '*/end* - End the current session\n\n'
        'Simply follow the prompts to find someone to share delivery costs with!'
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def end_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End the current session."""
    user_id = str(update.effective_user.id)
    
    if user_id in active_chats:
        other_user_id = active_chats[user_id]
        
        active_chats.pop(user_id, None)
        active_chats.pop(other_user_id, None)
        
        carts[:] = [cart for cart in carts if cart['user_id'] not in [user_id, other_user_id]]
        
        if user_id in users:
            users[user_id].pop('matched_with', None)
            users[user_id].pop('chat_active', None)
            users[user_id].pop('chat_requested', None)
            users[user_id]['step'] = 'idle'
            
        if other_user_id in users:
            users[other_user_id].pop('matched_with', None)
            users[other_user_id].pop('chat_active', None)
            users[other_user_id].pop('chat_requested', None)
            users[other_user_id]['step'] = 'idle'
        
        await update.message.reply_text(
            '✅ Session ended. Thank you for using DeliveryShare!\n\n'
            'Start a new session with /start if you want to find another match.',
            reply_markup=ReplyKeyboardRemove()
        )
        
        if other_user_id in users:
            await context.bot.send_message(
                chat_id=other_user_id,
                text='❌ The other user has ended the session.\n\n'
                     'Start a new session with /start if you want to find another match.',
                reply_markup=ReplyKeyboardRemove()
            )
    else:
        carts[:] = [cart for cart in carts if cart['user_id'] != user_id]
        if user_id in users:
            users[user_id]['step'] = 'idle'
            users[user_id].pop('matched_with', None)
            users[user_id].pop('chat_active', None)
            users[user_id].pop('chat_requested', None)
        await update.message.reply_text(
            'No active session. Start a new one with /start',
            reply_markup=ReplyKeyboardRemove()
        )
    logger.info(f"Session ended for user {user_id}")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await end_session(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages."""
    user_id = str(update.effective_user.id)
    
    if user_id not in users:
        await update.message.reply_text("Please start a new session with /start")
        return
        
    text = update.message.text
    user_data = users[user_id]
    
    if user_data.get('chat_active') and user_data.get('matched_with'):
        partner_id = user_data['matched_with']
        if partner_id in users and users[partner_id].get('chat_active'):
            try:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"💬 {user_data['pseudonym']}: {text}",
                    reply_markup=ReplyKeyboardRemove()
                )
                await update.message.reply_text(
                    "✅ Message sent to your partner",
                    reply_markup=ReplyKeyboardRemove()
                )
                logger.info(f"Message forwarded from {user_id} to {partner_id}")
                return
            except Exception as e:
                logger.error(f"Error forwarding message: {e}")
                await update.message.reply_text("❌ Failed to send message. Please try again.")
                return
    
    if user_data.get('step') == 'cart_amount':
        try:
            amount_text = text.strip()
            amount = float(''.join(c for c in amount_text if c.isdigit() or c == '.'))
            if amount <= 0:
                await update.message.reply_text("Please enter a valid amount greater than 0.")
                return
            user_data['cart_total'] = amount
            user_data['step'] = 'min_for_free'
            await update.message.reply_text(
                '💰 *What\'s the minimum order amount for free delivery?*\n\n'
                'Enter the amount (e.g., 500) or just type "300" if you\'re not sure:',
                parse_mode='Markdown'
            )
        except ValueError:
            await update.message.reply_text('Please enter a valid number for cart total (e.g., 250.50).')
        return
        
    elif user_data.get('step') == 'min_for_free':
        try:
            min_free_text = text.strip()
            min_free = float(''.join(c for c in min_free_text if c.isdigit() or c == '.'))
            if min_free <= 0:
                await update.message.reply_text("Please enter a valid amount greater than 0.")
                return
                
            user_data['min_for_free'] = min_free
            user_data['step'] = 'location'
            
            # Add user's cart to the global carts list
            cart_id = f"cart_{user_id}_{int(time.time())}"
            cart = {
                'cart_id': cart_id,
                'user_id': user_id,
                'pseudonym': user_data.get('pseudonym', 'User'),
                'app': user_data.get('app', 'Unknown'),
                'location': user_data.get('location'),
                'cart_total': user_data['cart_total'],
                'min_for_free': min_free,
                'timestamp': time.time()
            }
            
            # Remove any existing cart for this user
            carts[:] = [c for c in carts if c['user_id'] != user_id]
            carts.append(cart)
            
            logger.info(f"Added cart for user {user_id}: {cart}")
            
            location_keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton("📍 Share My Location", request_location=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await update.message.reply_text(
                '📍 *Almost there!* Please share your location so we can find nearby matches.\n\n'
                'Click the "📍 Share My Location" button below to continue.',
                reply_markup=location_keyboard,
                parse_mode='Markdown'
            )
        except ValueError:
            await update.message.reply_text('Please enter a valid number for the minimum order amount.')
    
    elif user_data.get('step') in ['location', 'sharing_location']:
        location_keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Share My Location", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await update.message.reply_text(
            'Please share your location using the button below to find nearby matches.',
            reply_markup=location_keyboard
        )
    
    else:
        await update.message.reply_text(
            'I\'m not sure what you\'re trying to do. '
            'Use /start to begin or /help for assistance.'
        )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle location sharing and start searching for matches."""
    try:
        if not update or not update.message or not update.effective_user:
            logger.error("Invalid update object or missing data")
            return
            
        user_id = str(update.effective_user.id)
        chat_id = update.effective_chat.id
        logger.info(f"Processing location from user {user_id}")
        
        if user_id not in users:
            logger.warning(f"User {user_id} not found, sending to start")
            await update.message.reply_text(
                "❌ Please start the bot with /start command first."
            )
            return
            
        user_data = users[user_id]
        
        location = update.message.location
        if not location:
            logger.warning("No location data in message")
            await update.message.reply_text(
                "❌ No location data received. Please try again."
            )
            return
            
        user_data.update({
            'location': (location.latitude, location.longitude),
            'step': 'searching',
            'search_start_time': context.bot_data.get('current_time', 0),
            'chat_id': str(chat_id)
        })
        
        logger.info(f"Location saved for user {user_id}: {user_data['location']}")
        
        cart_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        cart = {
            'cart_id': cart_id,
            'user_id': user_id,
            'pseudonym': user_data['pseudonym'],
            'app': user_data['app'],
            'location': user_data['location'],
            'cart_total': user_data['cart_total'],
            'items': user_data.get('items', 'N/A'),
            'min_for_free': user_data['min_for_free']
        }
        carts.append(cart)
        logger.info(f"Cart added for user {user_id}: {cart}")
        
        await update.message.reply_text(
            '✅ Location received! Starting search...',
            reply_markup=ReplyKeyboardRemove()
        )
        
        if not hasattr(context, 'job_queue') or context.job_queue is None:
            logger.error(f"Job queue not available for user {user_id}! Ensure python-telegram-bot[job-queue] is installed. "
                        f"Installed version: {__import__('telegram').__version__}. APScheduler available: {APSCHEDULER_AVAILABLE}")
            user_data['step'] = 'idle'
            carts[:] = [cart for cart in carts if cart['user_id'] != user_id]
            await context.bot.send_message(
                chat_id=chat_id,
                text='❌ Error: Unable to start search due to a configuration issue. Please try again with /start.',
                reply_markup=ReplyKeyboardRemove()
            )
            return
        
        current_jobs = context.job_queue.get_jobs_by_name(f'search_{user_id}')
        for job in current_jobs:
            job.schedule_removal()
        
        try:
            logger.info(f"Creating search job for user {user_id}")
            context.job_queue.run_repeating(
                search_for_matches_callback,
                interval=10.0,
                first=5,
                data={'user_id': user_id, 'chat_id': str(chat_id)},
                name=f'search_{user_id}'
            )
            logger.info(f"Search job created for user {user_id}")
            
            keyboard = [[InlineKeyboardButton("🛑 Stop Searching", callback_data="stop_search")]]
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    '🔍 Searching for potential matches...\n\n'
                    'I\'ll keep searching until I find someone or you stop the search.'
                ),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Failed to start search job for user {user_id}: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Couldn't start the search. Please try again with /start."
            )
    
    except Exception as e:
        logger.error(f"Unexpected error in handle_location: {e}", exc_info=True)
        if update and update.message:
            await update.message.reply_text(
                "❌ An unexpected error occurred. Please try again or use /start to begin a new session."
            )

async def search_for_matches(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> bool:
    """Search for potential matches for a user."""
    try:
        logger.info(f"=== Starting search_for_matches for user {user_id} ===")
        
        if user_id not in users:
            logger.error(f"User {user_id} not found in users dictionary")
            return False
            
        current_user = users[user_id]
        
        logger.info(f"User {user_id} state: {current_user.get('step')}")
        logger.info(f"User data: {current_user}")
        
        if current_user.get('step') != 'searching':
            logger.warning(f"User {user_id} is not in 'searching' state. Current state: {current_user.get('step')}")
            return False
            
        required_fields = ['app', 'location', 'cart_total', 'min_for_free']
        for field in required_fields:
            if field not in current_user:
                logger.error(f"Missing required field '{field}' for user {user_id}")
                return False
        
        logger.info(f"Searching for matches among {len(carts)} carts...")
        
        for cart in carts[:]:
            try:
                if cart['user_id'] == user_id:
                    logger.debug(f"Skipping own cart: {cart.get('cart_id', 'unknown')}")
                    continue
                    
                logger.info(f"Checking cart from user {cart['user_id']}")
                
                app1 = str(cart.get('app', '')).lower().strip()
                app2 = str(current_user.get('app', '')).lower().strip()
                if app1 != app2:
                    logger.debug(f"Skipping - different apps: '{app1}' != '{app2}'")
                    continue
                
                loc1 = cart.get('location')
                loc2 = current_user.get('location')
                logger.info(f"Comparing locations - User {user_id}: {loc2}, Potential match: {loc1}")
                
                # Check if location data is valid
                if not all([loc1, loc2, len(loc1) == 2, len(loc2) == 2]):
                    logger.warning(f"Invalid location data - User {user_id}: {loc2}, Potential match: {loc1}")
                    continue
                    
                # Calculate distance in kilometers using Haversine formula
                lat1, lon1 = loc1
                lat2, lon2 = loc2
                
                # Convert decimal degrees to radians
                lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
                
                # Haversine formula
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                c = 2 * asin(sqrt(a))
                distance_km = 6371 * c  # Radius of Earth in kilometers
                
                # Consider it a match if within 5km
                if distance_km > 5.0:  # 5km radius
                    logger.debug(f"Skipping - too far: {distance_km:.2f}km")
                    continue
                
                cart_total1 = float(cart.get('cart_total', 0))
                cart_total2 = float(current_user.get('cart_total', 0))
                min_req1 = float(cart.get('min_for_free', 0))
                min_req2 = float(current_user.get('min_for_free', 0))
                
                combined_total = cart_total1 + cart_total2
                min_required = max(min_req1, min_req2)
                
                logger.info(f"Checking order values - Combined: {combined_total}, Min required: {min_required}")
                
                if combined_total < min_required:
                    logger.debug(f"Skipping - insufficient combined total: {combined_total} < {min_required}")
                    continue
                
                logger.info(f"MATCH FOUND between {user_id} and {cart['user_id']}")
                
                partner_id = cart['user_id']
                
                users[user_id].update({
                    'matched_with': partner_id,
                    'step': 'matched',
                    'chat_active': False,
                    'partner_data': {
                        'app': cart.get('app'),
                        'cart_total': cart.get('cart_total', 0),
                        'min_for_free': cart.get('min_for_free', 0)
                    }
                })
                
                if partner_id in users:
                    users[partner_id].update({
                        'matched_with': user_id,
                        'step': 'matched',
                        'chat_active': False,
                        'partner_data': {
                            'app': current_user.get('app'),
                            'cart_total': current_user.get('cart_total', 0),
                            'min_for_free': current_user.get('min_for_free', 0)
                        }
                    })
                
                carts[:] = [c for c in carts if c['user_id'] not in [user_id, partner_id]]
                
                keyboard = [
                    [InlineKeyboardButton("💬 Start Anonymous Chat", callback_data="start_chat")],
                    [InlineKeyboardButton("❌ End Match", callback_data="end_match")],
                    [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                user_chat_id = current_user.get('chat_id', user_id)
                partner_chat_id = users.get(partner_id, {}).get('chat_id', partner_id) if partner_id in users else partner_id
                
                match_message = (
                    '🎉 *Match Found!* \n\n'
                    f'📱 *App*: {current_user["app"]}\n'
                    f'💰 *Your amount*: ₹{current_user.get("cart_total", 0):.2f}\n'
                    f'💰 *Their amount*: ₹{cart.get("cart_total", 0):.2f}\n\n'
                    '💬 Start an anonymous chat to coordinate your delivery!'
                )
                
                try:
                    await context.bot.send_message(
                        chat_id=user_chat_id,
                        text=match_message,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                    
                    if partner_id in users:
                        partner_msg = (
                            '🎉 *Match Found!* \n\n'
                            f'📱 *App*: {cart.get("app", "Unknown")}\n'
                            f'💰 *Your amount*: ₹{cart.get("cart_total", 0):.2f}\n'
                            f'💰 *Their amount*: ₹{current_user.get("cart_total", 0):.2f}\n\n'
                            '💬 Start an anonymous chat to coordinate your delivery!'
                        )
                        await context.bot.send_message(
                            chat_id=partner_chat_id,
                            text=partner_msg,
                            reply_markup=reply_markup,
                            parse_mode='Markdown'
                        )
                    
                    if hasattr(context, 'job_queue') and context.job_queue:
                        for uid in [user_id, partner_id]:
                            for job in context.job_queue.get_jobs_by_name(f'search_{uid}'):
                                job.schedule_removal()
                    
                    logger.info(f"Match successful between {user_id} and {partner_id}")
                    return True
                    
                except Exception as send_error:
                    logger.error(f"Error sending match notification: {send_error}")
                    return False
                
            except Exception as cart_error:
                logger.error(f"Error processing cart: {cart_error}", exc_info=True)
                continue
        
        logger.info(f"No matches found for user {user_id} this round")
        return False
        
    except Exception as e:
        logger.error(f"Error in search_for_matches: {e}", exc_info=True)
        return False

async def search_for_matches_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback function for the job queue to search for matches."""
    try:
        if not hasattr(context, 'job') or not context.job:
            logger.error("No job data in context")
            return
            
        job_data = getattr(context.job, 'data', {})
        if not job_data or 'user_id' not in job_data:
            logger.error("Invalid job data format")
            return
            
        user_id = str(job_data['user_id'])
        chat_id = job_data.get('chat_id', user_id)
        
        logger.info(f"=== Search callback triggered for user {user_id} ===")
        
        if user_id not in users:
            logger.warning(f"User {user_id} not found in users dictionary")
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Session expired. Please start again with /start"
            )
            context.job.schedule_removal()
            return
            
        user = users[user_id]
        current_step = user.get('step')
        
        if current_step != 'searching':
            logger.info(f"User {user_id} is in state '{current_step}', not 'searching'. Stopping job.")
            context.job.schedule_removal()
            return
            
        required_fields = ['location', 'app', 'cart_total', 'min_for_free']
        missing_fields = [field for field in required_fields if field not in user or not user[field]]
        
        if missing_fields:
            logger.warning(f"Missing required fields for user {user_id}: {', '.join(missing_fields)}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Missing required information. Please start again with /start"
            )
            context.job.schedule_removal()
            return
            
        search_duration = context.bot_data.get('current_time', 0) - user.get('search_start_time', 0)
        if search_duration > 1800:
            logger.info(f"Search timeout for user {user_id} after {search_duration} seconds")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏱️ Search timed out after 30 minutes. Use /start to try again.",
                reply_markup=ReplyKeyboardRemove()
            )
            context.job.schedule_removal()
            return
            
        logger.info(f"Searching for matches for user {user_id} (searching for {search_duration//60}m {search_duration%60}s)")
        
        found = await search_for_matches(context, user_id)
        
        if found:
            logger.info(f"Match found for user {user_id}, removing job")
            context.job.schedule_removal()
        else:
            logger.info(f"No matches found this round for user {user_id}")
            if search_duration > 0 and search_duration % 120 < 10:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔍 Still searching for matches... ({search_duration//60}m {search_duration%60}s elapsed)",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🛑 Stop Searching", callback_data="stop_search")]
                    ])
                )
                
    except Exception as e:
        logger.error(f"Error in search_for_matches_callback: {e}", exc_info=True)
        if 'chat_id' in locals():
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ An unexpected error occurred. Please try again or use /start to begin a new session.",
                reply_markup=ReplyKeyboardRemove()
            )
        context.job.schedule_removal()

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button presses."""
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    
    if query.data == 'open_app':
        await query.edit_message_text(
            '📱 *App Selection*\n\n'
            'Which delivery app are you using?',
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Zepto", callback_data="app_zepto"),
                    InlineKeyboardButton("Swiggy", callback_data="app_swiggy")
                ],
                [
                    InlineKeyboardButton("Zomato", callback_data="app_zomato"),
                    InlineKeyboardButton("Other", callback_data="app_other")
                ]
            ]),
            parse_mode='Markdown'
        )
        return
    
    if query.data == 'help':
        await help_command(update, context)
        return
    
    if user_id not in users:
        await query.edit_message_text("Please start a new session with /start")
        return
    
    if query.data.startswith('app_'):
        app_name = query.data[4:].capitalize()
        users[user_id]['app'] = app_name
        users[user_id]['step'] = 'cart_amount'
        
        await query.edit_message_text(
            f"Selected {app_name}. Now, please enter the total amount of your order:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="open_app")]
            ])
        )
        logger.info(f"App selected for user {user_id}: {app_name}")
        return
    
    if query.data == 'share_cart':
        await query.edit_message_text(
            "Please enter the total amount of your order or share a Zepto cart URL:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_options")]
            ])
        )
        users[user_id]['step'] = 'cart_amount'
        return
    
    if query.data == 'enter_amount':
        users[user_id]['step'] = 'cart_amount'
        await query.edit_message_text(
            "💵 Please enter the total amount of your order:"
        )
        return
        
    if query.data == 'back_to_options':
        users[user_id]['step'] = 'idle'
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Share Zepto Cart", callback_data="share_cart")],
                [InlineKeyboardButton("✏️ Enter Amount Manually", callback_data="enter_amount")],
                [InlineKeyboardButton("❌ Cancel", callback_data="end_session")]
            ])
        )
        return
        
    if query.data == 'stop_search':
        if hasattr(context, 'job_queue') and context.job_queue:
            for job in context.job_queue.get_jobs_by_name(f'search_{user_id}'):
                job.schedule_removal()
        if user_id in users and users[user_id].get('step') == 'searching':
            users[user_id]['step'] = 'idle'
            carts[:] = [cart for cart in carts if cart['user_id'] != user_id]
            await query.edit_message_text(
                '🛑 Search stopped. You can start a new search anytime!',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Start New Search", callback_data="new_search")]
                ])
            )
        return
    
    if query.data == 'new_search':
        if user_id in users:
            users[user_id]['matched_with'] = None
            users[user_id]['chat_active'] = False
            users[user_id]['chat_requested'] = False
            users[user_id]['step'] = 'started'
            carts[:] = [cart for cart in carts if cart['user_id'] != user_id]
            await query.edit_message_text(
                '🔄 Starting a new search! Please select an app:',
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Zepto", callback_data="app_zepto"),
                        InlineKeyboardButton("Swiggy", callback_data="app_swiggy")
                    ],
                    [
                        InlineKeyboardButton("Zomato", callback_data="app_zomato"),
                        InlineKeyboardButton("Other", callback_data="app_other")
                    ]
                ])
            )
        return
    
    if query.data == 'start_chat':
        if user_id in users and users[user_id].get('matched_with'):
            partner_id = users[user_id]['matched_with']
            users[user_id]['chat_requested'] = True
            
            await query.edit_message_text(
                "💬 Chat request sent!\n\n"
                "⏳ Waiting for your partner to accept...",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel Request", callback_data="cancel_chat_request")],
                    [InlineKeyboardButton("🛑 End Match", callback_data="end_match")]
                ])
            )
            
            if partner_id in users:
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"💬 {users[user_id]['pseudonym']} wants to start an anonymous chat!\n\n"
                         "Do you want to accept?",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Accept Chat", callback_data="accept_chat")],
                        [InlineKeyboardButton("❌ Decline", callback_data="decline_chat")],
                        [InlineKeyboardButton("🛑 End Match", callback_data="end_match")]
                    ])
                )
            
            logger.info(f"Chat request sent from {user_id} to {partner_id}")
        return
    
    if query.data == 'accept_chat':
        if user_id in users and users[user_id].get('matched_with'):
            partner_id = users[user_id]['matched_with']
            if partner_id in users and users[partner_id].get('chat_requested'):
                users[user_id]['chat_active'] = True
                users[partner_id]['chat_active'] = True
                users[partner_id]['chat_requested'] = False
                active_chats[user_id] = partner_id
                active_chats[partner_id] = user_id
                
                keyboard = [
                    [InlineKeyboardButton("🛑 End Chat", callback_data="end_chat")],
                    [InlineKeyboardButton("❌ End Match", callback_data="end_match")],
                    [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=user_id,
                    text='✨',
                    reply_markup=ReplyKeyboardRemove()
                )
                await context.bot.send_message(
                    chat_id=partner_id,
                    text='✨',
                    reply_markup=ReplyKeyboardRemove()
                )
                
                await query.edit_message_text(
                    "💬 Anonymous chat started!\n\n"
                    "📝 Send any message and it will be forwarded to your partner anonymously.\n"
                    "🔒 Your identity is protected.\n\n"
                    "Type your message below to start chatting!",
                    reply_markup=reply_markup
                )
                
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"✅ {users[user_id]['pseudonym']} accepted the chat!\n\n"
                         "💬 Start messaging to coordinate your delivery.\n"
                         "🔒 All messages are anonymous.",
                    reply_markup=reply_markup
                )
                
                logger.info(f"Chat accepted between {user_id} and {partner_id}")
        return
    
    if query.data == 'decline_chat':
        if user_id in users and users[user_id].get('matched_with'):
            partner_id = users[user_id]['matched_with']
            await query.edit_message_text(
                "❌ Chat request declined.\n\n"
                "You can still coordinate using other means.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Start Chat", callback_data="start_chat")],
                    [InlineKeyboardButton("🛑 End Match", callback_data="end_match")],
                    [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                ])
            )
            
            if partner_id in users:
                users[partner_id]['chat_requested'] = False
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"❌ {users[user_id]['pseudonym']} declined the chat request.\n\n"
                         "You can try again or end the match.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Request Chat Again", callback_data="start_chat")],
                        [InlineKeyboardButton("🛑 End Match", callback_data="end_match")],
                        [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                    ])
                )
            
            logger.info(f"Chat declined by {user_id}")
        return
    
    if query.data == 'cancel_chat_request':
        if user_id in users:
            users[user_id]['chat_requested'] = False
            await query.edit_message_text(
                "❌ Chat request cancelled.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Start Chat", callback_data="start_chat")],
                    [InlineKeyboardButton("🛑 End Match", callback_data="end_match")],
                    [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                ])
            )
            logger.info(f"Chat request cancelled by {user_id}")
        return
    
    if query.data == 'end_chat':
        if user_id in users and user_id in active_chats:
            partner_id = users[user_id].get('matched_with')
            if partner_id and partner_id in users:
                users[user_id]['chat_active'] = False
                users[partner_id]['chat_active'] = False
                active_chats.pop(user_id, None)
                active_chats.pop(partner_id, None)
                
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"🔌 Chat Disconnected!\n\n"
                         f"💬 {users[user_id]['pseudonym']} has ended the chat.\n"
                         "The anonymous chat session has been closed.\n\n"
                         "You can request to restart the chat or end the match.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Restart Chat", callback_data="start_chat")],
                        [InlineKeyboardButton("❌ End Match", callback_data="end_match")],
                        [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                    ])
                )
            
            await query.edit_message_text(
                "💬 Chat ended.\n\n"
                "You can restart the chat or end the match.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Restart Chat", callback_data="start_chat")],
                    [InlineKeyboardButton("❌ End Match", callback_data="end_match")],
                    [InlineKeyboardButton("🔄 Restart Search", callback_data="new_search")]
                ])
            )
            logger.info(f"Chat ended for user {user_id}")
        return
    
    if query.data == 'end_match':
        if user_id in users:
            partner_id = users[user_id].get('matched_with')
            if partner_id and partner_id in users:
                users[user_id]['matched_with'] = None
                users[user_id]['chat_active'] = False
                users[user_id]['chat_requested'] = False
                users[partner_id]['matched_with'] = None
                users[partner_id]['chat_active'] = False
                users[partner_id]['chat_requested'] = False
                users[partner_id]['step'] = 'idle'
                active_chats.pop(user_id, None)
                active_chats.pop(partner_id, None)
                
                await context.bot.send_message(
                    chat_id=partner_id,
                    text=f"🔌 Match Disconnected!\n\n"
                         f"❌ {users[user_id]['pseudonym']} has ended the match.\n"
                         "💔 The connection has been terminated.\n\n"
                         "You can start a new search to find another partner.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Find New Match", callback_data="new_search")]
                    ])
                )
            
            users[user_id]['step'] = 'idle'
            await query.edit_message_text(
                "❌ Match ended.\n\n"
                "Thank you for using DeliveryShare! Use /start to find a new match.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Find New Search", callback_data="new_search")]
                ])
            )
            logger.info(f"Match ended between {user_id} and {partner_id}")
        return
    
    if query.data == 'end_session':
        await end_session(update, context)
        return
    
    if query.data == 'confirm_cart':
        if user_id in users:
            users[user_id]['step'] = 'min_for_free'
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Cart confirmed! Total: ₹{users[user_id].get('cart_total', 0):.2f}\n\n"
                     "What's the minimum amount for free delivery? (e.g., 100 for Zepto)",
                reply_markup=ReplyKeyboardRemove()
            )
        return

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    
    if update and hasattr(update, 'effective_chat') and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An error occurred. Please try again or use /start to begin a new session."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

async def main_async() -> None:
    """Async entry point for the bot."""
    try:
        if not TOKEN:
            error_msg = "❌ Error: No TOKEN provided. Set the TELEGRAM_BOT_TOKEN environment variable."
            print(error_msg)
            logger.error(error_msg)
            return

        print("=== Starting DeliveryShare Bot ===")
        print(f"Python version: {sys.version}")
        print(f"python-telegram-bot version: {__import__('telegram').__version__}")
        print(f"APScheduler available: {APSCHEDULER_AVAILABLE}")
        try:
            pip_version = subprocess.check_output(['pip', '--version']).decode('utf-8').strip()
            logger.info(f"pip version: {pip_version}")
            pip_list = subprocess.check_output(['pip', 'list']).decode('utf-8')
            logger.info(f"Environment packages: {pip_list}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to retrieve pip information: {e}")

        try:
            from telegram.ext._jobqueue import JobQueue
        except ImportError:
            error_msg = (
                "❌ Job queue extension not found. Please install with:\n"
                "pip install \"python-telegram-bot[job-queue]==22.5\""
            )
            print(error_msg)
            logger.error(error_msg)
            return

        application = (
            Application.builder()
            .token(TOKEN)
            .concurrent_updates(True)
            .build()
        )
        print("✅ Application created successfully")
        print(f"Job queue enabled: {application.job_queue is not None}")
        logger.info(f"Job queue enabled: {application.job_queue is not None}")

        if application.job_queue is None:
            error_msg = (
                "❌ Job queue is not available.\n"
                "This is required for the bot to function.\n"
                "Please ensure you have installed the job queue extension with:\n"
                "pip install \"python-telegram-bot[job-queue]==22.5\" and apscheduler==3.10.4"
            )
            print(error_msg)
            logger.error(error_msg)
            return

        application.bot_data['current_time'] = 0
        
        async def update_time(context: ContextTypes.DEFAULT_TYPE):
            context.bot_data['current_time'] = context.bot_data.get('current_time', 0) + 1
            
        application.job_queue.run_repeating(
            update_time,
            interval=1.0,
            first=1,
            name='update_time'
        )

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("end", end_session))
        application.add_handler(CommandHandler("stop", stop))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(MessageHandler(filters.LOCATION, handle_location))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_error_handler(error_handler)

        print("✅ All handlers registered successfully")

        try:
            bot_info = await application.bot.get_me()
            print(f"🤖 Bot Info:")
            print(f"   Name: {bot_info.first_name}")
            print(f"   Username: @{bot_info.username}")
            print(f"   ID: {bot_info.id}")
            print(f"   Job queue: {application.job_queue is not None}")
        except Exception as e:
            logger.error(f"Failed to get bot info: {e}", exc_info=True)

        print("🚀 Starting bot...")
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
        print("✅ Bot is now running. Press Ctrl+C to stop.")
        
        while True:
            await asyncio.sleep(3600)
            
    except asyncio.CancelledError:
        print("\n🛑 Shutdown signal received...")
        
    except Exception as e:
        logger.error(f"Error running bot: {e}", exc_info=True)
        print(f"❌ Error running bot: {e}")
        raise
        
    finally:
        print("\n🛑 Stopping bot...")
        if application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        if application.post_stop is not None:
            await application.post_stop(application)
        print("✅ Bot has been stopped.")


if __name__ == '__main__':
    print("=== Starting DeliveryShare Bot ===")
    print(f"Python version: {sys.version}")
    print(f"python-telegram-bot version: {__import__('telegram').__version__}")
    print(f"APScheduler available: {APSCHEDULER_AVAILABLE}")
    print(f"Using token: {TOKEN[:5] if TOKEN else 'None'}...{TOKEN[-5:] if TOKEN else 'None'}")
    
    # Configure logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    # Run the main function
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n❌ Fatal error: {e}")
        print("Check bot.log for more details")
        sys.exit(1)