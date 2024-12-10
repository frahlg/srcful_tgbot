import asyncio
import aiohttp
import logging
from datetime import datetime, timezone, timedelta
from telegram.ext import Application, CommandHandler
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
AUTH_TOKEN = os.getenv('AUTH_TOKEN')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '60'))  # seconds
DB_PATH = os.getenv('DB_PATH', 'bot_data.db')
DEFAULT_TIMEOUT_MINUTES = 10  # Default timeout for considering a gateway offline

if not all([TELEGRAM_TOKEN, AUTH_TOKEN]):
    raise ValueError("Missing required environment variables: TELEGRAM_TOKEN and AUTH_TOKEN must be set")

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

class GatewayMonitor:
    def __init__(self):
        self.db = Database(DB_PATH)
        self.bot = None
        self._should_stop = False
        self.application = None

    async def start_polling(self):
        """Start background polling of gateway status"""
        logger.info("Starting gateway polling...")
        last_status = {}  # Track last known status of each gateway
        
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
                        is_online = self.check_gateway_status(gateway_data, der_latest_data)
                        
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
                            
                            # Update database and notify subscribers
                            subscribers = self.db.get_gateway_subscribers(gateway_id)
                            if subscribers and self.bot:
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
                
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
            
            # Wait for next poll
            await asyncio.sleep(CHECK_INTERVAL)

    async def setup(self):
        """Initialize the application"""
        # Initialize application
        self.application = Application.builder().token(TELEGRAM_TOKEN).build()
        self.bot = self.application.bot

        # Add command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("subscribe", self.subscribe_command))
        self.application.add_handler(CommandHandler("unsubscribe", self.unsubscribe_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))  # New admin command

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
            
            # Start polling task
            polling_task = asyncio.create_task(self.start_polling())
            
            # Keep the application running
            while not self._should_stop:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Error running bot: {e}")
            raise
        finally:
            # Cleanup
            await self.shutdown()

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

    def check_gateway_status(self, gateway_data, der_latest_data):
        """
        Check if gateway is online based on latest datapoint
        Returns: bool indicating if gateway is online
        """
        if not gateway_data or 'ders' not in gateway_data:
            return False

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

        # Check if the latest timestamp is within the last minute
        now = datetime.now(timezone.utc)
        return (now - latest_ts).total_seconds() <= 60  # 1 minute threshold

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
                der_name = self.escape_markdown(der['name'])
                der_info.append(f"• Name: {der_name}")
                
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
                is_online = self.check_gateway_status(gateway_data, der_latest_data)
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
        welcome_message = (
            "👋 *Welcome to Srcful Monitor\\!*\n\n"
            "I help you track gateway status and send notifications\\.\n\n"
            "*Quick Start:*\n"
            "• /status \\- Check gateways\n"
            "• /subscribe \\- Add gateway\n"
            "• /help \\- More info\n\n"
            "Start by using /subscribe with your gateway ID\\!"
        )
        await update.message.reply_text(welcome_message, parse_mode='MarkdownV2')

    async def help_command(self, update, context):
        """Handler for /help command"""
        help_message = (
            "📚 *Srcful Gateway Monitor Help*\n\n"
            "*Commands:*\n"
            "• /status \\- Shows gateway status\n"
            "• /subscribe \\- Monitor a gateway\n"
            "• /unsubscribe \\- Stop monitoring\n"
            "• /stats \\- Show bot statistics\n"
            "• /help \\- Show this help\n\n"
            "*Status Information:*\n"
            "• 🟢 Online \\- Recent data available\n"
            "• 🔴 Offline \\- No recent data\n\n"
            "*Example:*\n"
            "To monitor a gateway:\n"
            "`/subscribe 01233d032a7c838bee`"
        )
        await update.message.reply_text(help_message, parse_mode='MarkdownV2')

    async def subscribe_command(self, update, context):
        """Handler for /subscribe command"""
        if not context.args:
            await update.message.reply_text(
                "❗️ Please provide a gateway ID\\.\n"
                "Example: `/subscribe 01233d032a7c838bee`",
                parse_mode='MarkdownV2'
            )
            return

        gateway_id = context.args[0]
        chat_id = update.effective_chat.id

        # Verify gateway exists
        result = await self.fetch_gateway_status(gateway_id)
        if not result:
            await update.message.reply_text(
                "❌ Invalid gateway ID or gateway not found\\.\n"
                "Please check the ID and try again\\.",
                parse_mode='MarkdownV2'
            )
            return

        gateway_data, _ = result
        if self.db.add_subscription(chat_id, gateway_id):
            await update.message.reply_text(
                f"✅ Successfully subscribed to gateway:\n"
                f"Name: {gateway_data['name']}\n"
                f"ID: `{gateway_id}`\n\n"
                f"You'll receive notifications when status changes\\.\n"
                f"Use /status to check current status\\.",
                parse_mode='MarkdownV2'
            )
        else:
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
            "Authorization": f"Bearer {AUTH_TOKEN}",
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
            "Authorization": f"Bearer {AUTH_TOKEN}",
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
        """Handler for /stats command - shows bot usage statistics"""
        try:
            # Get stats from database
            total_users = len(self.db.get_all_users())
            subscriptions = self.db.get_subscription_stats()
            monitored_gateways = len(set(sub['gateway_id'] for sub in subscriptions))
            
            # Format message
            message_parts = [
                "*Sourceful Bot Statistics*",
                f"Total Users: `{total_users}`",
                f"Monitored Gateways: `{monitored_gateways}`",
                "",
                "*Most Monitored Gateways:*"
            ]

            # Add top 5 gateways by subscription count
            for gw in subscriptions[:5]:
                name = self.escape_markdown(gw['name'])
                message_parts.append(
                    f"• {name}\n"
                    f"  Subscribers: `{gw['subscriber_count']}`"
                )

            await update.message.reply_text(
                '\n'.join(message_parts),
                parse_mode='MarkdownV2'
            )

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            await update.message.reply_text(
                "Error getting statistics\\. Please try again later\\.",
                parse_mode='MarkdownV2'
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