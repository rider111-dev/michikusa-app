import sqlite3, os, datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "michikusa.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS farms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  company_code TEXT UNIQUE NOT NULL,
  phone TEXT, postal_code TEXT, address TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  user_id TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff',
  is_operator INTEGER NOT NULL DEFAULT 0,
  count_in_farm_rank INTEGER NOT NULL DEFAULT 1,
  UNIQUE(farm_id, user_id)
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  name TEXT NOT NULL, category TEXT,
  price INTEGER NOT NULL, volume_label TEXT, tax_rate REAL NOT NULL DEFAULT 0.08,
  box_l REAL NOT NULL, box_w REAL NOT NULL, box_h REAL NOT NULL,
  stock INTEGER NOT NULL DEFAULT 0,
  discontinued INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  name TEXT NOT NULL, phone TEXT, postal_code TEXT, address TEXT
);
CREATE TABLE IF NOT EXISTS carrier_settings (
  farm_id INTEGER PRIMARY KEY,
  mode TEXT NOT NULL DEFAULT 'auto',
  default_carrier TEXT NOT NULL DEFAULT 'yamato'
);
CREATE TABLE IF NOT EXISTS shipping_fees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  carrier TEXT NOT NULL, region TEXT NOT NULL, box_size TEXT NOT NULL,
  fee INTEGER NOT NULL,
  UNIQUE(farm_id, carrier, region, box_size)
);
CREATE TABLE IF NOT EXISTS payment_methods (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  default_mode TEXT NOT NULL DEFAULT 'ask',
  default_method_id INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  order_number TEXT NOT NULL,
  customer_id INTEGER,
  customer_name TEXT, customer_phone TEXT,
  payment_method TEXT,
  status TEXT NOT NULL DEFAULT 'unshipped',
  total_amount INTEGER NOT NULL DEFAULT 0,
  paid_out INTEGER NOT NULL DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS order_destinations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  postal_code TEXT, address TEXT, region TEXT,
  carrier TEXT, requested_date TEXT, requested_time_slot TEXT,
  shipping_fee INTEGER NOT NULL DEFAULT 0,
  tracking_number TEXT, shipped_at TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  destination_id INTEGER,
  product_id INTEGER NOT NULL,
  qty INTEGER NOT NULL,
  unit_price INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_progress (
  farm_id INTEGER PRIMARY KEY,
  profile INTEGER NOT NULL DEFAULT 0,
  products INTEGER NOT NULL DEFAULT 0,
  inventory INTEGER NOT NULL DEFAULT 0,
  shipping INTEGER NOT NULL DEFAULT 0,
  receipt INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS receipt_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  farm_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  show_tax_breakdown INTEGER NOT NULL DEFAULT 1,
  show_box_size INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  is_active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS feature_disclosure (
  farm_id INTEGER NOT NULL,
  feature_key TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (farm_id, feature_key)
);
CREATE TABLE IF NOT EXISTS quiz_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_key TEXT NOT NULL,
  level TEXT NOT NULL,
  question TEXT NOT NULL,
  choices TEXT NOT NULL,
  correct_index INTEGER NOT NULL,
  explanation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  question_id INTEGER NOT NULL,
  correct INTEGER NOT NULL,
  attempted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_points (
  user_id INTEGER PRIMARY KEY,
  points INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS operators (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS home_card_settings (
  farm_id INTEGER NOT NULL,
  card_key TEXT NOT NULL,
  visible INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (farm_id, card_key)
);
CREATE TABLE IF NOT EXISTS user_action_stats (
  user_pk INTEGER NOT NULL,
  action_key TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_pk, action_key)
);
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

def now():
    return datetime.datetime.now().isoformat(timespec="seconds")
