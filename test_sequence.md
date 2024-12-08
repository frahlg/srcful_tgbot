# Srcful Bot Test Sequence

## 1. Basic Command Flow
```
/start
```
Expected: Welcome message with basic commands

```
/help
```
Expected: Detailed help message with all commands

## 2. Gateway Subscription
```
/subscribe 01233d032a7c838bee
```
Expected: Confirmation of subscription to "Cool Rosewood Camel"

## 3. Status Check
```
/status
```
Expected:
- Gateway name
- Online/Offline status
- Last datapoint time
- Power information
- DER details

## 4. Database Persistence Test
```bash
# In terminal:
docker-compose down
docker-compose up --build
```
Then in Telegram:
```
/status
```
Expected: Should still show the gateway status (subscription persisted)

## 5. Timeout Testing
```
/set_timeout 5
/status
```
Expected: Different online/offline status with 5-minute timeout

```
/set_timeout 15
/status
```
Expected: Different status with 15-minute timeout

## 6. Error Cases
```
/subscribe invalid_id
```
Expected: Error message about invalid gateway

```
/subscribe 01233d032a7c838bee
```
Expected: Already subscribed message

## 7. Unsubscribe Flow
```
/unsubscribe
```
Expected: List of subscribed gateways

```
/unsubscribe 01233d032a7c838bee
```
Expected: Confirmation of unsubscribe

## 8. Final Check
```
/status
```
Expected: No subscribed gateways message

## Test Results Checklist
- [ ] Welcome message formatting correct
- [ ] Help message displays without errors
- [ ] Gateway subscription works
- [ ] Status shows correct information
- [ ] Database persists after restart
- [ ] Timeout changes affect status
- [ ] Error messages are helpful
- [ ] Unsubscribe works correctly