# Changelog

All notable changes to the Sourceful Energy Telegram Bot will be documented in this file.

## [0.1.4] - 2024-12-12

### Changed
- Removed AUTH_TOKEN requirement as it's not needed for API access
- Fixed message formatting for MarkdownV2 in all bot responses
- Improved startup message handling
- Updated environment templates for better consistency
- Fixed database directory handling in Docker setup

## [0.1.3] - 2024-12-10

### Added
- User-configurable threshold for gateway status checks
- Version tracking and display
- Bot restart notifications to users
- Improved help messages with dynamic threshold display

### Changed
- Default status check threshold from 1 minute to 5 minutes
- Help message now shows user's current threshold setting

## [0.1.2] - 2024-12-08

### Added
- Gateway statistics command (/stats)
- Improved status message formatting
- Better error handling for timestamps

### Changed
- Status messages now show power production
- Improved DER information display

## [0.1.1] - 2024-12-07

### Added
- Real-time gateway status monitoring
- Instant notifications for state changes
- Basic subscription management
- Docker support

### Changed
- Switched to SQLite for data persistence
- Improved error handling

## [0.1.0] - 2024-12-07

### Added
- Initial bot implementation
- Basic command structure
- Gateway status checking
- Subscription system 