# Sourceful Energy Telegram Bot

A Telegram bot for monitoring Sourceful Energy gateways in real-time. Get instant notifications when your gateways go online or offline, and monitor power production from your DERs (Distributed Energy Resources).

## Features

- Real-time gateway status monitoring
- Instant notifications on state changes (online/offline)
- Power production monitoring
- Multiple gateway support
- Easy subscription management
- Gateway statistics
- Automatic state detection based on data points
- Docker support for easy deployment

## Commands

- `/start` - Initialize the bot and get welcome message
- `/status` - Check current gateway status
- `/subscribe` - Monitor a gateway
- `/unsubscribe` - Stop monitoring
- `/stats` - View bot statistics
- `/help` - Show help information

## Status Information

- 🟢 ONLINE - Gateway has reported data within the last minute
- 🔴 OFFLINE - No data received for over a minute
- Power production in watts
- Last data point timestamp
- DER information (name, make, nominal power)

## Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and configure:
   ```
   TELEGRAM_TOKEN=your_bot_token
   API_URL=https://api.srcful.dev/
   AUTH_TOKEN=your_auth_token
   CHECK_INTERVAL=60
   ```
3. Run with Docker:
   ```bash
   docker-compose up -d
   ```

## Docker Support

The bot runs in a Docker container with:
- Persistent storage for database
- Automatic restart on failure
- Log rotation
- Volume mounts for data and logs

## Author

Created by Fredrik Ahlgren as a hobby project.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 