import asyncio
import aiohttp
import logging
from datetime import datetime, timezone, timedelta
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters
import json
import os
from logging.handlers import RotatingFileHandler
from models import Database
import humanize

# Configure logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        RotatingFileHandler(
            'logs/bot.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3
        ),
        logging.StreamHandler()  # Also log to console
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
API_URL = os.getenv('API_URL', 'https://api.srcful.dev/')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))  # seconds
DB_PATH = os.getenv('DB_PATH', 'bot_data.db')
BROADCAST_PASSWORD = os.getenv('BROADCAST_PASSWORD')

# Bot version
VERSION = "0.1.8"

if not TELEGRAM_TOKEN:
    raise ValueError("Missing required environment variable: TELEGRAM_TOKEN must be set")

if not BROADCAST_PASSWORD:
    logger.warning("BROADCAST_PASSWORD not set - broadcast functionality will be disabled")

# Enhanced GraphQL queries
GATEWAY_QUERY = """
query {
  gateway {
    gateway(id: "%s") {
      name
      id
      typeOf
      ders {
        type
        name
        lastSeen
        sn
        meta {
          make
          nominalPower
          dataPoints
        }
      }
    }
  }
}"""

DER_DATA_QUERY = """
query {
  derData {
    solar(sn: "%s") {
      latest {
        ts
        power
      }
    }
  }
}"""

# Conversation states for broadcast feature
ENTER_PASSWORD, ENTER_MESSAGE = range(2)

# Conversation states for subscription feature
ENTER_GATEWAY_ID = 0

class GatewayMonitor:
    def __init__(self):
        self.db = Database(DB_PATH)
        self.bot = None
        self._should_stop = False
        self.application = None
        self.broadcast_data = {}  # Store temporary data for broadcast conversations

    async def start_polling(self):
        """Start background polling of gateway status"""
        logger.info("Starting gateway polling...")
        last_status = {}  # Track last known status of each gateway
        initial_poll = True  # Flag to indicate first polling cycle
        
        while not self._should_stop:
            try:
                # Get all subscribed gateways
                gateway_ids = self.db.get_all_gateway_ids()
                
                for gateway_id in gateway_ids:
                    try:
                        # Fetch current status
                        result = await self.fetch_gateway_status(gateway_id)
                        if not result:
                            continue

                        gateway_data, der_latest_data = result
                        is_online = self.check_gateway_status(gateway_data, der_latest_data, chat_id=None)
                        
                        # Get latest timestamp from DER data
                        latest_ts = None
                        status_factors = {}
                        
                        for der in gateway_data.get('ders', []):
                            sn = der.get('sn')
                            if sn and sn in der_latest_data:
                                ts = der_latest_data[sn].get('ts')
                                if ts:
                                    ts_dt = self.parse_timestamp(ts)
                                    if ts_dt and (not latest_ts or ts_dt > latest_ts):
                                        latest_ts = ts_dt
                                    
                                    # Add DER status to factors
                                    status_factors[sn] = {
                                        'power': der_latest_data[sn].get('power'),
                                        'timestamp': ts_dt.isoformat() if ts_dt else None,
                                        'name': der.get('name'),
                                        'type': der.get('type')
                                    }
                        
                        # If no timestamp found, use current time
                        if not latest_ts:
                            latest_ts = datetime.now(timezone.utc)
                        
                        # Check if status has changed
                        current_status = (is_online, latest_ts)
                        last_known = last_status.get(gateway_id)
                        status_changed = (
                            last_known is None or 
                            last_known[0] != is_online
                        )
                        
                        if status_changed:
                            # Update last known status
                            last_status[gateway_id] = current_status
                            
                            # Only send notifications if:
                            # 1. This is not the initial polling cycle (bot just started)
                            # 2. We're notifying about a gateway going OFFLINE (not just coming online)
                            # 3. We actually have a last_known status (not first time seeing this gateway)
                            should_notify = (
                                not initial_poll and 
                                (not is_online or last_known is not None)
                            )
                            
                            # Update database and notify subscribers if needed
                            subscribers = self.db.get_gateway_subscribers(gateway_id)
                            if should_notify and subscribers and self.bot:
                                message = self.format_status_message(gateway_data, is_online, der_latest_data)
                                logger.info(f"Gateway {gateway_id} status changed to {'ONLINE' if is_online else 'OFFLINE'}")
                                
                                for chat_id in subscribers:
                                    try:
                                        await self.bot.send_message(
                                            chat_id=chat_id,
                                            text=message,
                                            parse_mode='MarkdownV2'
                                        )
                                    except Exception as e:
                                        logger.error(f"Failed to send notification to {chat_id}: {e}")
                        
                        # Always update the database
                        self.db.update_gateway_status(
                            gateway_id=gateway_id,
                            name=gateway_data['name'],
                            is_online=is_online,
                            timestamp=latest_ts.isoformat(),
                            status_factors=json.dumps(status_factors)
                        )
                    
                    except Exception as e:
                        logger.error(f"Error processing gateway {gateway_id}: {e}")
                
                # After completing the first poll cycle, set initial_poll to False
                if initial_poll:
                    initial_poll = False
                    logger.info("Initial polling cycle completed. Now monitoring for real status changes.")
                    
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
            
            # Wait for next poll
            await asyncio.sleep(CHECK_INTERVAL)

    async def setup(self):
        """Initialize the application"""
        logger.info(f"Starting Sourceful Bot v{VERSION}")
        
        # Initialize application
        self.application = Application.builder().token(TELEGRAM_TOKEN).build()
        self.bot = self.application.bot

        # Create conversation handler for subscription
        subscribe_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("subscribe", self.subscribe_command_start)],
            states={
                ENTER_GATEWAY_ID: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.subscribe_process_gateway_id)
                ],
            },
            fallbacks=[CommandHandler("cancel", self.subscribe_cancel)],
        )

        # Add command handlers - keep traditional slash commands as alternatives
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(subscribe_conv_handler)  # Use the conversation handler for subscribe
        self.application.add_handler(CommandHandler("unsubscribe", self.unsubscribe_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("threshold", self.threshold_command))
        
        # Add broadcast functionality if password is set, but rename it to "beacon" (hidden feature)
        if BROADCAST_PASSWORD:
            # Create a conversation handler for the hidden broadcast command
            broadcast_conv_handler = ConversationHandler(
                entry_points=[CommandHandler("beacon", self.broadcast_command_start)],
                states={
                    ENTER_PASSWORD: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.broadcast_check_password)
                    ],
                    ENTER_MESSAGE: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.broadcast_send_message)
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.broadcast_cancel)],
            )
            
            self.application.add_handler(broadcast_conv_handler)
            
        # Add a general message handler for natural language commands
        # This should be added LAST to avoid interfering with other handlers
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_natural_language
        ))

        # Initialize the application
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

    async def shutdown(self):
        """Cleanup and shutdown"""
        self._should_stop = True
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def run(self):
        """Start the bot and monitoring"""
        try:
            # Setup the application
            await self.setup()
            logger.info(f"Bot v{VERSION} starting up...")
            
            # Start polling task
            polling_task = asyncio.create_task(self.start_polling())
            
            # Send startup message to console
            startup_message = (
                "\n"
                "====================================\n"
                f"🤖 Sourceful Bot v{VERSION} is running\n"
                "====================================\n"
            )
            print(startup_message)
            logger.info(startup_message)

            # No longer sending startup notifications to users
            # This prevents unnecessary notifications on bot restart
            
            # Keep the application running
            while not self._should_stop:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in run: {str(e)}")
            logger.exception(e)
            raise
        finally:
            # Cleanup
            await self.shutdown()

    async def announce_version(self):
        """Announce bot version to users"""
        try:
            logger.info("Getting users for version announcement...")
            users = self.db.get_all_users()
            logger.info(f"Found {len(users)} users to notify")
            
            if not users:
                logger.info("No users found in database")
                return

            message = (
                f"🤖 *Sourceful Bot v{VERSION}*\n"
                f"Bot is running and ready\\!"
            )
            
            logger.info(f"Starting to send version {VERSION} announcements...")
            sent_count = 0
            for chat_id in users:
                try:
                    logger.info(f"Sending to chat_id: {chat_id}")
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode='MarkdownV2'
                    )
                    sent_count += 1
                    logger.info(f"Successfully sent to {chat_id}")
                except Exception as e:
                    logger.error(f"Failed to send to {chat_id}: {str(e)}")
            
            logger.info(f"Version announcement completed. Sent to {sent_count}/{len(users)} users")
        except Exception as e:
            logger.error(f"Error in announce_version: {str(e)}")
            logger.exception(e)

    def parse_timestamp(self, ts):
        """Parse timestamp from either milliseconds or ISO format"""
        try:
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            else:
                # Remove timezone indicator and handle microseconds
                ts = ts.replace('Z', '')
                
                # Split into parts
                if '.' in ts:
                    dt_part, ms_part = ts.split('.')
                    # Ensure exactly 6 digits for microseconds
                    ms_part = ms_part[:6].ljust(6, '0')
                    # Create a valid ISO format string
                    ts = f"{dt_part}.{ms_part}"
                
                # Parse the datetime
                try:
                    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                except ValueError:
                    # Try without microseconds
                    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
                
                # Set UTC timezone
                return dt.replace(tzinfo=timezone.utc)
                
        except Exception as e:
            logger.error(f"Error parsing timestamp {ts}: {e}")
            return None

    def check_gateway_status(self, gateway_data, der_latest_data, chat_id=None):
        """
        Check if gateway is online based on latest datapoint and user threshold
        Returns: bool indicating if gateway is online
        """
        if not gateway_data or 'ders' not in gateway_data:
            return False

        # Get user's threshold (default 5 minutes if not set)
        threshold_minutes = self.db.get_user_threshold(chat_id) if chat_id else 5

        # Get the most recent timestamp from any DER
        latest_ts = None
        for der in gateway_data['ders']:
            sn = der.get('sn')
            if sn and sn in der_latest_data:
                ts = der_latest_data[sn].get('ts')
                if ts:
                    ts_dt = self.parse_timestamp(ts)
                    if ts_dt and (not latest_ts or ts_dt > latest_ts):
                        latest_ts = ts_dt

        if not latest_ts:
            return False

        # Check if the latest timestamp is within the threshold
        now = datetime.now(timezone.utc)
        return (now - latest_ts).total_seconds() <= (threshold_minutes * 60)

    def escape_markdown(self, text: str) -> str:
        """Escape special characters for MarkdownV2 format"""
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        escaped_text = str(text)  # Convert to string in case we get a number
        for char in special_chars:
            escaped_text = escaped_text.replace(char, f'\\{char}')
        return escaped_text

    def format_power(self, power: int) -> str:
        """Format power value with proper escaping"""
        return self.escape_markdown(f"{power}W")

    def format_status_message(self, gateway_data, is_online, der_latest_data):
        """Format status message with current data"""
        status_emoji = "🟢" if is_online else "🔴"
        status_text = "ONLINE" if is_online else "OFFLINE"
        
        # Escape special characters for MarkdownV2
        gateway_name = self.escape_markdown(gateway_data['name'])
        gateway_id = gateway_data['id']
        
        message_parts = [
            f"{status_emoji} *Gateway: {gateway_name}*",
            f"ID: `{gateway_id}`",
            f"Status: {status_text}"
        ]

        # Add last seen timestamp if available
        if gateway_data.get('ders'):
            latest_ts = None
            for der in gateway_data['ders']:
                sn = der.get('sn')
                if sn and sn in der_latest_data:
                    ts = der_latest_data[sn].get('ts')
                    if ts:
                        ts_dt = self.parse_timestamp(ts)
                        if ts_dt and (not latest_ts or ts_dt > latest_ts):
                            latest_ts = ts_dt
            
            if latest_ts:
                time_ago = humanize.naturaltime(datetime.now(timezone.utc) - latest_ts)
                message_parts.append(f"Last data point: {self.escape_markdown(time_ago)}")
                
                # Add offline warning if last seen is more than a minute ago
                if not is_online:
                    message_parts.append("⚠️ *Gateway has not reported data in over a minute\\!*")
        
        message_parts.append("")  # Empty line

        # Add DER information
        if gateway_data.get('ders'):
            message_parts.append("*DER Information:*")
            for der in gateway_data['ders']:
                der_info = []
                # Only show the type of DER, not the name
                der_type = self.escape_markdown(der['type'])
                der_info.append(f"• Type: {der_type}")
                
                if der.get('meta'):
                    meta = der['meta']
                    if meta.get('make'):
                        make = self.escape_markdown(meta['make'])
                        der_info.append(f"• Make: {make}")
                    if meta.get('nominalPower'):
                        power = self.format_power(meta['nominalPower'])
                        der_info.append(f"• Nominal Power: {power}")
                
                # Add latest data if available
                sn = der.get('sn')
                if sn and sn in der_latest_data:
                    latest = der_latest_data[sn]
                    if latest.get('power') is not None:
                        power = self.format_power(latest['power'])
                        der_info.append(f"• Current Power: {power}")
                
                message_parts.extend(der_info)
                message_parts.append("")  # Empty line between DERs

        # Join all parts with newlines
        return '\n'.join(message_parts)

    async def status_command(self, update, context):
        """Handler for /status command"""
        chat_id = update.effective_chat.id
        subscribed_gateways = self.db.get_user_subscriptions(chat_id)
        
        if not subscribed_gateways:
            await update.message.reply_text(
                "❗️ You haven't subscribed to any gateways yet\\.\n"
                "Use /subscribe with a gateway ID to start monitoring\\.",
                parse_mode='MarkdownV2'
            )
            return

        await update.message.reply_text("🔍 Fetching gateway status\\.\\.\\.", parse_mode='MarkdownV2')
        
        status_messages = []
        for gateway_id in subscribed_gateways:
            try:
                result = await self.fetch_gateway_status(gateway_id)
                if not result:
                    status_messages.append(f"❌ Failed to fetch status for gateway `{gateway_id}`")
                    continue

                gateway_data, der_latest_data = result
                is_online = self.check_gateway_status(gateway_data, der_latest_data, chat_id)
                message = self.format_status_message(gateway_data, is_online, der_latest_data)
                status_messages.append(message)
            
            except Exception as e:
                logger.error(f"Error processing gateway {gateway_id}: {e}")
                status_messages.append(f"❌ Error processing gateway `{gateway_id}`")

        # Send all status messages
        for message in status_messages:
            await update.message.reply_text(message, parse_mode='MarkdownV2')

    async def start_command(self, update, context):
        """Handler for /start command"""
        chat_id = update.effective_chat.id
        
        # Ensure user exists in settings
        self.db.ensure_user_exists(chat_id)
        
        # Check if user has any subscriptions
        subscribed_gateways = self.db.get_user_subscriptions(chat_id)
        
        if not subscribed_gateways:
            # First-time user with no subscriptions
            welcome_message = (
                f"👋 *{self.escape_markdown(f'Welcome to Sourceful Energy Monitor v{VERSION}')}\\!*\n\n"
                "I'm your personal assistant for monitoring energy gateways\\.\n\n"
                "It looks like you haven't subscribed to any gateways yet\\. "
                "Let's get you started\\!\n\n"
                "*Here's how to use me:*\n"
                "1\\. Use `/subscribe <gateway_id>` to start monitoring a gateway\n"
                "2\\. Check gateway status anytime with `/status`\n"
                "3\\. Adjust how often I check with `/threshold <minutes>`\n\n"
                "💡 *Something cool:* You can also talk to me naturally\\!\n"
                "Instead of commands, try saying things like:\n"
                "• \"Subscribe to my gateway\"\n"
                "• \"Check status\"\n"
                "• \"Show me my power stats\""
            )
        else:
            # Returning user with existing subscriptions
            welcome_message = (
                f"👋 *{self.escape_markdown(f'Welcome back to Sourceful Monitor v{VERSION}')}\\!*\n\n"
                "I help you track gateway status and send notifications\\!\n\n"
                "*Quick Commands:*\n"
                "• /status \\- Check gateways\n"
                "• /subscribe \\- Add gateway\n"
                "• /threshold \\- Set check interval\n"
                "• /stats \\- Show power stats\n"
                "• /help \\- More info\n\n"
                f"You are currently monitoring {len(subscribed_gateways)} gateway\\(s\\)\\.\n\n"
                "💬 *Remember:* You can talk to me naturally without using commands\\!"
            )
        
        await update.message.reply_text(welcome_message, parse_mode='MarkdownV2')

    async def help_command(self, update, context):
        """Handler for /help command"""
        if not update.message:
            logger.error("Help command received but message is None")
            return

        threshold = self.db.get_user_threshold(update.effective_chat.id)
        help_message = (
            f"📚 *{self.escape_markdown(f'Sourceful Monitor v{VERSION}')}*\n\n"
            "*Commands:*\n"
            "• /start \\- Initialize bot\n"
            "• /status \\- Shows gateway status\n"
            "• /subscribe \\- Monitor a gateway\n"
            "• /unsubscribe \\- Stop monitoring\n"
            "• /threshold \\- Set status check interval\n"
            "• /stats \\- Show bot statistics\n"
            "• /help \\- Show this help\n"
        )
        
        # Hidden feature - don't show in help
        # if BROADCAST_PASSWORD:
        #     help_message += "• /beacon \\- Send message to all users \\(admin only\\)\n"
            
        help_message += (
            "\n"
            "*Status Information:*\n"
            f"• 🟢 Online \\- Data within {threshold} minutes\n"
            f"• 🔴 Offline \\- No data for {threshold}\\+ minutes\n"
            "• Power production in watts\n"
            "• DER information \\(type, make, power\\)\n\n"
            "*Natural Language:*\n"
            "You can also talk to me naturally\\! Try saying:\n"
            "• \"Check my gateway status\"\n"
            "• \"Show me power statistics\"\n"
            "• \"Set threshold to 15 minutes\"\n"
            "• \"Subscribe to \\<gateway ID\\>\"\n\n"
            "*Examples:*\n"
            "Monitor a gateway:\n"
            "`/subscribe 01233d032a7c838bee`\n"
            "or simply type:\n"
            "`subscribe 01233d032a7c838bee`\n\n"
            "Change status threshold:\n"
            "`/threshold 10` \\(10 minutes\\)"
        )
        await update.message.reply_text(help_message, parse_mode='MarkdownV2')

    async def subscribe_command(self, update, context):
        """Handler for /subscribe command"""
        chat_id = update.effective_chat.id
        logger.info(f"Subscribe command received from chat_id: {chat_id}")
        
        # Check if a gateway ID was provided
        if not context.args:
            logger.info(f"No gateway ID provided by chat_id: {chat_id}")
            await update.message.reply_text(
                "❗️ Please provide a gateway ID\\.\n"
                "Example: `/subscribe 01233d032a7c838bee`",
                parse_mode='MarkdownV2'
            )
            return

        gateway_id = context.args[0]
        logger.info(f"Subscribe request from {chat_id} for gateway {gateway_id}")

        # Verify gateway exists
        result = await self.fetch_gateway_status(gateway_id)
        if not result:
            logger.warning(f"Invalid gateway ID attempted: {gateway_id} by chat_id: {chat_id}")
            await update.message.reply_text(
                "❌ Invalid gateway ID or gateway not found\\.\n"
                "Please check the ID and try again\\.",
                parse_mode='MarkdownV2'
            )
            return

        gateway_data, _ = result
        if self.db.add_subscription(chat_id, gateway_id):
            # Also ensure user exists in settings
            self.db.ensure_user_exists(chat_id)
            
            await update.message.reply_text(
                f"✅ Successfully subscribed to gateway:\n"
                f"Name: {self.escape_markdown(gateway_data['name'])}\n"
                f"ID: `{gateway_id}`\n\n"
                f"You'll receive notifications when status changes\\.\n"
                f"Use /status to check current status\\.",
                parse_mode='MarkdownV2'
            )
            logger.info(f"Successfully subscribed chat_id: {chat_id} to gateway: {gateway_id}")
        else:
            logger.info(f"chat_id: {chat_id} already subscribed to gateway: {gateway_id}")
            await update.message.reply_text(
                "You're already subscribed to this gateway\\.\n"
                "Use /status to check current status\\.",
                parse_mode='MarkdownV2'
            )

    async def unsubscribe_command(self, update, context):
        """Handler for /unsubscribe command"""
        if not context.args:
            # Show list of subscribed gateways
            subscribed_gateways = self.db.get_user_subscriptions(update.effective_chat.id)
            if not subscribed_gateways:
                await update.message.reply_text(
                    "❗️ You're not subscribed to any gateways.\n"
                    "Use /subscribe <gateway_id> to start monitoring a gateway."
                )
                return

            message = "Your subscribed gateways:\n\n"
            for gateway_id in subscribed_gateways:
                info = self.db.get_gateway_info(gateway_id)
                if info:
                    message += f"• {info['name']}\n  ID: `{gateway_id}`\n\n"
            
            message += "To unsubscribe, use:\n`/unsubscribe <gateway_id>`"
            await update.message.reply_text(message, parse_mode='Markdown')
            return

        gateway_id = context.args[0]
        chat_id = update.effective_chat.id

        if self.db.remove_subscription(chat_id, gateway_id):
            await update.message.reply_text(
                "✅ Successfully unsubscribed from the gateway.\n"
                "You'll no longer receive notifications about its status."
            )
        else:
            await update.message.reply_text(
                "❌ You were not subscribed to this gateway."
            )

    async def fetch_gateway_status(self, gateway_id):
        """Fetch status for a specific gateway"""
        headers = {
            "Content-Type": "application/json",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                # Fetch gateway data
                async with session.post(
                    API_URL,
                    json={"query": GATEWAY_QUERY % gateway_id},
                    headers=headers,
                    timeout=10
                ) as response:
                    result = await response.json()
                    if 'errors' in result:
                        logger.error(f"GraphQL errors: {result['errors']}")
                        return None
                    
                    gateway_data = result.get('data', {}).get('gateway', {}).get('gateway')
                    if not gateway_data:
                        return None

                    # Fetch latest data for each DER
                    der_latest_data = {}
                    for der in gateway_data.get('ders', []):
                        if der.get('sn'):
                            latest = await self.fetch_der_data(der['sn'])
                            if latest:
                                der_latest_data[der['sn']] = latest

                    return gateway_data, der_latest_data

        except Exception as e:
            logger.error(f"Error fetching gateway status: {e}")
            return None

    async def fetch_der_data(self, sn):
        """Fetch latest data for a specific DER"""
        headers = {
            "Content-Type": "application/json",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    API_URL,
                    json={"query": DER_DATA_QUERY % sn},
                    headers=headers,
                    timeout=10
                ) as response:
                    result = await response.json()
                    if result.get('data', {}).get('derData', {}).get('solar', {}).get('latest'):
                        return result['data']['derData']['solar']['latest']
        except Exception as e:
            logger.error(f"Error fetching DER data for {sn}: {e}")
        return None

    async def stats_command(self, update, context):
        """Handler for /stats command - shows enhanced bot and power statistics"""
        try:
            chat_id = update.effective_chat.id
            
            # Get basic stats from database
            total_users = len(self.db.get_all_users())
            subscriptions = self.db.get_subscription_stats()
            monitored_gateways = len(set(sub['gateway_id'] for sub in subscriptions))
            
            # Initialize power statistics
            total_current_power = 0
            total_nominal_power = 0
            online_gateways = 0
            offline_gateways = 0
            der_types_count = {}
            der_count = 0
            
            # Get user's subscribed gateways for personalized stats
            user_gateways = self.db.get_user_subscriptions(chat_id)
            
            # Collect stats from all user's gateways
            user_stats = {
                'total_power': 0,
                'online_gateways': 0,
                'offline_gateways': 0,
                'total_capacity': 0,
                'der_count': 0
            }
            
            # Process all gateway data to gather statistics
            for gateway_id in user_gateways:
                # Fetch current gateway status and data
                result = await self.fetch_gateway_status(gateway_id)
                if not result:
                    continue
                    
                gateway_data, der_latest_data = result
                is_online = self.check_gateway_status(gateway_data, der_latest_data, chat_id)
                
                # Count online/offline gateways
                if is_online:
                    user_stats['online_gateways'] += 1
                else:
                    user_stats['offline_gateways'] += 1
                
                # Process each DER for power statistics
                if gateway_data.get('ders'):
                    for der in gateway_data.get('ders', []):
                        user_stats['der_count'] += 1
                        
                        # Count DER types
                        der_type = der.get('type', 'Unknown')
                        der_types_count[der_type] = der_types_count.get(der_type, 0) + 1
                        
                        # Add nominal power to total capacity
                        if der.get('meta') and der['meta'].get('nominalPower'):
                            nominal_power = der['meta']['nominalPower']
                            user_stats['total_capacity'] += nominal_power
                        
                        # Add current power if available
                        sn = der.get('sn')
                        if sn and sn in der_latest_data:
                            current_power = der_latest_data[sn].get('power', 0) or 0
                            user_stats['total_power'] += current_power
            
            # Calculate efficiency if we have both values
            efficiency = 0
            if user_stats['total_capacity'] > 0 and user_stats['total_power'] > 0:
                efficiency = (user_stats['total_power'] / user_stats['total_capacity']) * 100
                
            # Format the power values with appropriate units
            current_power_formatted = self.format_power_dynamic(user_stats['total_power'])
            capacity_formatted = self.format_power_dynamic(user_stats['total_capacity'])
            
            # Format message
            message_parts = [
                "📊 *Energy Production Dashboard*",
                "",
                "⚡ *Your Energy Overview:*"
            ]
            
            # Add power statistics if the user has subscriptions
            if user_gateways:
                message_parts.extend([
                    f"• Current Power: `{current_power_formatted}`",
                    f"• Total Capacity: `{capacity_formatted}`",
                    f"• Efficiency: `{efficiency:.1f}%`" if efficiency > 0 else "• Efficiency: `Not available`",
                    f"• Online Gateways: `{user_stats['online_gateways']}/{len(user_gateways)}`",
                    f"• Energy Resources: `{user_stats['der_count']}`"
                ])
                
                # Add DER type breakdown if available
                if der_types_count:
                    message_parts.append("")
                    message_parts.append("🔋 *Energy Sources:*")
                    for der_type, count in sorted(der_types_count.items(), key=lambda x: x[1], reverse=True):
                        message_parts.append(f"• {self.escape_markdown(der_type)}: `{count}`")
            else:
                message_parts.append("You're not monitoring any gateways yet\\. Use `/subscribe` to add gateways\\.")
            
            # Add network statistics
            message_parts.extend([
                "",
                "🌐 *Network Statistics:*",
                f"• Total Users: `{total_users}`",
                f"• Monitored Gateways: `{monitored_gateways}`",
                f"• Active Connections: `{len(subscriptions)}`"
            ])
            
            # Add timestamp
            current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            message_parts.extend([
                "",
                f"_Stats generated at: {self.escape_markdown(current_time)}_"
            ])

            await update.message.reply_text(
                '\n'.join(message_parts),
                parse_mode='MarkdownV2'
            )

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            logger.exception(e)
            await update.message.reply_text(
                "Error getting statistics\\. Please try again later\\.",
                parse_mode='MarkdownV2'
            )
            
    def format_power_dynamic(self, power: int) -> str:
        """Format power value with appropriate units (W, kW, MW)"""
        if power is None:
            return "0W"
            
        if power < 1000:
            return f"{power}W"
        elif power < 1000000:
            return f"{power/1000:.1f}kW"
        else:
            return f"{power/1000000:.2f}MW"

    async def threshold_command(self, update, context):
        """Handler for /threshold command"""
        chat_id = update.effective_chat.id
        
        # Check if a threshold value was provided
        if not context.args:
            current_threshold = self.db.get_user_threshold(chat_id)
            await update.message.reply_text(
                f"Current threshold is `{current_threshold}` minutes\\.\n"
                f"To change it, use: `/threshold <minutes>`\n"
                f"Example: `/threshold 10` for 10 minutes",
                parse_mode='MarkdownV2'
            )
            return

        try:
            minutes = int(context.args[0])
            if minutes < 1:
                await update.message.reply_text(
                    "❌ Threshold must be at least 1 minute\\.",
                    parse_mode='MarkdownV2'
                )
                return
            
            if minutes > 60:
                await update.message.reply_text(
                    "❌ Threshold cannot be more than 60 minutes\\.",
                    parse_mode='MarkdownV2'
                )
                return

            if self.db.set_user_threshold(chat_id, minutes):
                await update.message.reply_text(
                    f"✅ Threshold updated to `{minutes}` minutes\\.",
                    parse_mode='MarkdownV2'
                )
            else:
                await update.message.reply_text(
                    "❌ Failed to update threshold\\. Please try again\\.",
                    parse_mode='MarkdownV2'
                )

        except ValueError:
            await update.message.reply_text(
                "❌ Please provide a valid number of minutes\\.",
                parse_mode='MarkdownV2'
            )

    # Broadcast feature methods (hidden as "beacon" command)
    async def broadcast_command_start(self, update, context):
        """Start the broadcast command process (hidden as "beacon")"""
        await update.message.reply_text(
            "🔐 Admin authentication required\\.\n"
            "Please enter the broadcast password\\:",
            parse_mode='MarkdownV2'
        )
        return ENTER_PASSWORD

    async def broadcast_check_password(self, update, context):
        """Check the password for broadcasting"""
        chat_id = update.effective_chat.id
        entered_password = update.message.text.strip()
        
        # Delete the password message for security
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except Exception as e:
            logger.warning(f"Could not delete password message: {e}")
        
        if entered_password == BROADCAST_PASSWORD:
            await update.message.reply_text(
                "✅ Password correct\\!\n"
                "Please enter the message you want to broadcast to all users\\:",
                parse_mode='MarkdownV2'
            )
            # Store user id for later use
            self.broadcast_data[chat_id] = {"authenticated": True}
            return ENTER_MESSAGE
        else:
            await update.message.reply_text(
                "❌ Incorrect password\\. Broadcast cancelled\\.",
                parse_mode='MarkdownV2'
            )
            if chat_id in self.broadcast_data:
                del self.broadcast_data[chat_id]
            return ConversationHandler.END

    async def broadcast_send_message(self, update, context):
        """Send the broadcast message to all users"""
        chat_id = update.effective_chat.id
        message_text = update.message.text
        
        if chat_id not in self.broadcast_data or not self.broadcast_data[chat_id].get("authenticated"):
            await update.message.reply_text(
                "❌ Authentication error\\. Please restart the broadcast command\\.",
                parse_mode='MarkdownV2'
            )
            return ConversationHandler.END
        
        # Clean up stored data
        del self.broadcast_data[chat_id]
        
        # Get all users
        users = self.db.get_all_users()
        if not users:
            await update.message.reply_text(
                "No users found in database\\. Message not sent\\.",
                parse_mode='MarkdownV2'
            )
            return ConversationHandler.END
        
        # Format the broadcast message with a header
        broadcast_message = (
            "📢 *BROADCAST MESSAGE*\n\n"
            f"{message_text}"
        )
        
        # Send to all users
        sent_count = 0
        failed_count = 0
        
        await update.message.reply_text(
            f"Sending broadcast message to {len(users)} users\\.\\.\\.",
            parse_mode='MarkdownV2'
        )
        
        for user_id in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=broadcast_message,
                    parse_mode='Markdown'  # Use simpler Markdown to avoid escaping issues
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to {user_id}: {e}")
                failed_count += 1
        
        await update.message.reply_text(
            f"✅ Broadcast complete\\!\n"
            f"• Message sent to: {sent_count} users\n"
            f"• Failed deliveries: {failed_count}",
            parse_mode='MarkdownV2'
        )
        
        return ConversationHandler.END

    async def broadcast_cancel(self, update, context):
        """Cancel the broadcast conversation"""
        chat_id = update.effective_chat.id
        if chat_id in self.broadcast_data:
            del self.broadcast_data[chat_id]
            
        await update.message.reply_text(
            "Broadcast cancelled\\.",
            parse_mode='MarkdownV2'
        )
        return ConversationHandler.END

    # New conversation methods for subscription flow
    async def subscribe_command_start(self, update, context):
        """Start the subscription process conversation"""
        # If gateway ID is provided in the command, use the direct approach
        if context.args:
            return await self.subscribe_command(update, context)
        
        # Otherwise, start the conversation flow
        await update.message.reply_text(
            "Please enter the gateway ID you want to subscribe to:",
            parse_mode='Markdown'
        )
        return ENTER_GATEWAY_ID

    async def subscribe_process_gateway_id(self, update, context):
        """Process the gateway ID entered by the user"""
        gateway_id = update.message.text.strip()
        chat_id = update.effective_chat.id
        
        # Create a context.args-like structure for the existing command
        context.args = [gateway_id]
        
        # Call the existing command handler
        await self.subscribe_command(update, context)
        
        # End the conversation
        return ConversationHandler.END

    async def subscribe_cancel(self, update, context):
        """Cancel the subscription conversation"""
        await update.message.reply_text(
            "Subscription cancelled.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    async def handle_natural_language(self, update, context):
        """Handle natural language inputs without requiring slash commands"""
        message_text = update.message.text.lower().strip()
        chat_id = update.effective_chat.id
        
        # Simple keyword-based intent detection
        
        # Check for status related keywords
        if any(keyword in message_text for keyword in ['status', 'how are my gateways', 'check gateways']):
            # Create a mock context with no args
            context.args = []
            await self.status_command(update, context)
            return
            
        # Check for statistics related keywords
        elif any(keyword in message_text for keyword in ['stats', 'statistics', 'dashboard', 'energy', 'production', 'power']):
            await self.stats_command(update, context)
            return
            
        # Check for help related keywords
        elif any(keyword in message_text for keyword in ['help', 'commands', 'what can you do', 'how do i']):
            await self.help_command(update, context)
            return
            
        # Check for subscription with a gateway ID
        elif 'subscribe' in message_text:
            # Try to extract a gateway ID - looking for alphanumeric string of reasonable length
            import re
            gateway_matches = re.findall(r'\b([a-zA-Z0-9]{10,30})\b', message_text)
            
            if gateway_matches:
                # Found a potential gateway ID
                context.args = [gateway_matches[0]]
                await self.subscribe_command(update, context)
                return
            else:
                # No gateway ID found, start the subscription conversation
                return await self.subscribe_command_start(update, context)
                
        # Check for unsubscribe command
        elif any(keyword in message_text for keyword in ['unsubscribe', 'stop monitoring', 'remove gateway']):
            # Check if there's a gateway ID
            import re
            gateway_matches = re.findall(r'\b([a-zA-Z0-9]{10,30})\b', message_text)
            
            if gateway_matches:
                context.args = [gateway_matches[0]]
            else:
                context.args = []
                
            await self.unsubscribe_command(update, context)
            return
            
        # Check for threshold related commands
        elif any(keyword in message_text for keyword in ['threshold', 'interval', 'check time', 'minutes']):
            # Try to extract a number for minutes
            import re
            minute_matches = re.findall(r'\b(\d+)\b', message_text)
            
            if minute_matches:
                context.args = [minute_matches[0]]
            else:
                context.args = []
                
            await self.threshold_command(update, context)
            return
            
        # Greeting messages
        elif any(keyword in message_text for keyword in ['hi', 'hello', 'hey', 'howdy', 'greetings']):
            await update.message.reply_text(
                f"👋 Hello! I'm your energy monitoring assistant. How can I help you today?\n\n"
                f"You can ask me to check your gateway status, show statistics, or help you subscribe to a gateway.",
                parse_mode='Markdown'
            )
            return
            
        # Thank you messages
        elif any(keyword in message_text for keyword in ['thank', 'thanks', 'thx']):
            await update.message.reply_text(
                "You're welcome! I'm here to help with all your energy monitoring needs. Anything else I can do for you?",
                parse_mode='Markdown'
            )
            return
            
        # Default response for unrecognized messages
        await update.message.reply_text(
            "I'm not sure what you mean. Here are some things you can ask me:\n\n"
            "• Check the status of your gateways\n"
            "• Show energy statistics\n"
            "• Subscribe to a gateway\n"
            "• Change your threshold setting\n"
            "• Help with commands\n\n"
            "Feel free to ask in your own words or use commands like /status or /help if you prefer.",
            parse_mode='Markdown'
        )

def main():
    """Main entry point"""
    monitor = GatewayMonitor()
    
    async def run_bot():
        try:
            await monitor.run()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Error in main: {e}")
        finally:
            monitor._should_stop = True
            await monitor.shutdown()
    
    # Run the bot
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()

