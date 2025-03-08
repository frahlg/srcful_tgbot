import os
import json
import logging
import requests
from flask import Flask, request

# Configure logging
logger = logging.getLogger(__name__)

# Facebook Messenger API settings
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN')
FB_VERIFY_TOKEN = os.getenv('FB_VERIFY_TOKEN')

# Create Flask app
app = Flask(__name__)

class FacebookMessenger:
    def __init__(self, energy_monitor=None):
        """Initialize Facebook Messenger integration
        
        Args:
            energy_monitor: Instance of the GatewayMonitor to handle the core functionality
        """
        self.energy_monitor = energy_monitor
        if not FB_PAGE_ACCESS_TOKEN:
            logger.warning("FB_PAGE_ACCESS_TOKEN not set, Facebook Messenger responses will not work")
        if not FB_VERIFY_TOKEN:
            logger.warning("FB_VERIFY_TOKEN not set, webhook verification will fail")

    def setup_routes(self):
        """Setup Flask routes for Facebook Messenger webhook"""
        @app.route('/webhook', methods=['GET'])
        def webhook_verification():
            """Handle webhook verification from Facebook"""
            if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == FB_VERIFY_TOKEN:
                logger.info("Facebook webhook verification successful")
                return request.args.get('hub.challenge')
            else:
                logger.warning("Facebook webhook verification failed")
                return 'Verification failed', 403

        @app.route('/webhook', methods=['POST'])
        def webhook_handler():
            """Handle incoming messages from Facebook Messenger"""
            data = request.json
            logger.info(f"Received webhook data: {data}")
            
            # Check if this is a page webhook
            if data.get('object') == 'page':
                for entry in data.get('entry', []):
                    for messaging_event in entry.get('messaging', []):
                        # Extract sender and message info
                        sender_id = messaging_event.get('sender', {}).get('id')
                        
                        # Handle message
                        if messaging_event.get('message'):
                            self.handle_message(sender_id, messaging_event.get('message'))
                
                return 'EVENT_RECEIVED'
            
            return 'NOT_SUPPORTED_EVENT', 404

    def handle_message(self, sender_id, message_data):
        """Process incoming Facebook message
        
        Args:
            sender_id: Facebook user ID who sent the message
            message_data: Message content from Facebook
        """
        if not self.energy_monitor:
            logger.error("Energy monitor not configured for Facebook Messenger")
            self.send_message(sender_id, "Sorry, the energy monitoring system is not properly configured.")
            return
            
        # Extract message text
        message_text = message_data.get('text', '')
        if not message_text:
            self.send_message(sender_id, "I can only process text messages. Please send me a text command.")
            return
            
        logger.info(f"Received message from {sender_id}: {message_text}")
        
        # Process the message using natural language handler - will be implemented later
        self.process_natural_language(sender_id, message_text)

    async def process_natural_language(self, sender_id, message_text):
        """Process the message using natural language understanding
        
        This will be connected to the GatewayMonitor's natural language processing
        """
        if not self.energy_monitor:
            self.send_message(sender_id, "Sorry, the energy monitoring system is not properly configured.")
            return
            
        try:
            # Try to get a common response from the energy monitor
            response = await self.energy_monitor.process_message(message_text)
            
            if response:
                # We got a general response from the common handler
                self.send_message(sender_id, response)
                return
                
            # If we didn't get a common response, use the Facebook-specific handler
            fb_response = self.energy_monitor.handle_facebook_message(sender_id, message_text)
            if fb_response:
                self.send_message(sender_id, fb_response)
                return
                
            # If we still don't have a response, send a default message
            self.send_message(
                sender_id,
                "I'm not sure what you mean. You can ask me about energy statistics, "
                "gateway status, or get help with available commands."
            )
            
        except Exception as e:
            logger.error(f"Error processing natural language: {e}")
            self.send_message(
                sender_id, 
                "Sorry, I encountered an error processing your message. Please try again later."
            )

    def send_message(self, recipient_id, message_text):
        """Send message to Facebook Messenger user
        
        Args:
            recipient_id: Facebook user ID to send the message to
            message_text: Text content to send
        """
        if not FB_PAGE_ACCESS_TOKEN:
            logger.error("Cannot send message: FB_PAGE_ACCESS_TOKEN not set")
            return
            
        message_data = {
            "messaging_type": "RESPONSE",
            "recipient": {
                "id": recipient_id
            },
            "message": {
                "text": message_text
            }
        }
        
        response = requests.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": FB_PAGE_ACCESS_TOKEN},
            json=message_data
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to send message to {recipient_id}. Status: {response.status_code}, Response: {response.text}")
        else:
            logger.info(f"Successfully sent message to {recipient_id}")

def run_facebook_server(port=3001, energy_monitor=None):
    """Run the Facebook Messenger webhook server
    
    Args:
        port: Port to listen on
        energy_monitor: Instance of GatewayMonitor to handle core functionality
    """
    messenger = FacebookMessenger(energy_monitor)
    messenger.setup_routes()
    logger.info(f"Starting Facebook Messenger server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False) 