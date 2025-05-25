# Sourceful Energy Telegram Bot

A Telegram bot for monitoring Sourceful Energy gateways in real-time. Get instant notifications when your gateways go online or offline, and monitor power production from your DERs (Distributed Energy Resources).

Current version: 0.1.8

## Features

- **Real-time Gateway Monitoring**: Receive immediate notifications when gateway status changes
- **Power Production Tracking**: View current power output from all connected DERs
- **Customizable Thresholds**: Set your own threshold for online/offline status
- **User-friendly Commands**: Simple commands to check status and manage subscriptions
- **Improved Subscription Flow**: Natural conversation flow when subscribing to gateways

## Setup

1. Clone this repository
2. Copy `.env.template` to `.env` and configure:
   ```
   TELEGRAM_TOKEN=your_bot_token
   API_URL=https://api.srcful.dev/
   CHECK_INTERVAL=60
   DB_PATH=/app/data/bot_data.db
   BROADCAST_PASSWORD=your_secure_password
   ```
3. Use Docker to build and run the bot:
   ```bash
   docker-compose up --build
   ```

## Commands

- `/start` - Initialize the bot
- `/help` - Display all available commands
- `/status` - Check the status of all subscribed gateways
- `/subscribe <gateway_id>` - Subscribe to a gateway
- `/unsubscribe <gateway_id>` - Unsubscribe from a gateway
- `/threshold <minutes>` - Set the threshold for online/offline status
- `/stats` - Show bot statistics

