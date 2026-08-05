import streamlit as st
import pandas as pd
import bcrypt, datetime, statistics
from db import get_conn, init_db, now
from seed import seed, BOX_SIZES, CARRIERS, CARRIER_LABEL, REGIONS, FEATURE_KEYS, FEATURE_LEVEL, RANK_TIERS

st.set_page_config(page_title="みちくさ", page_icon="🍐", layout="centered")
init_db()
seed()

def check_pw(pw, pw_hash):
    try:
        return bcrypt.checkpw(pw.encode(), pw_hash.encode())
    except Exception:
        return False

def hash_pw_local(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def box_size_for_total(products_qty):
    total_vol = 0.0
    for p, qty in products_qty:
        total_vol += p["box_l"] * p["box_w"] * p["box_h"] * qty
    thresholds = [
        ("コンパクト", 8000), ("60", 15000), ("80", 30000), ("100", 55000),
        ("120", 90000), ("140", 135000), ("160", 190000), ("180", 260000), ("200", 10**9),
    ]
    for size, cap in thresholds:
        if total_vol <= cap:
            return size
    return "200"

def get_shipping_fee(conn, farm_id, carrier, region, box_size):
    row = conn.execute(
        "SELECT fee FROM shipping_fees WHERE farm_id=? AND carrier=? AND region=? AND box_size=?",
        (farm_id, carrier, region, box_size),
    ).fetchone()
    return row["fee"] if row else None

def setup_status(conn, farm_id):
    row = conn.execute("SELECT * FROM setup_progress WHERE farm_id=?", (farm_id,)).fetchone()
    if not row:
        return {"profile": False, "products": False, "inventory": False, "shipping": False, "receipt": False}
    return {k: bool(row[k]) for k in ["profile", "products", "inventory", "shipping", "receipt"]}

def refresh_setup_progress(conn, farm_id):
    farm = conn.execute("SELECT * FROM farms WHERE id=?", (farm_id,)).fetchone()
    profile_ok = bool(farm["phone"] and farm["address"])
    products_ok = conn.execute("SELECT COUNT(*) c FROM products WHERE farm_id=? AND discontinued=0", (farm_id,)).fetchone()["c"] > 0
    inventory_ok = conn.execute("SELECT COUNT(*) c FROM products WHERE farm_id=? AND stock>0", (farm_id,)).fetchone()["c"] > 0
    shipping_ok = conn.execute("SELECT COUNT(*) c FROM shipping_fees WHERE farm_id=?", (farm_id,)).fetchone()["c"] > 0
    receipt_ok = conn.execute("SELECT COUNT(*) c FROM receipt_templates WHERE farm_id=? AND is_active=1", (farm_id,)).fetchone()["c"] > 0
    conn.execute(
        "INSERT INTO setup_progress (farm_id, profile, products, inventory, shipping, receipt) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(farm_id) DO UPDATE SET profile=?, products=?, inventory=?, shipping=?, receipt=?",
        (farm_id, profile_ok, products_ok, inventory_ok, shipping_ok, receipt_ok,
         profile_ok, products_ok, inventory_ok, shipping_ok, receipt_ok),
    )
    conn.commit()

# ---------- ランク/ポイント ----------
ACTION_POINT_CAP = 20  # 1アクション種別あたりの獲得上限（周回による過剰加点を防ぐ）
QUIZ_POINT_CAP = 10    # 1問あたりの獲得上限
QUIZ_FIRST_BONUS = 5   # 初めて正解した時のボーナス

def award_action(conn, user_pk, action_key):
    conn.execute(
        "INSERT INTO user_action_stats (user_pk, action_key, count) VALUES (?,?,1) "
        "ON CONFLICT(user_pk, action_key) DO UPDATE SET count = count + 1",
        (user_pk, action_key),
    )
    conn.commit()

def action_points(conn, user_pk):
    rows = conn.execute("SELECT count FROM user_action_stats WHERE user_pk=?", (user_pk,)).fetchall()
    return sum(min(r["count"] * 2, ACTION_POINT_CAP) for r in rows)

def quiz_points(conn, user_pk):
    rows = conn.execute(
        "SELECT question_id, SUM(correct) c FROM quiz_attempts WHERE user_id=? GROUP BY question_id", (user_pk,)
    ).fetchall()
    total = 0
    for r in rows:
        c = r["c"] or 0
        if c > 0:
            total += min(QUIZ_POINT_CAP, QUIZ_FIRST_BONUS + (c - 1))
    return total

def setup_points(conn, farm_id):
    progress = setup_status(conn, farm_id)
    return sum(4 for v in progress.values() if v)

def total_points(conn, user_pk, farm_id):
    return action_points(conn, user_pk) + quiz_points(conn, user_pk) + setup_points(conn, farm_id)

def rank_for_points(points):
    tier = RANK_TIERS[0]
    for t in RANK_TIERS:
        if points >= t[0]:
            tier = t
    return tier  # (threshold, code, name)

def next_tier_progress(points):
    for i, t in enumerate(RANK_TIERS):
        if points < t[0]:
            prev = RANK_TIERS[i - 1][0]
            return (points - prev) / max(1, t[0] - prev), t
    return 1.0, RANK_TIERS[-1]

def farm_rank(conn, farm_id):
    users = conn.execute("SELECT * FROM users WHERE farm_id=? AND count_in_farm_rank=1", (farm_id,)).fetchall()
    if not users:
        return rank_for_points(0), 0.0
    tier_indices = []
    for u in users:
        pts = total_points(conn, u["id"], farm_id)
        tier = rank_for_points(pts)
        tier_indices.append(RANK_TIERS.index(tier))
    median_idx = int(statistics.median(tier_indices))
    tier = RANK_TIERS[median_idx]
    avg_ratio = sum(t / (len(RANK_TIERS) - 1) for t in tier_indices) / len(tier_indices)
    return tier, avg_ratio

# ---------- ログイン ----------
def login_screen():
    st.markdown("## 🍐 みちくさ")
    st.caption("農園向け受発注管理サービス")
    is_operator = st.checkbox("運営者としてログイン")
    if is_operator:
        with st.form("op_login"):
            op_id = st.text_input("運営者ID")
            op_pw = st.text_input("パスワード", type="password")
            if st.form_submit_button("ログイン", use_container_width=True):
                conn = get_conn()
                op = conn.execute("SELECT * FROM operators WHERE user_id=?", (op_id,)).fetchone()
                conn.close()
                if op and check_pw(op_pw, op["password_hash"]):
                    st.session_state.is_operator = True
                    st.session_state.user_id = op["user_id"]
                    st.rerun()
                else:
                    st.error("運営者IDまたはパスワードが違います")
        st.info("デモ用運営者ログイン: `operator1` / `operator1`")
        return
    with st.form("login"):
        company_code = st.text_input("農園ID")
        user_id = st.text_input("ユーザーID")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン", use_container_width=True)
    if submitted:
        conn = get_conn()
        farm = conn.execute("SELECT * FROM farms WHERE company_code=?", (company_code,)).fetchone()
        if not farm:
            st.error("農園IDが見つかりません")
            conn.close()
            return
        user = conn.execute("SELECT * FROM users WHERE farm_id=? AND user_id=?", (farm["id"], user_id)).fetchone()
        conn.close()
        if not user or not check_pw(password, user["password_hash"]):
            st.error("ユーザーIDまたはパスワードが違います")
            return
        st.session_state.farm_id = farm["id"]
        st.session_state.farm_name = farm["name"]
        st.session_state.user_id = user["user_id"]
        st.session_state.user_pk = user["id"]
        st.session_state.role = user["role"]
        st.rerun()
    st.info("デモ用ログイン: 農園ID `C001` / ユーザーID `tanji1234` / パスワード `tanji1234`")

# ---------- ホーム ----------
HOME_CARD_LABELS = {"today_summary": "本日の売上", "unshipped_count": "発送待ち件数", "low_stock": "在庫わずか"}

def home_screen(conn, farm_id):
    st.markdown(f"### ようこそ、{st.session_state.farm_name} さん")
    progress = setup_status(conn, farm_id)
    done = sum(progress.values())
    if done < 5:
        st.warning(f"セットアップ進行中：{done}/5 完了。すべて完了すると注文機能が使えます。")
    else:
        st.success("セットアップ完了！注文機能が利用できます。")

    cards = conn.execute(
        "SELECT * FROM home_card_settings WHERE farm_id=? AND visible=1 ORDER BY sort_order", (farm_id,)
    ).fetchall()
    if not cards:
        st.info("表示するカードがありません。「ホーム表示設定」から表示を選んでください。")
    else:
        today = datetime.date.today().isoformat()
        values = {}
        if any(c["card_key"] == "today_summary" for c in cards):
            values["today_summary"] = conn.execute(
                "SELECT COALESCE(SUM(total_amount),0) s FROM orders WHERE farm_id=? AND date(created_at)=?", (farm_id, today)
            ).fetchone()["s"]
        if any(c["card_key"] == "unshipped_count" for c in cards):
            values["unshipped_count"] = conn.execute(
                "SELECT COUNT(*) c FROM orders WHERE farm_id=? AND status='unshipped'", (farm_id,)
            ).fetchone()["c"]
        if any(c["card_key"] == "low_stock" for c in cards):
            values["low_stock"] = conn.execute(
                "SELECT COUNT(*) c FROM products WHERE farm_id=? AND stock<=5 AND discontinued=0", (farm_id,)
            ).fetchone()["c"]
        cols = st.columns(len(cards))
        for col, c in zip(cols, cards):
            key = c["card_key"]
            v = values.get(key, 0)
            col.metric(HOME_CARD_LABELS.get(key, key), f"¥{v:,}" if key == "today_summary" else v)

    st.divider()
    pts = total_points(conn, st.session_state.user_pk, farm_id)
    tier = rank_for_points(pts)
    st.caption(f"あなたのランク: {tier[1]}（{tier[2]}） / {pts}pt — ランクアップ道場・活用ガイドは左メニューから")

def home_customize_screen(conn, farm_id):
    st.markdown("### ホーム表示設定")
    st.caption("表示・非表示と並び順を設定できます")
    rows = conn.execute("SELECT * FROM home_card_settings WHERE farm_id=? ORDER BY sort_order", (farm_id,)).fetchall()
    for i, c in enumerate(rows):
        cols = st.columns([3, 1, 1, 1])
        cols[0].write(HOME_CARD_LABELS.get(c["card_key"], c["card_key"]))
        visible = cols[1].checkbox("表示", value=bool(c["visible"]), key=f"vis_{c['card_key']}")
        if visible != bool(c["visible"]):
            conn.execute("UPDATE home_card_settings SET visible=? WHERE farm_id=? AND card_key=?", (visible, farm_id, c["card_key"]))
            conn.commit()
            st.rerun()
        if cols[2].button("↑", key=f"up_{c['card_key']}", disabled=i == 0):
            other = rows[i - 1]
            conn.execute("UPDATE home_card_settings SET sort_order=? WHERE farm_id=? AND card_key=?", (other["sort_order"], farm_id, c["card_key"]))
            conn.execute("UPDATE home_card_settings SET sort_order=? WHERE farm_id=? AND card_key=?", (c["sort_order"], farm_id, other["card_key"]))
            conn.commit()
            st.rerun()
        if cols[3].button("↓", key=f"down_{c['card_key']}", disabled=i == len(rows) - 1):
            other = rows[i + 1]
            conn.execute("UPDATE home_card_settings SET sort_order=? WHERE farm_id=? AND card_key=?", (other["sort_order"], farm_id, c["card_key"]))
            conn.execute("UPDATE home_card_settings SET sort_order=? WHERE farm_id=? AND card_key=?", (c["sort_order"], farm_id, other["card_key"]))
            conn.commit()
            st.rerun()

# ---------- 商品管理 ----------
def products_screen(conn, farm_id):
    st.markdown("### 商品管理")
    with st.expander("新規商品を登録", expanded=False):
        with st.form("new_product", clear_on_submit=True):
            name = st.text_input("商品名")
            category = st.text_input("カテゴリ", value="梨")
            price = st.number_input("価格（税抜）", min_value=0, step=100)
            volume = st.text_input("数量表記（例: 3kg / 8〜10玉）")
            tax = st.selectbox("税率", [0.08, 0.10], format_func=lambda x: f"{int(x*100)}%")
            st.caption("発送時の箱サイズ（登録必須）")
            bc1, bc2, bc3 = st.columns(3)
            box_l = bc1.number_input("縦(cm)", min_value=1.0, step=1.0)
            box_w = bc2.number_input("横(cm)", min_value=1.0, step=1.0)
            box_h = bc3.number_input("高さ(cm)", min_value=1.0, step=1.0)
            stock = st.number_input("初期在庫数", min_value=0, step=1)
            if st.form_submit_button("登録"):
                if not name or box_l <= 0 or box_w <= 0 or box_h <= 0:
                    st.error("商品名・箱サイズ（縦横高さ）は必須です")
                else:
                    conn.execute(
                        "INSERT INTO products (farm_id, name, category, price, volume_label, tax_rate, box_l, box_w, box_h, stock, discontinued) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                        (farm_id, name, category, price, volume, tax, box_l, box_w, box_h, stock),
                    )
                    conn.commit()
                    award_action(conn, st.session_state.user_pk, "register_product")
                    st.success("登録しました")
                    st.rerun()

    rows = conn.execute("SELECT * FROM products WHERE farm_id=? ORDER BY discontinued, name", (farm_id,)).fetchall()
    for p in rows:
        with st.container(border=True):
            cols = st.columns([3, 1, 1])
            status = "（取扱休止）" if p["discontinued"] else ""
            cols[0].markdown(f"**{p['name']}**{status}  \n¥{p['price']:,}（税{int(p['tax_rate']*100)}%） / {p['volume_label'] or '-'}")
            cols[0].caption(f"箱: {p['box_l']}×{p['box_w']}×{p['box_h']} cm / 在庫 {p['stock']}")
            if cols[1].button("取扱休止" if not p["discontinued"] else "再開", key=f"disc_{p['id']}"):
                conn.execute("UPDATE products SET discontinued=? WHERE id=?", (0 if p["discontinued"] else 1, p["id"]))
                conn.commit()
                st.rerun()
            if cols[2].button("削除", key=f"del_{p['id']}"):
                st.session_state[f"confirm_del_{p['id']}"] = True
            if st.session_state.get(f"confirm_del_{p['id']}"):
                if st.button(f"本当に削除する（{p['name']}）", key=f"confirm2_{p['id']}", type="primary"):
                    conn.execute("DELETE FROM products WHERE id=?", (p["id"],))
                    conn.commit()
                    st.rerun()

# ---------- 在庫管理 ----------
def inventory_screen(conn, farm_id):
    st.markdown("### 在庫管理")
    only_low = st.checkbox("在庫わずか（5以下）のみ表示")
    query = "SELECT * FROM products WHERE farm_id=? AND discontinued=0"
    if only_low:
        query += " AND stock<=5"
    query += " ORDER BY stock ASC"
    rows = conn.execute(query, (farm_id,)).fetchall()
    for p in rows:
        cols = st.columns([3, 2])
        cols[0].markdown(f"**{p['name']}**")
        new_stock = cols[1].number_input("在庫数", min_value=0, value=p["stock"], key=f"stock_{p['id']}")
        if new_stock != p["stock"]:
            conn.execute("UPDATE products SET stock=? WHERE id=?", (new_stock, p["id"]))
            conn.commit()
            award_action(conn, st.session_state.user_pk, "update_inventory")
            st.rerun()

# ---------- 送料設定 ----------
def shipping_settings_screen(conn, farm_id):
    st.markdown("### 送料設定")
    cs = conn.execute("SELECT * FROM carrier_settings WHERE farm_id=?", (farm_id,)).fetchone()
    mode = st.radio("配送業者の指定方法", ["auto", "manual"], index=0 if cs["mode"] == "auto" else 1,
                     format_func=lambda x: "自動セット" if x == "auto" else "注文時に毎回指定")
    default_carrier = st.selectbox("自動セット時のデフォルト業者", CARRIERS, index=CARRIERS.index(cs["default_carrier"]),
                                    format_func=lambda x: CARRIER_LABEL[x])
    if st.button("保存"):
        conn.execute("UPDATE carrier_settings SET mode=?, default_carrier=? WHERE farm_id=?", (mode, default_carrier, farm_id))
        conn.commit()
        st.success("保存しました")

    st.divider()
    st.caption("運送会社ごとの地域×箱サイズ送料表")
    carrier = st.selectbox("運送会社を選択", CARRIERS, format_func=lambda x: CARRIER_LABEL[x])
    sizes = BOX_SIZES if carrier == "yamato" else [s for s in BOX_SIZES if s != "コンパクト"]
    fees = conn.execute("SELECT * FROM shipping_fees WHERE farm_id=? AND carrier=?", (farm_id, carrier)).fetchall()
    fee_map = {(f["region"], f["box_size"]): f["fee"] for f in fees}
    df = pd.DataFrame([[fee_map.get((r, s), 0) for s in sizes] for r in REGIONS], index=REGIONS, columns=sizes)
    edited = st.data_editor(df, use_container_width=True)
    if st.button("送料表を保存", key=f"save_fees_{carrier}"):
        for region in REGIONS:
            for size in sizes:
                conn.execute(
                    "INSERT INTO shipping_fees (farm_id, carrier, region, box_size, fee) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(farm_id, carrier, region, box_size) DO UPDATE SET fee=?",
                    (farm_id, carrier, region, size, int(edited.loc[region, size]), int(edited.loc[region, size])),
                )
        conn.commit()
        st.success("送料表を保存しました")

# ---------- 注文（かんたん販売・複数配送先対応） ----------
def order_screen(conn, farm_id):
    st.markdown("### 注文（かんたん販売）")
    progress = setup_status(conn, farm_id)
    if not all(progress.values()):
        missing = [k for k, v in progress.items() if not v]
        st.error("セットアップが完了していないため注文できません。管理画面から以下を完了してください：\n\n" + "\n".join(f"- {m}" for m in missing))
        return

    products = conn.execute("SELECT * FROM products WHERE farm_id=? AND discontinued=0 ORDER BY name", (farm_id,)).fetchall()
    if not products:
        st.info("販売可能な商品がありません。商品管理から登録してください。")
        return

    if "order_dest_count" not in st.session_state:
        st.session_state.order_dest_count = 1

    customer_name = st.text_input("お客様名")
    cs = conn.execute("SELECT * FROM carrier_settings WHERE farm_id=?", (farm_id,)).fetchone()

    c1, c2 = st.columns(2)
    if c1.button("配送先を追加"):
        st.session_state.order_dest_count += 1
    if c2.button("配送先を1件減らす") and st.session_state.order_dest_count > 1:
        st.session_state.order_dest_count -= 1

    destinations = []
    for i in range(st.session_state.order_dest_count):
        with st.container(border=True):
            st.markdown(f"**配送先 {i+1}**")
            region = st.selectbox("地域", REGIONS, key=f"region_{i}")
            time_slot = st.selectbox("お届け希望時間帯", ["指定なし", "午前中", "14〜16時", "16〜18時", "18〜20時", "19〜21時"], key=f"slot_{i}")
            requested_date = st.date_input("お届け希望日（任意）", value=None, key=f"date_{i}")
            if cs["mode"] == "auto":
                carrier = cs["default_carrier"]
                if st.checkbox(f"配送業者を個別に変更する（自動セット: {CARRIER_LABEL[carrier]}）", key=f"ovr_{i}"):
                    carrier = st.selectbox("配送業者", CARRIERS, format_func=lambda x: CARRIER_LABEL[x], key=f"carrier_{i}")
            else:
                carrier = st.selectbox("配送業者", CARRIERS, format_func=lambda x: CARRIER_LABEL[x], key=f"carrier_{i}")
            st.caption("この配送先に送る商品を選択")
            selections = []
            for p in products:
                qty = st.number_input(f"{p['name']}（¥{p['price']:,} / 在庫{p['stock']}）", min_value=0, step=1, key=f"qty_{i}_{p['id']}")
                if qty > 0:
                    selections.append((p, qty))
            destinations.append({"region": region, "time_slot": time_slot, "date": requested_date, "carrier": carrier, "items": selections})

    stock_used = {}
    for d in destinations:
        for p, qty in d["items"]:
            stock_used[p["id"]] = stock_used.get(p["id"], 0) + qty
    over_stock = [p for p in products if stock_used.get(p["id"], 0) > p["stock"]]
    if over_stock:
        st.error("在庫を超えて選択されています: " + "、".join(p["name"] for p in over_stock))
        return

    valid_dests = [d for d in destinations if d["items"]]
    if not valid_dests:
        st.info("各配送先で1点以上の商品を選択してください")
        return

    st.divider()
    grand_total = 0
    dest_calcs = []
    for i, d in enumerate(valid_dests):
        box_size = box_size_for_total(d["items"])
        fee = get_shipping_fee(conn, farm_id, d["carrier"], d["region"], box_size)
        subtotal = sum(p["price"] * qty for p, qty in d["items"])
        st.write(f"配送先{i+1}: 箱サイズ {box_size} / 送料 " + (f"¥{fee:,}" if fee is not None else "未設定") + f" / 商品合計 ¥{subtotal:,}")
        if fee is None:
            st.error("送料が未設定の組み合わせがあります。送料設定で登録してください。")
            return
        grand_total += subtotal + fee
        dest_calcs.append({**d, "box_size": box_size, "fee": fee, "subtotal": subtotal})

    st.markdown(f"### 合計金額: ¥{grand_total:,}")
    payment_methods = conn.execute("SELECT * FROM payment_methods WHERE farm_id=?", (farm_id,)).fetchall()
    pm_names = [pm["name"] for pm in payment_methods] or ["現金"]
    payment = st.selectbox("お支払い方法", pm_names)

    if st.button("注文を確定する", type="primary"):
        order_number = "ORD" + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        conn.execute(
            "INSERT INTO orders (farm_id, order_number, customer_name, payment_method, status, total_amount, created_at) VALUES (?,?,?,?,?,?,?)",
            (farm_id, order_number, customer_name, payment, "unshipped", grand_total, now()),
        )
        order_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        for d in dest_calcs:
            conn.execute(
                "INSERT INTO order_destinations (order_id, region, carrier, requested_date, requested_time_slot, shipping_fee) VALUES (?,?,?,?,?,?)",
                (order_id, d["region"], d["carrier"], str(d["date"]) if d["date"] else None, d["time_slot"], d["fee"]),
            )
            dest_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            for p, qty in d["items"]:
                conn.execute(
                    "INSERT INTO order_items (order_id, destination_id, product_id, qty, unit_price) VALUES (?,?,?,?,?)",
                    (order_id, dest_id, p["id"], qty, p["price"]),
                )
                conn.execute("UPDATE products SET stock = stock - ? WHERE id=?", (qty, p["id"]))
        conn.commit()
        award_action(conn, st.session_state.user_pk, "place_order")
        st.session_state.order_dest_count = 1
        st.success(f"注文を確定しました（注文番号: {order_number}）")
        st.rerun()

# ---------- 発送管理 ----------
def shipping_admin_screen(conn, farm_id):
    st.markdown("### 発送管理")
    rows = conn.execute(
        "SELECT o.id, o.order_number, o.customer_name, o.total_amount, d.id dest_id, d.region, d.carrier, d.requested_date, d.requested_time_slot, d.tracking_number "
        "FROM orders o JOIN order_destinations d ON d.order_id=o.id WHERE o.farm_id=? AND o.status='unshipped' ORDER BY o.created_at",
        (farm_id,),
    ).fetchall()
    if not rows:
        st.info("発送待ちの注文はありません")
        return
    for r in rows:
        with st.container(border=True):
            st.markdown(f"**{r['order_number']}** / {r['customer_name'] or '-'} / {CARRIER_LABEL.get(r['carrier'], r['carrier'])} / {r['region']}")
            st.caption(f"希望日: {r['requested_date'] or '指定なし'} {r['requested_time_slot'] or ''}")
            tracking = st.text_input("追跡番号", key=f"track_{r['dest_id']}")
            carrier_sel = st.selectbox("配送業者", CARRIERS, index=CARRIERS.index(r["carrier"]) if r["carrier"] in CARRIERS else 0,
                                        format_func=lambda x: CARRIER_LABEL[x], key=f"carrier_{r['dest_id']}")
            if st.button("発送済みにする", key=f"ship_{r['dest_id']}"):
                if not tracking:
                    st.error("追跡番号と配送業者は両方必須です")
                else:
                    conn.execute("UPDATE order_destinations SET tracking_number=?, carrier=?, shipped_at=? WHERE id=?",
                                 (tracking, carrier_sel, now(), r["dest_id"]))
                    conn.execute("UPDATE orders SET status='shipped' WHERE id=?", (r["id"],))
                    conn.commit()
                    award_action(conn, st.session_state.user_pk, "mark_shipped")
                    st.success("発送済みにしました")
                    st.rerun()

# ---------- 顧客情報 ----------
def customers_screen(conn, farm_id):
    st.markdown("### 顧客情報")
    with st.expander("新規顧客を登録"):
        with st.form("new_customer", clear_on_submit=True):
            name = st.text_input("顧客名")
            phone = st.text_input("電話番号")
            postal = st.text_input("郵便番号")
            address = st.text_input("住所")
            if st.form_submit_button("登録") and name:
                conn.execute("INSERT INTO customers (farm_id, name, phone, postal_code, address) VALUES (?,?,?,?,?)",
                             (farm_id, name, phone, postal, address))
                conn.commit()
                st.success("登録しました")
                st.rerun()
    rows = conn.execute("SELECT * FROM customers WHERE farm_id=? ORDER BY name", (farm_id,)).fetchall()
    if rows:
        st.dataframe(pd.DataFrame([dict(r) for r in rows]).drop(columns=["farm_id"]), use_container_width=True)
    else:
        st.info("登録済みの顧客がいません")

# ---------- 売上明細 ----------
def sales_detail_screen(conn, farm_id):
    st.markdown("### 売上明細")
    c1, c2, c3 = st.columns(3)
    date_from = c1.date_input("期間（開始）", value=None, key="sd_from")
    date_to = c2.date_input("期間（終了）", value=None, key="sd_to")
    kw = c3.text_input("顧客名・注文番号で検索")
    query = "SELECT * FROM orders WHERE farm_id=?"
    params = [farm_id]
    if date_from:
        query += " AND date(created_at) >= ?"
        params.append(str(date_from))
    if date_to:
        query += " AND date(created_at) <= ?"
        params.append(str(date_to))
    if kw:
        query += " AND (customer_name LIKE ? OR order_number LIKE ?)"
        params += [f"%{kw}%", f"%{kw}%"]
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    if not rows:
        st.info("該当する注文がありません")
        return
    for o in rows:
        with st.container(border=True):
            st.markdown(f"**{o['order_number']}** / {o['customer_name'] or '-'} / ¥{o['total_amount']:,} / {'発送済' if o['status']=='shipped' else '発送待ち'}")
            items = conn.execute(
                "SELECT p.name, oi.qty, oi.unit_price FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?",
                (o["id"],),
            ).fetchall()
            for it in items:
                st.caption(f"{it['name']} × {it['qty']}（¥{it['unit_price']:,}）")

# ---------- 売上分析 ----------
def sales_analysis_screen(conn, farm_id):
    st.markdown("### 売上分析")
    orders = conn.execute("SELECT * FROM orders WHERE farm_id=?", (farm_id,)).fetchall()
    if not orders:
        st.info("分析可能な注文データがありません")
        return
    df = pd.DataFrame([dict(o) for o in orders])
    total_orders = len(df)
    cancel_rate = 0.0  # キャンセル状態は未実装のため0固定
    st.metric("客単価", f"¥{df['total_amount'].mean():,.0f}")
    st.metric("注文件数", total_orders)
    st.metric("キャンセル率", f"{cancel_rate:.1f}%")

    by_name = df.groupby("customer_name").size().sort_values(ascending=False)
    repeaters = (by_name >= 2).sum()
    new_customers = (by_name == 1).sum()
    st.write(f"リピーター: {repeaters}名 / 新規: {new_customers}名")

    st.caption("売れ筋ランキング")
    items = conn.execute(
        "SELECT p.name, SUM(oi.qty) qty, SUM(oi.qty*oi.unit_price) sales FROM order_items oi "
        "JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.farm_id=? "
        "GROUP BY p.name ORDER BY sales DESC", (farm_id,),
    ).fetchall()
    if items:
        st.dataframe(pd.DataFrame([dict(r) for r in items]), use_container_width=True)

    st.caption("地域別売上")
    region_rows = conn.execute(
        "SELECT d.region, SUM(o.total_amount) sales FROM order_destinations d JOIN orders o ON o.id=d.order_id "
        "WHERE o.farm_id=? GROUP BY d.region ORDER BY sales DESC", (farm_id,),
    ).fetchall()
    if region_rows:
        st.dataframe(pd.DataFrame([dict(r) for r in region_rows]), use_container_width=True)

# ---------- 入金 ----------
def payout_screen(conn, farm_id):
    st.markdown("### 入金")
    rows = conn.execute("SELECT * FROM orders WHERE farm_id=? AND status='shipped' AND paid_out=0", (farm_id,)).fetchall()
    total = sum(r["total_amount"] for r in rows)
    fee = int(total * 0.15)
    net = total - fee
    st.write(f"未清算の発送済み注文: {len(rows)}件")
    st.write(f"合計金額: ¥{total:,} / 手数料15%控除後の入金予定額: ¥{net:,}")
    if rows and st.button("この期間を入金済みにする", type="primary"):
        for r in rows:
            conn.execute("UPDATE orders SET paid_out=1 WHERE id=?", (r["id"],))
        conn.commit()
        st.success("入金済みにしました")
        st.rerun()
    st.divider()
    st.caption("入金済みの注文（履歴）")
    paid = conn.execute("SELECT * FROM orders WHERE farm_id=? AND paid_out=1 ORDER BY created_at DESC", (farm_id,)).fetchall()
    if paid:
        st.dataframe(pd.DataFrame([dict(r) for r in paid])[["order_number", "total_amount", "created_at"]], use_container_width=True)

# ---------- ユーザー管理 ----------
def users_screen(conn, farm_id):
    st.markdown("### ユーザー管理")
    with st.form("new_user", clear_on_submit=True):
        uid = st.text_input("ユーザーID")
        pw = st.text_input("パスワード", type="password")
        role = st.selectbox("権限", ["staff", "admin"], format_func=lambda x: "一般" if x == "staff" else "管理者")
        if st.form_submit_button("追加") and uid and pw:
            exists = conn.execute("SELECT 1 FROM users WHERE farm_id=? AND user_id=?", (farm_id, uid)).fetchone()
            if exists:
                st.error("そのユーザーIDは既に使われています")
            else:
                conn.execute("INSERT INTO users (farm_id, user_id, password_hash, role) VALUES (?,?,?,?)",
                             (farm_id, uid, hash_pw_local(pw), role))
                conn.commit()
                st.success("追加しました")
                st.rerun()
    rows = conn.execute("SELECT * FROM users WHERE farm_id=?", (farm_id,)).fetchall()
    for u in rows:
        cols = st.columns([3, 2, 1])
        cols[0].write(f"{u['user_id']}（{'管理者' if u['role']=='admin' else '一般'}）")
        count_in = cols[1].checkbox("農園全体ランクに含める", value=bool(u["count_in_farm_rank"]), key=f"cnt_{u['id']}")
        if count_in != bool(u["count_in_farm_rank"]):
            conn.execute("UPDATE users SET count_in_farm_rank=? WHERE id=?", (count_in, u["id"]))
            conn.commit()
            st.rerun()
        if u["user_id"] != st.session_state.user_id:
            if cols[2].button("削除", key=f"deluser_{u['id']}"):
                st.session_state[f"confirm_deluser_{u['id']}"] = True
            if st.session_state.get(f"confirm_deluser_{u['id']}"):
                if st.button(f"本当に削除する（{u['user_id']}）", key=f"confirmu2_{u['id']}"):
                    conn.execute("DELETE FROM users WHERE id=?", (u["id"],))
                    conn.commit()
                    st.rerun()

    st.divider()
    st.markdown("#### 一般権限への機能開示設定")
    st.caption("チェックした機能は一般ユーザーに開示され、ランクアップ道場の出題範囲にも含まれます")
    for key, label in FEATURE_KEYS.items():
        row = conn.execute("SELECT * FROM feature_disclosure WHERE farm_id=? AND feature_key=?", (farm_id, key)).fetchone()
        enabled = st.checkbox(label, value=bool(row["enabled"]) if row else False, key=f"fd_{key}")
        if row and enabled != bool(row["enabled"]):
            conn.execute("UPDATE feature_disclosure SET enabled=? WHERE farm_id=? AND feature_key=?", (enabled, farm_id, key))
            conn.commit()
            st.rerun()

# ---------- 支払方法 ----------
def payment_methods_screen(conn, farm_id):
    st.markdown("### 支払方法")
    with st.form("new_pm", clear_on_submit=True):
        name = st.text_input("支払方法名")
        if st.form_submit_button("追加") and name:
            conn.execute("INSERT INTO payment_methods (farm_id, name) VALUES (?,?)", (farm_id, name))
            conn.commit()
            st.rerun()
    rows = conn.execute("SELECT * FROM payment_methods WHERE farm_id=?", (farm_id,)).fetchall()
    for pm in rows:
        cols = st.columns([3, 1])
        cols[0].write(pm["name"])
        if cols[1].button("削除", key=f"delpm_{pm['id']}"):
            conn.execute("DELETE FROM payment_methods WHERE id=?", (pm["id"],))
            conn.commit()
            st.rerun()

# ---------- 領収書設定 ----------
def receipt_settings_screen(conn, farm_id):
    st.markdown("### 領収書設定")
    with st.expander("新規テンプレートを追加"):
        with st.form("new_template", clear_on_submit=True):
            name = st.text_input("テンプレート名")
            show_tax = st.checkbox("税額の内訳を表示", value=True)
            show_box = st.checkbox("箱サイズを表示", value=False)
            note = st.text_area("備考欄（任意）")
            if st.form_submit_button("追加") and name:
                conn.execute(
                    "INSERT INTO receipt_templates (farm_id, name, show_tax_breakdown, show_box_size, note, is_active) VALUES (?,?,?,?,?,0)",
                    (farm_id, name, show_tax, show_box, note),
                )
                conn.commit()
                st.rerun()
    rows = conn.execute("SELECT * FROM receipt_templates WHERE farm_id=?", (farm_id,)).fetchall()
    for t in rows:
        with st.container(border=True):
            st.markdown(f"**{t['name']}**" + ("　✅ 使用中" if t["is_active"] else ""))
            st.caption(f"税内訳表示: {'あり' if t['show_tax_breakdown'] else 'なし'} / 箱サイズ表示: {'あり' if t['show_box_size'] else 'なし'}")
            cols = st.columns(2)
            if not t["is_active"] and cols[0].button("これを使用中にする", key=f"act_{t['id']}"):
                conn.execute("UPDATE receipt_templates SET is_active=0 WHERE farm_id=?", (farm_id,))
                conn.execute("UPDATE receipt_templates SET is_active=1 WHERE id=?", (t["id"],))
                conn.commit()
                st.rerun()
            if cols[1].button("削除", key=f"deltmpl_{t['id']}"):
                conn.execute("DELETE FROM receipt_templates WHERE id=?", (t["id"],))
                conn.commit()
                st.rerun()

# ---------- 農園情報 ----------
def profile_screen(conn, farm_id):
    st.markdown("### 農園情報の編集")
    farm = conn.execute("SELECT * FROM farms WHERE id=?", (farm_id,)).fetchone()
    with st.form("profile"):
        name = st.text_input("農園名", value=farm["name"])
        phone = st.text_input("電話番号", value=farm["phone"] or "")
        postal = st.text_input("郵便番号", value=farm["postal_code"] or "")
        address = st.text_input("住所", value=farm["address"] or "")
        if st.form_submit_button("保存"):
            conn.execute("UPDATE farms SET name=?, phone=?, postal_code=?, address=? WHERE id=?",
                         (name, phone, postal, address, farm_id))
            conn.commit()
            st.success("保存しました")
            st.rerun()

# ---------- 機能活用ガイド ----------
GUIDE_CONTENT = {
    "order": "注文画面では、配送先の地域と商品の箱サイズから送料が自動計算されます。複数の配送先にまとめて配送する場合は「配送先を追加」で分けて入力できます。セットアップが完了していないと利用できません。",
    "inventory_view": "在庫管理では商品ごとの在庫数を編集できます。「在庫わずかのみ表示」で残数が少ない商品だけを素早く確認できます。",
    "customers_view": "顧客情報では顧客マスタの登録・検索ができます。ここに登録した顧客は今後の注文で選択できるようになります。",
    "payout_view": "入金画面では、発送済みの注文のうち未清算のものをまとめて確認し、手数料15%を控除した入金予定額を計算できます。",
}

def guide_screen(conn, farm_id):
    st.markdown("### 機能活用ガイド")
    disclosed = {r["feature_key"] for r in conn.execute(
        "SELECT feature_key FROM feature_disclosure WHERE farm_id=? AND enabled=1", (farm_id,)
    ).fetchall()}
    visible_keys = FEATURE_KEYS.keys() if st.session_state.role == "admin" else disclosed
    for key in visible_keys:
        with st.expander(FEATURE_KEYS[key]):
            st.write(GUIDE_CONTENT.get(key, "説明は準備中です。"))
            if st.button(f"{FEATURE_KEYS[key]}の画面へ", key=f"goto_{key}"):
                st.info("左メニューの対応する画面から操作してください。")

# ---------- ランクアップ道場 ----------
def quiz_screen(conn, farm_id):
    st.markdown("### ランクアップ道場")
    pts = total_points(conn, st.session_state.user_pk, farm_id)
    tier = rank_for_points(pts)
    ratio, next_t = next_tier_progress(pts)
    st.write(f"あなたのランク: **{tier[1]}（{tier[2]}）** — {pts}pt")
    st.progress(min(1.0, ratio), text=f"次のランク {next_t[1]}（{next_t[2]}）まで")

    ftier, favg = farm_rank(conn, farm_id)
    st.write(f"農園全体ランク: **{ftier[1]}（{ftier[2]}）**（参加メンバー全体の到達度で判定）")
    st.progress(min(1.0, favg), text="農園全体の進捗")

    st.divider()
    disclosed = {r["feature_key"] for r in conn.execute(
        "SELECT feature_key FROM feature_disclosure WHERE farm_id=? AND enabled=1", (farm_id,)
    ).fetchall()}
    allowed_keys = list(FEATURE_KEYS.keys()) if st.session_state.role == "admin" else list(disclosed)
    if not allowed_keys:
        st.info("開示されている機能がまだありません。管理者にお問い合わせください。")
        return

    level = st.radio("クイズの種類", ["beginner", "intermediate", "advanced", "weak"],
                      format_func=lambda x: {"beginner": "初心者", "intermediate": "中級者", "advanced": "上級者", "weak": "苦手問題だけ出題"}[x])

    if st.button("クイズを開始（5問）", type="primary"):
        if level == "weak":
            qs = conn.execute(
                "SELECT q.* FROM quiz_questions q WHERE q.feature_key IN ({}) AND ("
                "(SELECT COUNT(*) FROM quiz_attempts a WHERE a.question_id=q.id AND a.user_id=? AND a.correct=1)=0"
                " OR (SELECT AVG(a.correct)*1.0 FROM quiz_attempts a WHERE a.question_id=q.id AND a.user_id=?) < 0.5)"
                .format(",".join("?" * len(allowed_keys))),
                allowed_keys + [st.session_state.user_pk, st.session_state.user_pk],
            ).fetchall()
        else:
            qs = conn.execute(
                "SELECT * FROM quiz_questions WHERE feature_key IN ({}) AND level=?".format(",".join("?" * len(allowed_keys))),
                allowed_keys + [level],
            ).fetchall()
        if not qs:
            st.warning("出題できる問題がありません（開示機能を増やすと出題対象が広がります）")
        else:
            import random
            picked = random.sample(qs, min(5, len(qs)))
            st.session_state.quiz_set = [dict(q) for q in picked]
            st.session_state.quiz_index = 0
            st.session_state.quiz_answers = []
            st.rerun()

    if "quiz_set" in st.session_state and st.session_state.quiz_index < len(st.session_state.quiz_set):
        q = st.session_state.quiz_set[st.session_state.quiz_index]
        st.markdown(f"**Q{st.session_state.quiz_index+1}. {q['question']}**")
        choices = q["choices"].split("|")
        choice = st.radio("選択してください", choices, key=f"quiz_choice_{st.session_state.quiz_index}")
        if st.button("回答する", key=f"quiz_submit_{st.session_state.quiz_index}"):
            is_correct = choices.index(choice) == q["correct_index"]
            conn.execute("INSERT INTO quiz_attempts (user_id, question_id, correct, attempted_at) VALUES (?,?,?,?)",
                         (st.session_state.user_pk, q["id"], int(is_correct), now()))
            conn.commit()
            st.session_state.quiz_answers.append({"q": q, "correct": is_correct, "picked": choice})
            st.session_state.quiz_index += 1
            st.rerun()
    elif "quiz_set" in st.session_state and st.session_state.quiz_index >= len(st.session_state.quiz_set) and st.session_state.quiz_set:
        st.markdown("#### 結果")
        correct_count = sum(1 for a in st.session_state.quiz_answers if a["correct"])
        st.write(f"{correct_count} / {len(st.session_state.quiz_answers)} 問正解")
        for a in st.session_state.quiz_answers:
            icon = "✅" if a["correct"] else "❌"
            st.write(f"{icon} {a['q']['question']}")
            st.caption(f"あなたの回答: {a['picked']} / 正解: {a['q']['choices'].split('|')[a['q']['correct_index']]}")
            st.caption(f"解説: {a['q']['explanation']}")
        if st.button("道場を終える"):
            del st.session_state.quiz_set
            del st.session_state.quiz_index
            del st.session_state.quiz_answers
            st.rerun()

# ---------- 運営者コンソール ----------
def operator_console_screen(conn):
    st.markdown("### 運営者コンソール")
    with st.expander("新規農園アカウントを発行"):
        with st.form("new_farm", clear_on_submit=True):
            name = st.text_input("農園名")
            code = st.text_input("農園ID（company_code）")
            admin_uid = st.text_input("管理者ユーザーID")
            admin_pw = st.text_input("管理者パスワード", type="password")
            if st.form_submit_button("発行") and name and code and admin_uid and admin_pw:
                exists = conn.execute("SELECT 1 FROM farms WHERE company_code=?", (code,)).fetchone()
                if exists:
                    st.error("その農園IDは既に使われています")
                else:
                    conn.execute("INSERT INTO farms (name, company_code, created_at) VALUES (?,?,?)", (name, code, now()))
                    farm_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
                    conn.execute("INSERT INTO users (farm_id, user_id, password_hash, role) VALUES (?,?,?,?)",
                                 (farm_id, admin_uid, hash_pw_local(admin_pw), "admin"))
                    conn.execute("INSERT INTO carrier_settings (farm_id) VALUES (?)", (farm_id,))
                    conn.execute("INSERT INTO setup_progress (farm_id) VALUES (?)", (farm_id,))
                    conn.commit()
                    st.success("発行しました")
                    st.rerun()

    farms = conn.execute("SELECT * FROM farms ORDER BY created_at DESC").fetchall()
    for f in farms:
        with st.container(border=True):
            cols = st.columns([3, 1])
            order_count = conn.execute("SELECT COUNT(*) c FROM orders WHERE farm_id=?", (f["id"],)).fetchone()["c"]
            cols[0].markdown(f"**{f['name']}**（ID: {f['company_code']}） / 注文数: {order_count}")
            if cols[1].button("削除", key=f"delfarm_{f['id']}"):
                st.session_state[f"confirm_delfarm_{f['id']}"] = True
            if st.session_state.get(f"confirm_delfarm_{f['id']}"):
                if st.button(f"本当に削除する（{f['name']}）", key=f"confirmf2_{f['id']}"):
                    conn.execute("DELETE FROM farms WHERE id=?", (f["id"],))
                    conn.execute("DELETE FROM users WHERE farm_id=?", (f["id"],))
                    conn.commit()
                    st.rerun()
            admin_users = conn.execute("SELECT * FROM users WHERE farm_id=? AND role='admin'", (f["id"],)).fetchall()
            for u in admin_users:
                new_pw = st.text_input(f"{u['user_id']} の新パスワード", type="password", key=f"resetpw_{u['id']}")
                if st.button("パスワードリセット", key=f"resetbtn_{u['id']}") and new_pw:
                    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw_local(new_pw), u["id"]))
                    conn.commit()
                    st.success("リセットしました")

# ---------- メイン ----------
def main():
    if st.session_state.get("is_operator"):
        with st.sidebar:
            st.markdown(f"**運営者: {st.session_state.user_id}**")
            if st.button("ログアウト"):
                st.session_state.pop("is_operator", None)
                st.session_state.pop("user_id", None)
                st.rerun()
        conn = get_conn()
        operator_console_screen(conn)
        conn.close()
        return

    if "farm_id" not in st.session_state:
        login_screen()
        return

    conn = get_conn()
    refresh_setup_progress(conn, st.session_state.farm_id)

    with st.sidebar:
        st.markdown(f"**{st.session_state.farm_name}**")
        st.caption(f"{st.session_state.user_id}（{'管理者' if st.session_state.role=='admin' else '一般'}）")
        pages = ["ホーム", "注文", "機能活用ガイド", "ランクアップ道場", "ホーム表示設定"]
        if st.session_state.role == "admin":
            pages += ["商品管理", "在庫管理", "発送管理", "顧客情報", "売上明細", "売上分析", "送料設定",
                      "領収書設定", "入金", "ユーザー管理", "支払方法", "農園情報"]
        page = st.radio("画面", pages)
        if st.button("ログアウト"):
            for k in ["farm_id", "farm_name", "user_id", "user_pk", "role"]:
                st.session_state.pop(k, None)
            st.rerun()

    farm_id = st.session_state.farm_id
    screens = {
        "ホーム": home_screen, "注文": order_screen, "機能活用ガイド": guide_screen,
        "ランクアップ道場": quiz_screen, "ホーム表示設定": home_customize_screen,
        "商品管理": products_screen, "在庫管理": inventory_screen, "発送管理": shipping_admin_screen,
        "顧客情報": customers_screen, "売上明細": sales_detail_screen, "売上分析": sales_analysis_screen,
        "送料設定": shipping_settings_screen, "領収書設定": receipt_settings_screen, "入金": payout_screen,
        "ユーザー管理": users_screen, "支払方法": payment_methods_screen, "農園情報": profile_screen,
    }
    screens[page](conn, farm_id)
    conn.close()

if __name__ == "__main__":
    main()
