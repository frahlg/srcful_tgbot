# Sourceful Energy Telegram Bot

A Telegram bot for monitoring Sourceful Energy gateways in real-time. Get instant notifications when your gateways go online or offline, and monitor power production from your DERs (Distributed Energy Resources).

Current version: 0.1.4

## Features

- Real-time gateway status monitoring
- Instant notifications on state changes (online/offline)
- Power production monitoring
- Multiple gateway support
- Easy subscription management
- Gateway statistics
- Automatic state detection based on data points
- User-configurable status thresholds
- Docker support for easy deployment

## Commands

- `/start` - Initialize the bot and get welcome message
- `/status` - Check current gateway status
- `/subscribe` - Monitor a gateway
- `/unsubscribe` - Stop monitoring
- `/threshold` - Set status check interval (1-60 minutes)
- `/stats` - View bot statistics
- `/help` - Show help information
- `/broadcast` - Send a message to all users (password required)

## Status Information

- 🟢 ONLINE - Gateway has reported data within user's threshold (default: 5 minutes)
- 🔴 OFFLINE - No data received for longer than threshold
- Power production in watts
- Last data point timestamp
- DER information (name, make, nominal power)

## Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and configure:
   ```
   TELEGRAM_TOKEN=your_bot_token
   API_URL=https://api.srcful.dev/
   CHECK_INTERVAL=60
   DB_PATH=/app/data/bot_data.db
   # Set a password for the broadcast functionality
   BROADCAST_PASSWORD=your_secure_password
   # Optional: Add admin user IDs for broadcast functionality
   ADMIN_USER_IDS=123456789,987654321
   ```
3. Run with Docker:
   ```bash
   docker-compose up -d
   ```

## Admin Features

Administrators can send broadcast messages to all users with the `/broadcast` command. 
To use admin features:

1. Add your Telegram user ID to the ADMIN_USER_IDS environment variable (comma-separated list)
2. Restart the bot
3. Use `/broadcast YOUR_PASSWORD Your message here` to send announcements to all users
4. Admin commands are only visible to authorized users

## Docker Support

The bot runs in a Docker container with:
- Persistent storage for database
- Automatic restart on failure
- Log rotation
- Volume mounts for data and logs

## Version History

See [CHANGELOG.md](CHANGELOG.md) for version history and changes.

## Author

Created by Fredrik Ahlgren as a hobby project.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 

## Broadcast Messages

Any user can send broadcast messages to all users by using the right password:

1. Set a secure BROADCAST_PASSWORD in your environment configuration
2. Use `/broadcast YOUR_PASSWORD Your message here` to send announcements
3. The message will be sent to all subscribed users 