import logging
import math
import os
import re
import secrets
import sqlite3
import time
from functools import wraps
from urllib.parse import urlparse

import psycopg
import requests
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_VERCEL = os.environ.get("VERCEL") == "1"
if IS_VERCEL and not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database on Vercel.")

secret_key = os.environ.get("SECRET_KEY")
if IS_VERCEL and not secret_key:
    raise RuntimeError("Set SECRET_KEY in the Vercel project environment.")
if IS_VERCEL and not (
    os.environ.get("ADMIN_EMAIL", "").strip() and os.environ.get("ADMIN_PASSWORD")
):
    raise RuntimeError("Set ADMIN_EMAIL and ADMIN_PASSWORD in the Vercel project environment.")

app = Flask(__name__)
app.secret_key = secret_key or os.urandom(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get(
        "COOKIE_SECURE", "true" if IS_VERCEL else "false"
    ).lower() == "true",
    BKASH_BASE_URL=os.environ.get(
        "BKASH_BASE_URL",
        "https://tokenized.sandbox.bka.sh/v2/tokenized-checkout",
    ).rstrip("/"),
    PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
)
DB_NAME = os.environ.get("DATABASE_PATH", "habi_jabi.db")
logger = logging.getLogger(__name__)


class BkashError(Exception):
    pass


class DatabaseConnection:
    def __init__(self):
        self.is_postgres = bool(DATABASE_URL)
        if self.is_postgres:
            self.connection = psycopg.connect(DATABASE_URL)
        else:
            self.connection = sqlite3.connect(DB_NAME)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")

    def execute(self, query, params=None):
        if self.is_postgres:
            query = query.replace("?", "%s")
        if params is None:
            cursor = self.connection.execute(query)
        else:
            cursor = self.connection.execute(query, params)
        return DatabaseResult(cursor, self.is_postgres)

    def executemany(self, query, params):
        if self.is_postgres:
            query = query.replace("?", "%s")
        cursor = self.connection.cursor()
        cursor.executemany(query, params)
        return DatabaseResult(cursor, self.is_postgres)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


class DatabaseResult:
    def __init__(self, cursor, is_postgres):
        self.cursor = cursor
        self.is_postgres = is_postgres

    def _as_dict(self, row):
        if row is None:
            return None
        if not self.is_postgres:
            return dict(row)
        columns = [column.name for column in self.cursor.description]
        return dict(zip(columns, row))

    def fetchone(self):
        return self._as_dict(self.cursor.fetchone())

    def fetchall(self):
        rows = []
        for row in self.cursor.fetchall():
            mapped_row = self._as_dict(row)
            if mapped_row is not None:
                rows.append(mapped_row)
        return rows

    def fetchone_required(self):
        row = self.fetchone()
        if row is None:
            raise RuntimeError("Expected a database row, but the query returned none.")
        return row

    @property
    def rowcount(self):
        return self.cursor.rowcount


def get_db():
    return DatabaseConnection()


def get_table_columns(conn, table):
    if DATABASE_URL:
        rows = conn.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = ?""",
            (table,),
        ).fetchall()
        return {row["column_name"] for row in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def marketplace_seed_products():
    groups = [
        (
            "Electronics",
            "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=500&q=80",
            "Reliable everyday technology with practical features.",
            850,
            225,
            [
                "Nova A15 Smartphone",
                "128GB Android Smartphone",
                "43-inch 4K Smart TV",
                "Portable Bluetooth Speaker",
                "Noise-Canceling Headphones",
                "Dual-Band Wi-Fi Router",
                "30W USB-C Fast Charger",
                "1TB Portable SSD",
                "Full HD USB Webcam",
                "Wireless Mechanical Keyboard",
            ],
        ),
        (
            "Fashion",
            "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?w=500&q=80",
            "Comfortable style made for daily wear.",
            650,
            180,
            [
                "Everyday Cotton T-Shirt",
                "Classic Denim Jeans",
                "Men's Casual Sneakers",
                "Women's Canvas Tote Bag",
                "Lightweight Zip Jacket",
                "Printed Cotton Kurti",
                "Everyday Polo Shirt",
                "Relaxed Fit Chinos",
                "Crossbody Shoulder Bag",
                "Soft Knit Cardigan",
            ],
        ),
        (
            "Gadgets",
            "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&q=80",
            "Handy gadgets designed to make the day easier.",
            550,
            145,
            [
                "Active Fit Smart Band",
                "AMOLED Smart Watch",
                "Mini Home Projector",
                "7-in-1 USB-C Hub",
                "20,000mAh Power Bank",
                "Smart RGB LED Strip",
                "Silent Wireless Mouse",
                "Bluetooth Key Finder",
                "Adjustable Car Phone Mount",
                "Rechargeable Neck Fan",
            ],
        ),
        (
            "Home & Living",
            "https://images.unsplash.com/photo-1570222094114-d054a817e56b?w=500&q=80",
            "Thoughtful essentials for a more comfortable home.",
            750,
            195,
            [
                "Soft Cotton Bedsheet Set",
                "Minimal Table Lamp",
                "Memory Foam Pillow Pair",
                "Woven Laundry Basket",
                "Foldable Storage Organizer",
                "Indoor Ceramic Planter Set",
                "Non-Slip Door Mat",
                "Double-Layer Blackout Curtains",
                "Wall-Mounted Floating Shelf",
                "Compact Air Purifier",
            ],
        ),
        (
            "Beauty",
            "https://images.unsplash.com/photo-1556228720-195a672e8a03?w=500&q=80",
            "Simple self-care picks for your daily routine.",
            350,
            95,
            [
                "Gentle Daily Face Cleanser",
                "Hydrating Face Moisturizer",
                "Vitamin C Brightening Serum",
                "SPF 50 Daily Sunscreen",
                "Nourishing Hair Oil",
                "Everyday Makeup Brush Set",
                "Soothing Aloe Vera Gel",
                "Reusable Makeup Remover Pads",
                "Overnight Lip Care Balm",
                "Strengthening Shampoo Set",
            ],
        ),
        (
            "Sports & Outdoors",
            "https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=500&q=80",
            "Gear for movement, training, and time outdoors.",
            450,
            130,
            [
                "Non-Slip Yoga Mat",
                "Adjustable Jump Rope",
                "Training Football Size 5",
                "Outdoor Basketball",
                "Pair of Adjustable Dumbbells",
                "Resistance Band Set",
                "Insulated Sports Bottle",
                "Lightweight Gym Duffel Bag",
                "Ventilated Cycling Helmet",
                "Recreational Tennis Racket",
            ],
        ),
        (
            "Kids & Baby",
            "https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=500&q=80",
            "Playful, practical essentials for little ones.",
            400,
            110,
            [
                "Colorful Building Block Set",
                "Soft Plush Teddy Bear",
                "Wooden Shape Sorter",
                "Kids' Drawing Board",
                "Baby Cotton Blanket",
                "Stacking Rings Toy",
                "Reusable Kids' Lunch Box",
                "Watercolor Paint Set for Kids",
                "Animal Puzzle Board",
                "Adjustable Kids' Backpack",
            ],
        ),
        (
            "Books & Stationery",
            "https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=500&q=80",
            "Useful paper goods for ideas, learning, and work.",
            120,
            35,
            [
                "Hardcover Daily Journal",
                "Weekly Desk Planner",
                "A5 Ruled Notebook Pack",
                "Watercolor Sketchbook",
                "24-Color Marker Set",
                "Smooth-Write Ballpoint Pens",
                "Compact Desk Organizer",
                "Pastel Highlighter Set",
                "Reusable Study Flash Cards",
                "Premium Gift-Wrap Paper Set",
            ],
        ),
        (
            "Kitchen & Dining",
            "https://images.unsplash.com/photo-1556911220-e15b29be8c8f?w=500&q=80",
            "Hardworking kitchen tools for everyday meals.",
            280,
            85,
            [
                "1.5L Electric Kettle",
                "Non-Stick Cookware Set",
                "Stainless Steel Knife Set",
                "Digital Kitchen Scale",
                "Borosilicate Glass Food Containers",
                "Manual Vegetable Chopper",
                "Insulated Travel Mug",
                "Bamboo Serving Board",
                "Stackable Ceramic Dinner Set",
                "Adjustable Measuring Cup Set",
            ],
        ),
        (
            "Health & Personal Care",
            "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=500&q=80",
            "Everyday wellness essentials for home and travel.",
            320,
            90,
            [
                "Digital Forehead Thermometer",
                "Compact Handheld Massager",
                "Portable Water Flosser",
                "Breathable Sleep Eye Mask",
                "Automatic Pill Organizer",
                "Compact First Aid Kit",
                "Essential Oil Humidifier",
                "Reusable Hot and Cold Pack",
                "Travel Personal Care Kit",
                "Ergonomic Lumbar Support Cushion",
            ],
        ),
    ]
    products = []
    for category, image, description, base_price, increment, names in groups:
        for index, name in enumerate(names):
            price = base_price + increment * index
            products.append(
                (name, category, price, round(price * 1.25), image, description)
            )
    return products


def init_db():
    conn = get_db()
    cursor = conn
    if DATABASE_URL:
        cursor.execute("SELECT pg_advisory_xact_lock(710241)")
    id_primary_key = (
        "SERIAL PRIMARY KEY"
        if DATABASE_URL
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    price_type = "DOUBLE PRECISION" if DATABASE_URL else "REAL"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS sellers (
            id {id_primary_key},
            name TEXT NOT NULL,
            store_name TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    store_name_index = (
        "LOWER(store_name)" if DATABASE_URL else "store_name COLLATE NOCASE"
    )
    cursor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_sellers_store_name_nocase ON sellers({store_name_index})"
    )
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS products (
            id {id_primary_key},
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price {price_type} NOT NULL,
            old_price {price_type},
            image TEXT NOT NULL,
            description TEXT,
            seller_id INTEGER REFERENCES sellers(id)
        )
    """)
    product_columns = get_table_columns(conn, "products")
    if "seller_id" not in product_columns:
        cursor.execute(
            "ALTER TABLE products ADD COLUMN seller_id INTEGER REFERENCES sellers(id)"
        )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_seller_id ON products(seller_id)")
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id {id_primary_key},
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS admins (
            id {id_primary_key},
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS orders (
            id {id_primary_key},
            user_id INTEGER REFERENCES users(id),
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            total_amount {price_type} NOT NULL,
            items TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            payment_status TEXT NOT NULL DEFAULT 'Due',
            bkash_payment_id TEXT,
            bkash_trx_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    existing_columns = get_table_columns(conn, "orders")
    order_migrations = {
        "user_id": "INTEGER REFERENCES users(id)",
        "status": "TEXT NOT NULL DEFAULT 'Pending'",
        "payment_status": "TEXT NOT NULL DEFAULT 'Due'",
        "bkash_payment_id": "TEXT",
        "bkash_trx_id": "TEXT",
    }
    for column, definition in order_migrations.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS integration_tokens (
            provider TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT,
            expires_at INTEGER NOT NULL
        )
    """)

    product_count = cursor.execute(
        "SELECT COUNT(*) AS product_count FROM products"
    ).fetchone_required()["product_count"]
    if product_count == 0:
        sample_products = [
            ("Wireless ANC Headphones", "Electronics", 2450, 3200, "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=500&q=80", "Deep bass, 30-hour battery life, and active noise cancellation."),
            ("Classic Men's Cotton Panjabi", "Fashion", 1350, 1800, "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?w=500&q=80", "100% breathable premium cotton, tailored fit."),
            ("Smart Fitness Watch Pro", "Gadgets", 1890, 2500, "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&q=80", "Heart rate monitor, IP68 waterproof, 7-day battery."),
            ("Mechanical Gaming Keyboard RGB", "Electronics", 3100, 3800, "https://images.unsplash.com/photo-1595225476474-87563907a212?w=500&q=80", "Tactile switches with customizable RGB backlight."),
            ("Minimalist Leather Backpack", "Fashion", 1650, 2200, "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=500&q=80", "Water-resistant PU leather, fits up to 15.6-inch laptop."),
            ("Portable Mini Blender", "Home & Living", 1150, 1500, "https://images.unsplash.com/photo-1570222094114-d054a817e56b?w=500&q=80", "USB rechargeable 6-blade juicer for smoothies on the go."),
        ]
        cursor.executemany(
            "INSERT INTO products (name, category, price, old_price, image, description) VALUES (?, ?, ?, ?, ?, ?)",
            sample_products,
        )
        product_count += len(sample_products)

    existing_names = {
        row["name"] for row in cursor.execute("SELECT name FROM products").fetchall()
    }
    for product in marketplace_seed_products():
        if product_count >= 100:
            break
        if product[0] in existing_names:
            continue
        cursor.execute(
            """INSERT INTO products
               (name, category, price, old_price, image, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            product,
        )
        existing_names.add(product[0])
        product_count += 1

    admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_email and admin_password:
        cursor.execute(
            """INSERT INTO admins (email, password_hash) VALUES (?, ?)
               ON CONFLICT(email) DO NOTHING""",
            (admin_email, generate_password_hash(admin_password, method="pbkdf2:sha256")),
        )
    elif admin_email or admin_password:
        logger.warning("Set both ADMIN_EMAIL and ADMIN_PASSWORD to bootstrap the admin account.")

    conn.commit()
    conn.close()


init_db()


def csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def load_identity_and_check_csrf():
    g.current_user = None
    g.current_admin = None
    g.current_seller = None
    user_id = session.get("user_id")
    admin_id = session.get("admin_id")
    seller_id = session.get("seller_id")
    if user_id is not None:
        conn = get_db()
        g.current_user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if g.current_user is None:
            session.pop("user_id", None)
    if admin_id is not None:
        conn = get_db()
        g.current_admin = conn.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
        conn.close()
        if g.current_admin is None:
            session.pop("admin_id", None)
    if seller_id is not None:
        conn = get_db()
        g.current_seller = conn.execute(
            "SELECT * FROM sellers WHERE id = ?", (seller_id,)
        ).fetchone()
        conn.close()
        if g.current_seller is None:
            session.pop("seller_id", None)
    if request.method == "POST":
        saved_token = session.get("_csrf_token", "")
        submitted_token = request.form.get("csrf_token", "")
        if not saved_token or not secrets.compare_digest(saved_token, submitted_token):
            abort(400, description="The form expired. Reload the page and try again.")


@app.context_processor
def inject_template_context():
    cart = session.get("cart", {})
    total_items = sum(quantity for quantity in cart.values() if isinstance(quantity, int))
    return {
        "cart_count": total_items,
        "current_user": g.get("current_user"),
        "current_admin": g.get("current_admin"),
        "current_seller": g.get("current_seller"),
        "csrf_token": csrf_token,
        "bkash_configured": bkash_is_configured(),
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.current_user is None:
            flash("Please sign in to continue.")
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.current_admin is None:
            return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def seller_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.current_seller is None:
            flash("Sign in to your seller account to continue.")
            return redirect(url_for("seller_login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def safe_next_url(target):
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def bkash_is_configured():
    credentials_configured = all(
        os.environ.get(key)
        for key in ("BKASH_APP_KEY", "BKASH_APP_SECRET", "BKASH_USERNAME", "BKASH_PASSWORD")
    )
    public_url = urlparse(app.config["PUBLIC_BASE_URL"])
    local_http = public_url.scheme == "http" and public_url.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    return bool(
        credentials_configured
        and public_url.netloc
        and (public_url.scheme == "https" or local_http)
    )


def bkash_endpoint(path):
    base_url = app.config["BKASH_BASE_URL"]
    parsed_base_url = urlparse(base_url)
    if (
        parsed_base_url.scheme != "https"
        or not parsed_base_url.netloc
        or parsed_base_url.query
        or parsed_base_url.fragment
    ):
        raise BkashError("The bKash API base URL must use HTTPS.")
    return f"{base_url}/{path.lstrip('/')}"


def bkash_auth_request(path, body):
    required = ("BKASH_APP_KEY", "BKASH_APP_SECRET", "BKASH_USERNAME", "BKASH_PASSWORD")
    if not all(os.environ.get(key) for key in required):
        raise BkashError("bKash merchant credentials are not configured.")
    try:
        response = requests.post(
            bkash_endpoint(path),
            headers={
                "username": os.environ["BKASH_USERNAME"],
                "password": os.environ["BKASH_PASSWORD"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
            timeout=(5, 25),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BkashError("Could not connect to bKash. Please try again.") from exc
    if data.get("statusCode") not in (None, "0000") or not data.get("id_token"):
        raise BkashError(data.get("statusMessage") or "bKash authentication failed.")
    return data


def get_bkash_token():
    conn = get_db()
    cached = conn.execute(
        "SELECT * FROM integration_tokens WHERE provider = 'bkash'"
    ).fetchone()
    conn.close()
    now = int(time.time())
    if cached and cached["expires_at"] > now + 60:
        return cached["access_token"]

    credentials = {
        "app_key": os.environ.get("BKASH_APP_KEY", ""),
        "app_secret": os.environ.get("BKASH_APP_SECRET", ""),
    }
    if cached and cached["refresh_token"]:
        credentials["refresh_token"] = cached["refresh_token"]
        response_data = bkash_auth_request("auth/refresh-token", credentials)
    else:
        response_data = bkash_auth_request("auth/grant-token", credentials)

    expires_in = int(response_data.get("expires_in", 3600))
    conn = get_db()
    conn.execute(
        """INSERT INTO integration_tokens (provider, access_token, refresh_token, expires_at)
           VALUES ('bkash', ?, ?, ?)
           ON CONFLICT(provider) DO UPDATE SET
               access_token = excluded.access_token,
               refresh_token = excluded.refresh_token,
               expires_at = excluded.expires_at""",
        (
            response_data["id_token"],
            response_data.get("refresh_token"),
            now + expires_in,
        ),
    )
    conn.commit()
    conn.close()
    return response_data["id_token"]


def bkash_request(path, body):
    token = get_bkash_token()
    try:
        response = requests.post(
            bkash_endpoint(path),
            headers={
                "Authorization": token,
                "X-App-Key": os.environ["BKASH_APP_KEY"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
            timeout=(5, 25),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BkashError("Could not connect to bKash. Please try again.") from exc
    if data.get("externalCode") or data.get("errorMessageEn"):
        raise BkashError(data.get("errorMessageEn") or "bKash could not process the payment.")
    return data


def payment_is_verified(order, response_data):
    if response_data.get("paymentId") != order["bkash_payment_id"]:
        return False
    if response_data.get("transactionStatus") != "Completed":
        return False
    if response_data.get("currency") != "BDT":
        return False
    try:
        received_amount = round(float(response_data["amount"]), 2)
    except (KeyError, TypeError, ValueError):
        return False
    if received_amount != round(float(order["total_amount"]), 2):
        return False
    invoice = response_data.get("merchantInvoiceNumber") or response_data.get("merchantInvoice")
    if invoice and invoice != f"ORDER-{order['id']}":
        return False
    return bool(response_data.get("trxId"))


def record_verified_bkash_payment(order, response_data):
    conn = get_db()
    conn.execute(
        """UPDATE orders SET payment_status = 'Paid', bkash_trx_id = ?
           WHERE id = ? AND bkash_payment_id = ? AND payment_status != 'Paid'""",
        (response_data["trxId"], order["id"], order["bkash_payment_id"]),
    )
    conn.commit()
    conn.close()
    if g.current_user and g.current_user["id"] == order["user_id"]:
        session.pop("cart", None)


def current_callback_url():
    callback_path = url_for("bkash_callback")
    if app.config["PUBLIC_BASE_URL"]:
        return f"{app.config['PUBLIC_BASE_URL']}{callback_path}"
    raise BkashError("Set PUBLIC_BASE_URL to the public checkout origin.")


@app.route("/")
def index():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "All")
    conn = get_db()
    query = """SELECT products.*, sellers.store_name AS seller_name
               FROM products LEFT JOIN sellers ON sellers.id = products.seller_id
               WHERE 1=1"""
    params = []
    if search:
        query += " AND (LOWER(name) LIKE LOWER(?) OR LOWER(description) LIKE LOWER(?))"
        params.extend([f"%{search}%", f"%{search}%"])
    if category and category != "All":
        query += " AND category = ?"
        params.append(category)
    products = conn.execute(query, params).fetchall()
    categories = [
        row["category"]
        for row in conn.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall()
    ]
    conn.close()
    return render_template(
        "index.html",
        products=products,
        categories=categories,
        selected_category=category,
        search=search,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.current_user is not None:
        return redirect(url_for("account"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter your name and a valid email address.")
            return render_template("register.html")
        if len(password) < 8:
            flash("Your password must be at least 8 characters.")
            return render_template("register.html")
        if not re.fullmatch(r"01[3-9]\d{8}", phone):
            flash("Enter a valid Bangladesh mobile number.")
            return render_template("register.html")
        conn = get_db()
        try:
            cursor = conn.execute(
                """INSERT INTO users (name, email, phone, password_hash)
                   VALUES (?, ?, ?, ?) RETURNING id""",
                (name, email, phone, generate_password_hash(password, method="pbkdf2:sha256")),
            )
            user_id = cursor.fetchone_required()["id"]
            conn.commit()
        except (sqlite3.IntegrityError, psycopg.IntegrityError):
            conn.close()
            flash("An account with that email already exists.")
            return render_template("register.html")
        conn.close()
        saved_cart = session.get("cart", {})
        session.clear()
        session["cart"] = saved_cart
        session["user_id"] = user_id
        flash("Your customer account is ready.")
        return redirect(url_for("account"))
    return render_template("register.html")


@app.route("/seller/register", methods=["GET", "POST"])
def seller_register():
    if g.current_seller is not None:
        return redirect(url_for("seller_dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        store_name = request.form.get("store_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        if not name or not store_name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter your name, store name, and a valid email address.")
            return render_template("seller_register.html")
        if not re.fullmatch(r"01[3-9]\d{8}", phone):
            flash("Enter a valid Bangladesh mobile number.")
            return render_template("seller_register.html")
        if len(password) < 8:
            flash("Your password must be at least 8 characters.")
            return render_template("seller_register.html")
        conn = get_db()
        try:
            cursor = conn.execute(
                """INSERT INTO sellers (name, store_name, email, phone, password_hash)
                   VALUES (?, ?, ?, ?, ?) RETURNING id""",
                (
                    name,
                    store_name,
                    email,
                    phone,
                    generate_password_hash(password, method="pbkdf2:sha256"),
                ),
            )
            seller_id = cursor.fetchone_required()["id"]
            conn.commit()
        except (sqlite3.IntegrityError, psycopg.IntegrityError):
            conn.close()
            flash("That seller email or store name is already registered.")
            return render_template("seller_register.html")
        conn.close()
        session.clear()
        session["seller_id"] = seller_id
        flash("Your seller account is ready. Add your first product below.")
        return redirect(url_for("seller_dashboard"))
    return render_template("seller_register.html")


@app.route("/seller/login", methods=["GET", "POST"])
def seller_login():
    if g.current_seller is not None:
        return redirect(url_for("seller_dashboard"))
    next_url = safe_next_url(request.args.get("next", ""))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        seller = conn.execute("SELECT * FROM sellers WHERE email = ?", (email,)).fetchone()
        conn.close()
        if seller is None or not check_password_hash(seller["password_hash"], password):
            flash("Seller email or password is incorrect.")
            return render_template("seller_login.html", next_url=next_url)
        session.clear()
        session["seller_id"] = seller["id"]
        flash(f"Welcome to your {seller['store_name']} seller account.")
        return redirect(next_url or url_for("seller_dashboard"))
    return render_template("seller_login.html", next_url=next_url)


@app.route("/seller/logout", methods=["POST"])
@seller_required
def seller_logout():
    session.clear()
    flash("You have signed out of your seller account.")
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user is not None:
        return redirect(url_for("account"))
    next_url = safe_next_url(request.args.get("next", ""))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Email or password is incorrect.")
            return render_template("login.html", next_url=next_url)
        saved_cart = session.get("cart", {})
        session.clear()
        session["cart"] = saved_cart
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['name']}.")
        return redirect(next_url or url_for("account"))
    return render_template("login.html", next_url=next_url)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have signed out.")
    return redirect(url_for("index"))


@app.route("/account")
@login_required
def account():
    conn = get_db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC",
        (g.current_user["id"],),
    ).fetchall()
    conn.close()
    return render_template("account.html", orders=orders)


@app.route("/account/profile", methods=["POST"])
@login_required
def update_profile():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Enter your name and a valid email address.")
        return redirect(url_for("account"))
    if not re.fullmatch(r"01[3-9]\d{8}", phone):
        flash("Enter a valid Bangladesh mobile number.")
        return redirect(url_for("account"))
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET name = ?, email = ?, phone = ? WHERE id = ?",
            (name, email, phone, g.current_user["id"]),
        )
        conn.commit()
    except (sqlite3.IntegrityError, psycopg.IntegrityError):
        conn.close()
        flash("That email address is already associated with another account.")
        return redirect(url_for("account"))
    conn.close()
    flash("Your profile has been updated.")
    return redirect(url_for("account"))


@app.route("/add-to-cart/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    conn = get_db()
    product = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if product is None:
        abort(404)
    cart = session.get("cart", {})
    product_id_string = str(product_id)
    cart[product_id_string] = min(cart.get(product_id_string, 0) + 1, 99)
    session["cart"] = cart
    flash("Item added to your Habi Jabi cart.")
    return redirect(request.referrer or url_for("index"))


@app.route("/cart")
def cart():
    cart_data = session.get("cart", {})
    conn = get_db()
    cart_items = []
    subtotal = 0
    for product_id, quantity in cart_data.items():
        if not str(product_id).isdigit() or not isinstance(quantity, int) or quantity < 1:
            continue
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if product:
            item_total = product["price"] * quantity
            subtotal += item_total
            cart_items.append({"product": product, "qty": quantity, "total": item_total})
    conn.close()
    delivery_fee = 60 if subtotal > 0 else 0
    grand_total = subtotal + delivery_fee
    return render_template(
        "cart.html",
        cart_items=cart_items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        grand_total=grand_total,
    )


@app.route("/update-cart/<int:product_id>/<action>", methods=["POST"])
def update_cart(product_id, action):
    if action not in {"inc", "dec", "remove"}:
        abort(404)
    cart = session.get("cart", {})
    product_id_string = str(product_id)
    if product_id_string in cart:
        if action == "inc":
            cart[product_id_string] = min(cart[product_id_string] + 1, 99)
        elif action == "dec":
            cart[product_id_string] -= 1
            if cart[product_id_string] <= 0:
                del cart[product_id_string]
        else:
            del cart[product_id_string]
    session["cart"] = cart
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    cart_data = session.get("cart", {})
    if not cart_data:
        flash("Your cart is empty.")
        return redirect(url_for("index"))
    customer_name = request.form.get("customer_name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    payment_method = request.form.get("payment_method", "")
    if not customer_name or not address or not re.fullmatch(r"01[3-9]\d{8}", phone):
        flash("Enter your name, a valid mobile number, and delivery address.")
        return redirect(url_for("cart"))
    if payment_method not in {"bKash", "Cash on Delivery"}:
        flash("Choose a supported payment method.")
        return redirect(url_for("cart"))
    if payment_method == "bKash" and not bkash_is_configured():
        flash("bKash is not configured yet. Choose cash on delivery or contact the store.")
        return redirect(url_for("cart"))

    conn = get_db()
    item_summaries = []
    subtotal = 0
    for product_id, quantity in cart_data.items():
        if not str(product_id).isdigit() or not isinstance(quantity, int) or quantity < 1:
            continue
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if product:
            subtotal += product["price"] * quantity
            item_summaries.append(f"{product['name']} (x{quantity})")
    if not item_summaries:
        conn.close()
        session.pop("cart", None)
        flash("Your cart no longer contains available products.")
        return redirect(url_for("index"))

    total_amount = round(subtotal + 60, 2)
    payment_status = "Due" if payment_method == "Cash on Delivery" else "Initiated"
    cursor = conn.execute(
        """INSERT INTO orders
           (user_id, customer_name, phone, address, payment_method, total_amount, items, payment_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
        (
            g.current_user["id"],
            customer_name,
            phone,
            address,
            payment_method,
            total_amount,
            ", ".join(item_summaries),
            payment_status,
        ),
    )
    order_id = cursor.fetchone_required()["id"]
    conn.commit()
    if payment_method == "Cash on Delivery":
        conn.close()
        session.pop("cart", None)
        flash(f"Order #{order_id} placed. Pay ৳{total_amount:.0f} when it arrives.")
        return redirect(url_for("account"))
    conn.close()

    try:
        payment = bkash_request(
            "payment/create",
            {
                "payerReference": phone,
                "callbackURL": current_callback_url(),
                "amount": f"{total_amount:.2f}",
                "currency": "BDT",
                "intent": "sale",
                "merchantInvoiceNumber": f"ORDER-{order_id}",
            },
        )
        payment_id = payment.get("paymentId")
        payment_url = payment.get("bkashURL", "")
        parsed_payment_url = urlparse(payment_url)
        if (
            not payment_id
            or parsed_payment_url.scheme != "https"
            or not parsed_payment_url.hostname
            or not parsed_payment_url.hostname.endswith(".bkash.com")
        ):
            raise BkashError("bKash returned an invalid checkout response.")
    except BkashError:
        logger.exception("Could not create bKash payment for order %s.", order_id)
        conn = get_db()
        conn.execute(
            "UPDATE orders SET payment_status = 'Failed' WHERE id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()
        flash("Could not start bKash checkout. No payment was taken; please try again.")
        return redirect(url_for("cart"))

    conn = get_db()
    conn.execute(
        "UPDATE orders SET bkash_payment_id = ? WHERE id = ?",
        (payment_id, order_id),
    )
    conn.commit()
    conn.close()
    return redirect(payment_url)


@app.route("/payments/bkash/callback")
def bkash_callback():
    payment_id = request.args.get("paymentID", "")
    callback_status = request.args.get("status", "").lower()
    if not payment_id:
        flash("The bKash payment response was incomplete.")
        return redirect(url_for("account") if g.current_user else url_for("login"))
    conn = get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE bkash_payment_id = ? AND payment_method = 'bKash'",
        (payment_id,),
    ).fetchone()
    conn.close()
    if order is None:
        flash("We could not match this bKash payment to an order.")
        return redirect(url_for("account") if g.current_user else url_for("login"))
    account_url = url_for("account") if g.current_user else url_for("login")
    if order["payment_status"] == "Paid":
        flash(f"Payment for order #{order['id']} is confirmed.")
        return redirect(account_url)
    if callback_status in {"failure", "cancel"}:
        try:
            payment_result = bkash_request("query/payment", {"paymentId": payment_id})
        except BkashError:
            logger.exception("Could not verify bKash callback for order %s.", order["id"])
            payment_result = None
        if payment_result and payment_is_verified(order, payment_result):
            record_verified_bkash_payment(order, payment_result)
            flash(f"Payment confirmed for order #{order['id']}.")
        elif (
            payment_result
            and payment_result.get("paymentId") == payment_id
            and payment_result.get("transactionStatus") == "Initiated"
        ):
            payment_status = "Cancelled" if callback_status == "cancel" else "Failed"
            conn = get_db()
            conn.execute(
                "UPDATE orders SET payment_status = ? WHERE id = ? AND payment_status != 'Paid'",
                (payment_status, order["id"]),
            )
            conn.commit()
            conn.close()
            flash("The bKash payment was not completed. You can try placing the order again.")
        else:
            flash("Payment is not confirmed yet. Check your order history before trying again.")
        return redirect(account_url)
    if callback_status != "success":
        flash("The bKash payment is still being verified.")
        return redirect(account_url)

    payment_result = None
    try:
        payment_result = bkash_request("payment/execute", {"paymentId": payment_id})
    except BkashError:
        logger.exception("Could not execute bKash payment for order %s.", order["id"])
    if not payment_result or not payment_is_verified(order, payment_result):
        try:
            payment_result = bkash_request("query/payment", {"paymentId": payment_id})
        except BkashError:
            logger.exception("Could not query bKash payment for order %s.", order["id"])
            payment_result = None
    if payment_result and payment_is_verified(order, payment_result):
        record_verified_bkash_payment(order, payment_result)
        flash(f"Payment confirmed for order #{order['id']}.")
    else:
        flash("Payment is not confirmed yet. Check your order history before trying again.")
    return redirect(account_url)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if g.current_admin is not None:
        return redirect(url_for("admin"))
    next_url = safe_next_url(request.args.get("next", ""))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = get_db()
        admin_user = conn.execute("SELECT * FROM admins WHERE email = ?", (email,)).fetchone()
        conn.close()
        if admin_user is None or not check_password_hash(admin_user["password_hash"], password):
            flash("Admin email or password is incorrect.")
            return render_template("admin_login.html", next_url=next_url)
        session.clear()
        session["admin_id"] = admin_user["id"]
        return redirect(next_url or url_for("admin"))
    return render_template("admin_login.html", next_url=next_url)


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.clear()
    flash("Admin signed out.")
    return redirect(url_for("index"))


def product_form_values():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    image = request.form.get("image", "").strip() or (
        "https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=500&q=80"
    )
    try:
        price = float(request.form.get("price", ""))
        old_price_input = request.form.get("old_price", "").strip()
        old_price = float(old_price_input) if old_price_input else None
    except ValueError as exc:
        raise ValueError("Enter valid product prices.") from exc
    image_url = urlparse(image)
    if (
        not name
        or not category
        or not math.isfinite(price)
        or price <= 0
        or (old_price is not None and (not math.isfinite(old_price) or old_price < 0))
    ):
        raise ValueError("Enter a product name, category, and valid price.")
    if image_url.scheme not in {"http", "https"} or not image_url.netloc:
        raise ValueError("Product image must use an http or https URL.")
    return name, category, price, old_price, image, description


@app.route("/seller/dashboard", methods=["GET", "POST"])
@seller_required
def seller_dashboard():
    if request.method == "POST":
        try:
            product_values = product_form_values()
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("seller_dashboard"))
        conn = get_db()
        conn.execute(
            """INSERT INTO products
               (name, category, price, old_price, image, description, seller_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (*product_values, g.current_seller["id"]),
        )
        conn.commit()
        conn.close()
        flash("Your product is now listed in the marketplace.")
        return redirect(url_for("seller_dashboard"))

    conn = get_db()
    products = conn.execute(
        "SELECT * FROM products WHERE seller_id = ? ORDER BY id DESC",
        (g.current_seller["id"],),
    ).fetchall()
    conn.close()
    return render_template("seller_dashboard.html", products=products)


@app.route("/seller/product/<int:product_id>", methods=["GET", "POST"])
@seller_required
def seller_edit_product(product_id):
    conn = get_db()
    product = conn.execute(
        "SELECT * FROM products WHERE id = ? AND seller_id = ?",
        (product_id, g.current_seller["id"]),
    ).fetchone()
    if product is None:
        conn.close()
        abort(404)
    if request.method == "POST":
        try:
            product_values = product_form_values()
        except ValueError as exc:
            conn.close()
            flash(str(exc))
            return redirect(url_for("seller_edit_product", product_id=product_id))
        conn.execute(
            """UPDATE products SET name = ?, category = ?, price = ?, old_price = ?,
               image = ?, description = ? WHERE id = ? AND seller_id = ?""",
            (*product_values, product_id, g.current_seller["id"]),
        )
        conn.commit()
        conn.close()
        flash("Your product details have been updated.")
        return redirect(url_for("seller_dashboard"))
    conn.close()
    return render_template("seller_edit_product.html", product=product)


@app.route("/seller/product/<int:product_id>/delete", methods=["POST"])
@seller_required
def seller_delete_product(product_id):
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM products WHERE id = ? AND seller_id = ?",
        (product_id, g.current_seller["id"]),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        abort(404)
    flash("Your product has been removed from the marketplace.")
    return redirect(url_for("seller_dashboard"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        try:
            product_values = product_form_values()
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("admin"))
        conn = get_db()
        conn.execute(
            """INSERT INTO products (name, category, price, old_price, image, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            product_values,
        )
        conn.commit()
        conn.close()
        flash("Product added to the catalog.")
        return redirect(url_for("admin"))

    conn = get_db()
    products = conn.execute(
        """SELECT products.*, sellers.store_name AS seller_name FROM products
           LEFT JOIN sellers ON sellers.id = products.seller_id
           ORDER BY products.id DESC"""
    ).fetchall()
    orders = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    sellers = conn.execute("SELECT * FROM sellers ORDER BY id DESC").fetchall()
    conn.close()
    return render_template(
        "admin.html", products=products, orders=orders, users=users, sellers=sellers
    )


@app.route("/admin/product/<int:product_id>", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    conn = get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if product is None:
        conn.close()
        abort(404)
    if request.method == "POST":
        try:
            product_values = product_form_values()
        except ValueError as exc:
            conn.close()
            flash(str(exc))
            return redirect(url_for("edit_product", product_id=product_id))
        conn.execute(
            """UPDATE products SET name = ?, category = ?, price = ?, old_price = ?,
               image = ?, description = ? WHERE id = ?""",
            (*product_values, product_id),
        )
        conn.commit()
        conn.close()
        flash("Product updated.")
        return redirect(url_for("admin"))
    conn.close()
    return render_template("edit_product.html", product=product)


@app.route("/admin/delete/<int:product_id>", methods=["POST"])
@admin_required
def delete_product(product_id):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    flash("Product deleted.")
    return redirect(url_for("admin"))


@app.route("/admin/order/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    allowed_statuses = {"Pending", "Processing", "Shipped", "Delivered", "Cancelled"}
    status = request.form.get("status", "")
    if status not in allowed_statuses:
        abort(400, description="Invalid order status.")
    conn = get_db()
    order = conn.execute(
        "SELECT payment_status FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if order is None:
        conn.close()
        abort(404)
    if status == "Cancelled" and order["payment_status"] == "Paid":
        conn.close()
        flash("A paid order cannot be cancelled here; issue a refund before cancelling it.")
        return redirect(url_for("admin"))
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()
    flash(f"Order #{order_id} updated.")
    return redirect(url_for("admin"))


@app.route("/admin/order/<int:order_id>/payment", methods=["POST"])
@admin_required
def update_cod_payment_status(order_id):
    payment_status = request.form.get("payment_status", "")
    if payment_status not in {"Due", "Paid"}:
        abort(400, description="Invalid payment status.")
    conn = get_db()
    order = conn.execute(
        "SELECT payment_method FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if order is None:
        conn.close()
        abort(404)
    if order["payment_method"] != "Cash on Delivery":
        conn.close()
        abort(400, description="Online payment status can only be updated by its payment provider.")
    conn.execute(
        "UPDATE orders SET payment_status = ? WHERE id = ?",
        (payment_status, order_id),
    )
    conn.commit()
    conn.close()
    flash(f"Payment status for order #{order_id} updated.")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
