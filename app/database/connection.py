"""SQLite connection and idempotent schema/data migrations."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    max_message_id TEXT NOT NULL UNIQUE,
    chat_id INTEGER NOT NULL,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('text','voice','poll')),
    timestamp TEXT NOT NULL,
    reply_to TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id,timestamp);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    interval_type TEXT NOT NULL DEFAULT 'weekly',
    custom_days INTEGER,
    summary_hour INTEGER NOT NULL DEFAULT 9,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow'
);
CREATE TABLE IF NOT EXISTS summary_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin','creator')),
    subscription_start TEXT,
    subscription_end TEXT,
    is_unlimited_subscription INTEGER NOT NULL DEFAULT 0 CHECK(is_unlimited_subscription IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    chat_type TEXT NOT NULL CHECK(chat_type IN ('chat','channel','dialog','unknown')),
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK(amount >= 0),
    token_type TEXT NOT NULL CHECK(token_type IN ('trial','purchased','subscription','promo')),
    expires_at TEXT,
    remaining_amount INTEGER NOT NULL CHECK(remaining_amount >= 0),
    source_id TEXT,
    created_at TEXT NOT NULL,
    CHECK(remaining_amount <= amount)
);
CREATE INDEX IF NOT EXISTS idx_token_grants_user ON token_grants(user_id,token_type,expires_at,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_token_grants_source ON token_grants(source_id) WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS token_operations (
    operation_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    grant_id INTEGER,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_operations_user ON token_operations(user_id,created_at);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ends_at TEXT,
    is_unlimited_subscription INTEGER NOT NULL DEFAULT 0 CHECK(is_unlimited_subscription IN (0,1)),
    source TEXT NOT NULL DEFAULT 'paid' CHECK(source IN ('paid','promo')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    warning_sent_for TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    payment_id TEXT UNIQUE,
    product_type TEXT NOT NULL CHECK(product_type IN ('tokens','subscription')),
    tariff TEXT NOT NULL,
    chat_id INTEGER,
    token_amount INTEGER NOT NULL DEFAULT 0,
    subscription_months INTEGER NOT NULL DEFAULT 0,
    original_amount INTEGER NOT NULL,
    final_amount INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','paid','cancelled','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paid_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id,created_at);

CREATE TABLE IF NOT EXISTS promo_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    usage_type TEXT NOT NULL CHECK(usage_type IN ('once','limited','unlimited')),
    max_uses INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
    reward_type TEXT NOT NULL CHECK(reward_type IN ('tokens','subscription','role')),
    token_amount INTEGER NOT NULL DEFAULT 0,
    subscription_months INTEGER NOT NULL DEFAULT 0,
    is_unlimited_subscription INTEGER NOT NULL DEFAULT 0 CHECK(is_unlimited_subscription IN (0,1)),
    admin_role TEXT CHECK(admin_role IN ('admin','creator')),
    expires_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((usage_type='limited' AND max_uses IS NOT NULL AND max_uses > 0) OR usage_type IN ('once','unlimited'))
);
CREATE INDEX IF NOT EXISTS idx_promo_active ON promo_codes(code,is_active,expires_at);
CREATE TABLE IF NOT EXISTS promo_code_usages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    promo_code_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reward_type TEXT NOT NULL,
    reward_amount INTEGER NOT NULL DEFAULT 0,
    used_at TEXT NOT NULL,
    UNIQUE(promo_code_id,user_id)
);
CREATE INDEX IF NOT EXISTS idx_promo_usages_user ON promo_code_usages(user_id,used_at);

CREATE TABLE IF NOT EXISTS chat_cooldowns (
    chat_id INTEGER PRIMARY KEY,
    cooldown_until TEXT,
    in_flight INTEGER NOT NULL DEFAULT 0 CHECK(in_flight IN (0,1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_states (
    user_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_context (
    poll_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    data_json TEXT NOT NULL,
    total_votes INTEGER,
    updated_at TEXT NOT NULL
);
"""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {r['name'] for r in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_messages(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'").fetchone()
    sql = (row[0] or '') if row else ''
    if row and "'poll'" not in sql:
        connection.execute("ALTER TABLE messages RENAME TO messages_legacy")
        connection.execute("DROP INDEX IF EXISTS idx_messages_chat_time")
        connection.execute("""CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            max_message_id TEXT NOT NULL UNIQUE,
            chat_id INTEGER NOT NULL,
            user_id INTEGER,
            user_name TEXT NOT NULL,
            text TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('text','voice','poll')),
            timestamp TEXT NOT NULL,
            reply_to TEXT
        )""")
        connection.execute("""INSERT OR IGNORE INTO messages(id,max_message_id,chat_id,user_id,user_name,text,source,timestamp,reply_to)
            SELECT id,max_message_id,chat_id,user_id,user_name,text,source,timestamp,reply_to FROM messages_legacy""")
        connection.execute("DROP TABLE messages_legacy")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id,timestamp)")


def _ensure_user_columns(connection: sqlite3.Connection) -> None:
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
        return
    cols = _table_columns(connection, 'users')
    if 'role' not in cols:
        connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if 'subscription_start' not in cols: connection.execute("ALTER TABLE users ADD COLUMN subscription_start TEXT")
    if 'subscription_end' not in cols: connection.execute("ALTER TABLE users ADD COLUMN subscription_end TEXT")
    if 'is_unlimited_subscription' not in cols: connection.execute("ALTER TABLE users ADD COLUMN is_unlimited_subscription INTEGER NOT NULL DEFAULT 0")


def _migrate_legacy_balances(connection: sqlite3.Connection) -> None:
    """Move old user+chat balances into one user-wide token pool.

    free_granted lets us preserve the old free allocation as trial tokens; any
    remainder is treated as purchased because its exact legacy origin cannot be
    recovered from the old schema. The old table is retained as *_legacy for
    auditability and is never used by runtime code.
    """
    old = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_chat_balances'").fetchone()
    if not old:
        return
    marker = connection.execute("SELECT 1 FROM token_grants LIMIT 1").fetchone()
    if marker:
        connection.execute("ALTER TABLE user_chat_balances RENAME TO user_chat_balances_legacy")
        return
    now = datetime.now(timezone.utc).isoformat()
    free_amount = 30
    rows = connection.execute("SELECT user_id,chat_id,tokens,free_granted FROM user_chat_balances ORDER BY user_id,chat_id").fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row['user_id'], {'trial': 0, 'purchased': []})
        tokens = max(0, int(row['tokens']))
        if row['free_granted']:
            item['trial'] += tokens
        else:
            item['purchased'].append((row['chat_id'], tokens))
    for user_id, item in grouped.items():
        trial = min(item['trial'], free_amount)
        if trial:
            connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id,trial,'trial',None,trial,f"migration:trial:{user_id}",now))
        remainder = max(0, item['trial'] - trial)
        if remainder:
            connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id,remainder,'purchased',None,remainder,f"migration:legacy-extra:{user_id}",now))
        for chat_id, tokens in item['purchased']:
            if tokens:
                connection.execute("INSERT INTO token_grants(user_id,amount,token_type,expires_at,remaining_amount,source_id,created_at) VALUES(?,?,?,?,?,?,?)",
                    (user_id,tokens,'purchased',None,tokens,f"migration:purchased:{user_id}:{chat_id}",now))
    connection.execute("ALTER TABLE user_chat_balances RENAME TO user_chat_balances_legacy")


def connect(db_path: str) -> sqlite3.Connection:
    parent = Path(db_path).parent
    if str(parent) not in ('', '.'):
        parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=30000')
    _migrate_messages(connection)
    # Existing installations need shape changes before SCHEMA creates indexes.
    _ensure_user_columns(connection)
    connection.executescript(SCHEMA)
    _ensure_user_columns(connection)
    # Migrate the older subscription table shape without dropping payment history.
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriptions'").fetchone():
        cols=_table_columns(connection,'subscriptions')
        if 'is_unlimited_subscription' not in cols: connection.execute("ALTER TABLE subscriptions ADD COLUMN is_unlimited_subscription INTEGER NOT NULL DEFAULT 0")
        if 'source' not in cols: connection.execute("ALTER TABLE subscriptions ADD COLUMN source TEXT NOT NULL DEFAULT 'paid'")
        if 'warning_sent_for' not in cols: connection.execute("ALTER TABLE subscriptions ADD COLUMN warning_sent_for TEXT")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_cooldowns'").fetchone():
        cols=_table_columns(connection,'chat_cooldowns')
        if 'in_flight' not in cols: connection.execute("ALTER TABLE chat_cooldowns ADD COLUMN in_flight INTEGER NOT NULL DEFAULT 0")
    connection.commit()
    _migrate_legacy_balances(connection)
    connection.commit()
    return connection
