import sqlite3
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        """Initialize database schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Create gateway_status table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gateway_status (
                    gateway_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_online INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT NOT NULL,
                    status_factors TEXT,
                    last_updated TEXT NOT NULL
                )
            ''')
            
            # Create subscriptions table if not exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    chat_id INTEGER,
                    gateway_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, gateway_id),
                    FOREIGN KEY (gateway_id) REFERENCES gateway_status(gateway_id)
                )
            ''')

            # Create user settings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    chat_id INTEGER PRIMARY KEY,
                    threshold_minutes INTEGER NOT NULL DEFAULT 5,
                    last_version_seen TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            ''')
            
            conn.commit()

    def update_gateway_status(self, gateway_id: str, name: str, is_online: bool, timestamp: str, status_factors: str) -> bool:
        """Update gateway status and return True if state changed"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Get current status
                cursor.execute('SELECT is_online FROM gateway_status WHERE gateway_id = ?', (gateway_id,))
                result = cursor.fetchone()
                
                if result is None:
                    # New gateway
                    cursor.execute('''
                        INSERT INTO gateway_status 
                        (gateway_id, name, is_online, last_seen, status_factors, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (gateway_id, name, is_online, timestamp, status_factors, timestamp))
                    state_changed = True
                else:
                    # Existing gateway
                    old_status = bool(result[0])
                    if old_status != is_online:
                        # Status changed
                        cursor.execute('''
                            UPDATE gateway_status 
                            SET name = ?, is_online = ?, last_seen = ?, 
                                status_factors = ?, last_updated = ?
                            WHERE gateway_id = ?
                        ''', (name, is_online, timestamp, status_factors, timestamp, gateway_id))
                        state_changed = True
                    else:
                        # Just update last_seen and status_factors
                        cursor.execute('''
                            UPDATE gateway_status 
                            SET name = ?, last_seen = ?, status_factors = ?, last_updated = ?
                            WHERE gateway_id = ?
                        ''', (name, timestamp, status_factors, timestamp, gateway_id))
                        state_changed = False
                
                conn.commit()
                return state_changed
        except Exception as e:
            logger.error(f"Error updating gateway status: {e}")
            return False

    def get_gateway_subscribers(self, gateway_id: str) -> List[int]:
        """Get all chat IDs subscribed to a gateway"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT chat_id 
                FROM subscriptions 
                WHERE gateway_id = ?
            ''', (gateway_id,))
            return [row[0] for row in cursor.fetchall()]

    def subscribe_to_gateway(self, chat_id: int, gateway_id: str) -> bool:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute('''
                    INSERT INTO subscriptions (chat_id, gateway_id, created_at)
                    VALUES (?, ?, ?)
                ''', (chat_id, gateway_id, now))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def unsubscribe_from_gateway(self, chat_id: int, gateway_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM subscriptions
                WHERE chat_id = ? AND gateway_id = ?
            ''', (chat_id, gateway_id))
            changed = cursor.rowcount > 0
            conn.commit()
            return changed

    def get_gateways_not_in(self, gateway_ids: List[str]) -> List[Dict]:
        """Get all gateways that are not in the provided list of IDs and were seen in the last hour"""
        if not gateway_ids:
            return []
            
        placeholders = ','.join(['?' for _ in gateway_ids])
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        
        query = f'''
            SELECT id, name, wallet, is_online, last_seen
            FROM gateways
            WHERE id NOT IN ({placeholders})
            AND is_online = 1
            AND last_seen > ?
        '''
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, gateway_ids + [one_hour_ago])
            return [{
                'id': row[0],
                'name': row[1],
                'wallet': row[2],
                'is_online': bool(row[3]),
                'last_seen': datetime.fromisoformat(row[4])
            } for row in cursor.fetchall()]

    def get_gateway_stats(self) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc)
            offline_threshold = (now - timedelta(minutes=5)).isoformat()
            
            # Get all gateways
            cursor.execute('SELECT COUNT(*) FROM gateways')
            total = cursor.fetchone()[0]
            
            # Get online count (seen in last 5 minutes and marked as online)
            cursor.execute('''
                SELECT COUNT(*) FROM gateways 
                WHERE last_seen > ? AND is_online = 1
            ''', (offline_threshold,))
            online = cursor.fetchone()[0]
            
            # Get gateway type breakdown (handle case where type column might not exist)
            try:
                cursor.execute('''
                    SELECT COALESCE(type, 'UNKNOWN') as type, COUNT(*) as count
                    FROM gateways
                    GROUP BY type
                ''')
                types = {row[0]: row[1] for row in cursor.fetchall()}
            except sqlite3.OperationalError:
                types = {'UNKNOWN': total}
            
            # Get recent changes, but only within the last hour
            one_hour_ago = (now - timedelta(hours=1)).isoformat()
            try:
                cursor.execute('''
                    SELECT id, name, wallet, is_online, last_state_change, type
                    FROM gateways
                    WHERE last_state_change > ?
                    ORDER BY last_state_change DESC
                    LIMIT 5
                ''', (one_hour_ago,))
            except sqlite3.OperationalError:
                # Fallback query without type column
                cursor.execute('''
                    SELECT id, name, wallet, is_online, last_state_change
                    FROM gateways
                    WHERE last_state_change > ?
                    ORDER BY last_state_change DESC
                    LIMIT 5
                ''', (one_hour_ago,))
            
            recent_changes = []
            for row in cursor.fetchall():
                change = {
                    'id': row[0],
                    'name': row[1],
                    'wallet': row[2],
                    'is_online': bool(row[3]),
                    'last_state_change': datetime.fromisoformat(row[4]),
                }
                if len(row) > 5:  # If type column exists
                    change['type'] = row[5]
                else:
                    change['type'] = 'UNKNOWN'
                recent_changes.append(change)
            
            return {
                'total': total,
                'online': online,
                'offline': total - online,
                'types': types,
                'recent_changes': recent_changes
            }

    def update_gateway_status(self, gateway_id, name, is_online, timestamp, status_factors):
        """Update gateway status in the database"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO gateway_status 
                    (gateway_id, name, is_online, last_seen, status_factors, last_updated) 
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (gateway_id, name, is_online, timestamp, status_factors, timestamp))
                conn.commit()
        except Exception as e:
            logger.error(f"Error updating gateway status: {e}")

    def create_tables(self):
        """Create necessary database tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Gateway status table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gateway_status (
                    gateway_id TEXT PRIMARY KEY,
                    name TEXT,
                    is_online BOOLEAN,
                    last_seen TIMESTAMP,
                    status_factors TEXT,
                    last_updated TIMESTAMP
                )
            ''')
            
            # Subscriptions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    chat_id INTEGER,
                    gateway_id TEXT,
                    PRIMARY KEY (chat_id, gateway_id)
                )
            ''')
            
            # Status change notifications table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS status_notifications (
                    chat_id INTEGER,
                    gateway_id TEXT,
                    last_status BOOLEAN,
                    last_notification TIMESTAMP,
                    PRIMARY KEY (chat_id, gateway_id)
                )
            ''')
            
            conn.commit()

    def get_all_gateway_ids(self) -> List[str]:
        """Get all gateway IDs that have subscriptions"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT gateway_id 
                FROM subscriptions
            ''')
            return [row[0] for row in cursor.fetchall()]

    def get_gateway_info(self, gateway_id: str) -> Optional[Dict]:
        """Get gateway information including status factors"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, is_online, last_seen, status_factors
                FROM gateway_status
                WHERE gateway_id = ?
            ''', (gateway_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'name': row[0],
                    'is_online': bool(row[1]),
                    'last_seen': row[2],
                    'status_factors': json.loads(row[3]) if row[3] else None
                }
            return None

    def check_and_update_notification_status(self, gateway_id: str) -> bool:
        """Check if gateway status has changed since last notification"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get current status and last notification status
            cursor.execute('''
                SELECT g.is_online, n.last_status
                FROM gateways g
                LEFT JOIN status_notifications n ON g.id = n.gateway_id
                WHERE g.id = ?
            ''', (gateway_id,))
            
            row = cursor.fetchone()
            if not row:
                return False
                
            current_status = bool(row[0])
            last_notified_status = bool(row[1]) if row[1] is not None else None
            
            if last_notified_status is None or current_status != last_notified_status:
                # Status changed or first notification
                cursor.execute('''
                    INSERT OR REPLACE INTO status_notifications 
                    (gateway_id, last_status, last_notification)
                    VALUES (?, ?, ?)
                ''', (gateway_id, current_status, datetime.now(timezone.utc).isoformat()))
                return True
                
            return False

    def get_user_subscriptions(self, chat_id: int) -> List[str]:
        """Get all gateway IDs that a user is subscribed to"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT gateway_id 
                FROM subscriptions 
                WHERE chat_id = ?
            ''', (chat_id,))
            return [row[0] for row in cursor.fetchall()]

    def add_subscription(self, chat_id: int, gateway_id: str) -> bool:
        """Add a new subscription"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()
                logger.info(f"Adding subscription: chat_id={chat_id}, gateway_id={gateway_id}")
                cursor.execute('''
                    INSERT INTO subscriptions (chat_id, gateway_id, created_at)
                    VALUES (?, ?, ?)
                ''', (chat_id, gateway_id, now))
                conn.commit()
                logger.info("Subscription added successfully")
                return True
        except sqlite3.IntegrityError:
            logger.info(f"Subscription already exists: chat_id={chat_id}, gateway_id={gateway_id}")
            return False
        except Exception as e:
            logger.error(f"Error adding subscription: {str(e)}")
            logger.exception(e)
            return False

    def remove_subscription(self, chat_id: int, gateway_id: str) -> bool:
        """Remove a subscription"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM subscriptions 
                WHERE chat_id = ? AND gateway_id = ?
            ''', (chat_id, gateway_id))
            return cursor.rowcount > 0

    def get_all_users(self) -> List[int]:
        """Get all unique users"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                logger.info("Executing query to get all users...")
                
                # Get users from both subscriptions and user_settings
                cursor.execute('''
                    SELECT DISTINCT chat_id 
                    FROM (
                        SELECT chat_id FROM subscriptions
                        UNION
                        SELECT chat_id FROM user_settings
                    )
                ''')
                users = [row[0] for row in cursor.fetchall()]
                logger.info(f"Database query returned {len(users)} users")
                return users
        except Exception as e:
            logger.error("Error getting users from database")
            logger.error(f"Error details: {str(e)}")
            logger.exception(e)
            return []

    def get_subscription_stats(self) -> List[Dict]:
        """Get statistics about gateway subscriptions"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    s.gateway_id,
                    g.name,
                    COUNT(s.chat_id) as subscriber_count
                FROM subscriptions s
                JOIN gateway_status g ON s.gateway_id = g.gateway_id
                GROUP BY s.gateway_id
                ORDER BY subscriber_count DESC
            ''')
            return [
                {
                    'gateway_id': row[0],
                    'name': row[1],
                    'subscriber_count': row[2]
                }
                for row in cursor.fetchall()
            ]

    def get_user_threshold(self, chat_id: int) -> int:
        """Get user's threshold setting in minutes, default is 5"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT threshold_minutes 
                FROM user_settings 
                WHERE chat_id = ?
            ''', (chat_id,))
            result = cursor.fetchone()
            return result[0] if result else 5  # Default to 5 minutes

    def set_user_threshold(self, chat_id: int, minutes: int) -> bool:
        """Set user's threshold setting in minutes"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute('''
                    INSERT INTO user_settings (chat_id, threshold_minutes, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        threshold_minutes = excluded.threshold_minutes,
                        updated_at = excluded.updated_at
                ''', (chat_id, minutes, now, now))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error setting user threshold: {e}")
            return False

    def ensure_user_exists(self, chat_id: int) -> None:
        """Make sure user exists in settings table"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute('''
                    INSERT OR IGNORE INTO user_settings 
                    (chat_id, threshold_minutes, created_at, updated_at)
                    VALUES (?, 5, ?, ?)
                ''', (chat_id, now, now))
                conn.commit()
        except Exception as e:
            logger.error(f"Error ensuring user exists: {e}")
            logger.exception(e)