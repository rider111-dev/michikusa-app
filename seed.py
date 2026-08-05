import bcrypt
from db import get_conn, init_db, now

BOX_SIZES = ["コンパクト", "60", "80", "100", "120", "140", "160", "180", "200"]
CARRIERS = ["yamato", "sagawa", "yupack"]
CARRIER_LABEL = {"yamato": "ヤマト運輸", "sagawa": "佐川急便", "yupack": "日本郵便"}
REGIONS = ["北海道", "東北", "関東", "中部", "近畿", "中国", "四国", "九州", "沖縄"]

FEATURE_KEYS = {
    "order": "注文（かんたん販売）",
    "inventory_view": "在庫の確認",
    "customers_view": "顧客情報の確認",
    "payout_view": "入金状況の確認",
}
FEATURE_LEVEL = {"order": "beginner", "inventory_view": "beginner", "customers_view": "intermediate", "payout_view": "advanced"}

RANK_TIERS = [
    (0, "F", "見習い"), (30, "E", "駆け出し"), (70, "D", "一人前"), (120, "C", "中堅"),
    (180, "B", "熟練"), (250, "A", "師範代"), (330, "S", "達人"), (420, "SS", "大達人"),
]

QUIZ_BANK = [
    ("order", "beginner", "注文画面で配送先を「配送」にした場合、送料はどう決まりますか？",
     ["店主が毎回手入力する", "配送先の地域と商品の箱サイズから自動計算される", "常に一律料金になる"], 1,
     "送料は配送先の地域と、商品の合計から判定される箱サイズを送料表に当てはめて自動計算されます。"),
    ("order", "beginner", "セットアップが完了していないと注文画面はどうなりますか？",
     ["いつでも注文できる", "注文がブロックされ残りのセットアップ項目が案内される", "在庫だけ確認できる"], 1,
     "農園情報・商品・在庫・送料・領収書の5項目が完了するまで注文はブロックされます。"),
    ("inventory_view", "beginner", "在庫管理画面で「在庫わずか」の商品を素早く見つける方法は？",
     ["商品を1件ずつ開いて確認する", "在庫わずかのみ表示するチェックで絞り込む", "できない"], 1,
     "「在庫わずかのみ表示」のチェックボックスで絞り込めます。"),
    ("customers_view", "intermediate", "顧客情報画面での新規登録に必須なのはどれですか？",
     ["顧客名のみ必須、他は任意", "すべて必須", "電話番号のみ必須"], 0,
     "顧客名以外の電話番号・郵便番号・住所は任意項目です。"),
    ("payout_view", "advanced", "入金額はどのように計算されますか？",
     ["発送済み注文の合計金額そのまま", "発送済み注文の合計から手数料15%を控除した額", "全注文の合計金額"], 1,
     "入金予定額は、未清算の発送済み注文合計から手数料15%を控除して計算されます。"),
]

def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def seed():
    init_db()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM farms WHERE company_code = ?", ("C001",))
    row = cur.fetchone()
    if row:
        conn.close()
        return
    cur.execute(
        "INSERT INTO farms (name, company_code, phone, postal_code, address, created_at) VALUES (?,?,?,?,?,?)",
        ("たんじ農園", "C001", "090-0000-0000", "123-4567", "山梨県笛吹市桃source1-1", now()),
    )
    farm_id = cur.lastrowid
    cur.execute(
        "INSERT INTO users (farm_id, user_id, password_hash, role, is_operator) VALUES (?,?,?,?,0)",
        (farm_id, "tanji1234", hash_pw("tanji1234"), "admin"),
    )
    cur.execute("INSERT INTO carrier_settings (farm_id, mode, default_carrier) VALUES (?,?,?)", (farm_id, "auto", "yamato"))
    cur.execute("INSERT INTO setup_progress (farm_id) VALUES (?)", (farm_id,))

    products = [
        ("幸水（3kg箱）", "梨", 3200, "3kg / 8〜10玉", 0.08, 35, 30, 20, "120"),
        ("豊水（5kg箱）", "梨", 4800, "5kg / 12〜14玉", 0.08, 40, 35, 22, "140"),
        ("新高（訳あり2kg）", "梨", 1800, "2kg / 4〜5玉", 0.08, 28, 24, 15, "80"),
    ]
    box_dims = {"120": (35, 30, 20), "140": (40, 35, 22), "80": (28, 24, 15)}
    for name, cat, price, vol, tax, l, w, h, box in products:
        cur.execute(
            "INSERT INTO products (farm_id, name, category, price, volume_label, tax_rate, box_l, box_w, box_h, stock, discontinued) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
            (farm_id, name, cat, price, vol, tax, l, w, h, 50),
        )

    base_fee = {"コンパクト": 700, "60": 800, "80": 1000, "100": 1200, "120": 1400, "140": 1600, "160": 1800, "180": 2000, "200": 2200}
    for carrier in CARRIERS:
        sizes = BOX_SIZES if carrier == "yamato" else [s for s in BOX_SIZES if s != "コンパクト"]
        for region in REGIONS:
            region_mult = 1.0 if region == "関東" else 1.15
            for size in sizes:
                fee = int(base_fee[size] * region_mult)
                cur.execute(
                    "INSERT OR IGNORE INTO shipping_fees (farm_id, carrier, region, box_size, fee) VALUES (?,?,?,?,?)",
                    (farm_id, carrier, region, size, fee),
                )

    for name, mode in [("現金", "default"), ("銀行振込", "ask")]:
        cur.execute("INSERT INTO payment_methods (farm_id, name, default_mode) VALUES (?,?,?)", (farm_id, name, mode))

    cur.execute(
        "INSERT INTO receipt_templates (farm_id, name, show_tax_breakdown, show_box_size, note, is_active) VALUES (?,?,?,?,?,0)",
        (farm_id, "標準テンプレート", 1, 0, ""),
    )

    for key in FEATURE_KEYS:
        cur.execute("INSERT OR IGNORE INTO feature_disclosure (farm_id, feature_key, enabled) VALUES (?,?,0)", (farm_id, key))

    for key, order in [("today_summary", 0), ("unshipped_count", 1), ("low_stock", 2)]:
        cur.execute("INSERT OR IGNORE INTO home_card_settings (farm_id, card_key, visible, sort_order) VALUES (?,?,1,?)", (farm_id, key, order))

    if cur.execute("SELECT COUNT(*) c FROM quiz_questions").fetchone()["c"] == 0:
        for feature_key, level, question, choices, correct_index, explanation in QUIZ_BANK:
            cur.execute(
                "INSERT INTO quiz_questions (feature_key, level, question, choices, correct_index, explanation) VALUES (?,?,?,?,?,?)",
                (feature_key, level, question, "|".join(choices), correct_index, explanation),
            )

    cur.execute("SELECT 1 FROM operators WHERE user_id=?", ("operator1",))
    if not cur.fetchone():
        cur.execute("INSERT INTO operators (user_id, password_hash) VALUES (?,?)", ("operator1", hash_pw("operator1")))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    seed()
    print("seed complete")
