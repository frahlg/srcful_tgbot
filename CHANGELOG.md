# Changelog

All notable changes to the Sourceful Energy Telegram Bot will be documented in this file.

## [0.1.8] - 2024-03-08

### Added
- Conversational natural language interface
  - Users can interact without using slash commands
  - Bot understands natural language requests
  - Responds to greetings and thanks
  - Extracts gateway IDs and numbers from text
  - Provides helpful guidance when commands aren't recognized

## [0.1.7] - 2024-03-08

### Added
- Enhanced statistics dashboard
  - Shows real-time power generation metrics
  - Calculates efficiency based on capacity vs. current production
  - Breaks down energy sources by type
  - Provides personalized statistics for each user
  - Displays power values with appropriate units (W, kW, MW)

## [0.1.6] - 2024-03-08

### Added
- Improved first-time user experience
  - Custom welcome message for users with no subscriptions
  - Step-by-step guidance for new users
  - Different messaging for returning users

### Changed
- Simplified DER information display
  - Removed DER names from status messages
  - Now shows only DER type, make, and power
- Improved notification behavior
  - Bot no longer sends startup messages when restarted
  - Only sends notifications for actual gateway status changes
  - Prioritizes offline notifications over online ones

## [0.1.5] - 2024-03-08

### Added
- Hidden admin beacon feature
  - Added secret `/beacon` command with password authentication
  - Allows administrators to send announcements to all users
  - Not displayed in help documentation or public interfaces
- Improved subscription flow with conversation handling
  - Users can now enter gateway ID as a response when prompted
  - More natural interaction when subscribing to gateways

## [0.1.4] - 2024-12-12

### Changed
- Removed AUTH_TOKEN requirement as it's not needed for API access
- Fixed message formatting for MarkdownV2 in all bot responses
- Improved startup message handling
- Updated environment templates for better consistency
- Fixed database directory handling in Docker setup
