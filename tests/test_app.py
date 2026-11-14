import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch


_test_database = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_test_database.name, "store.db")
os.environ["ADMIN_EMAIL"] = "admin@example.test"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["BKASH_APP_KEY"] = "test-app-key"
os.environ["BKASH_APP_SECRET"] = "test-app-secret"
os.environ["BKASH_USERNAME"] = "test-merchant"
os.environ["BKASH_PASSWORD"] = "test-merchant-password"
os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:5000"

import app as store


class StoreAccountTests(unittest.TestCase):
    def setUp(self):
        conn = store.get_db()
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM sellers")
        conn.execute("DELETE FROM integration_tokens")
        conn.commit()
        conn.close()
        store.init_db()
        self.client = store.app.test_client()

    def csrf_token(self, client, page="/"):
        response = client.get(page)
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def register_customer(self, client=None):
        client = client or self.client
        token = self.csrf_token(client, "/register")
        response = client.post(
            "/register",
            data={
                "csrf_token": token,
                "name": "Test Customer",
                "email": "customer@example.test",
                "phone": "01700000000",
                "password": "test-password-123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My customer account", response.data)
        return client

    def register_seller(self, client=None, email="seller@example.test", store_name="Sample Store"):
        client = client or self.client
        token = self.csrf_token(client, "/seller/register")
        response = client.post(
            "/seller/register",
            data={
                "csrf_token": token,
                "name": "Test Seller",
                "store_name": store_name,
                "email": email,
                "phone": "01700000000",
                "password": "test-seller-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"seller panel", response.data)
        return client

    def add_first_product(self, client):
        token = self.csrf_token(client)
        product_id = store.get_db().execute("SELECT id FROM products LIMIT 1").fetchone()["id"]
        response = client.post(
            f"/add-to-cart/{product_id}",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

    def test_customer_registration_and_admin_are_separate(self):
        self.register_customer()
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.location)

        login_client = store.app.test_client()
        token = self.csrf_token(login_client, "/login")
        response = login_client.post(
            "/login",
            data={
                "csrf_token": token,
                "email": "customer@example.test",
                "password": "test-password-123",
            },
            follow_redirects=True,
        )
        self.assertIn(b"My customer account", response.data)

        admin_client = store.app.test_client()
        response = admin_client.get("/admin")
        self.assertEqual(response.status_code, 302)
        token = self.csrf_token(admin_client, "/admin/login")
        response = admin_client.post(
            "/admin/login",
            data={
                "csrf_token": token,
                "email": "admin@example.test",
                "password": "test-admin-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin dashboard", response.data)
        self.assertIn(b"customer@example.test", response.data)
        self.assertEqual(self.client.get("/account").status_code, 200)

    def test_catalog_starts_with_at_least_100_products(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        conn = store.get_db()
        product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(product_count, 100)

    def test_sellers_have_separate_accounts_and_only_manage_their_products(self):
        seller_client = self.register_seller()
        token = self.csrf_token(seller_client, "/seller/dashboard")
        response = seller_client.post(
            "/seller/dashboard",
            data={
                "csrf_token": token,
                "name": "Seller Exclusive Backpack",
                "category": "Fashion",
                "price": "2200",
                "old_price": "2800",
                "image": "",
                "description": "Listed by an independent seller.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Seller Exclusive Backpack", response.data)
        conn = store.get_db()
        product = conn.execute(
            "SELECT id, seller_id FROM products WHERE name = ?",
            ("Seller Exclusive Backpack",),
        ).fetchone()
        seller = conn.execute(
            "SELECT id FROM sellers WHERE email = ?", ("seller@example.test",)
        ).fetchone()
        self.assertEqual(product["seller_id"], seller["id"])
        conn.close()

        other_seller = store.app.test_client()
        self.register_seller(
            other_seller,
            email="other-seller@example.test",
            store_name="Another Store",
        )
        self.assertEqual(
            other_seller.get(f"/seller/product/{product['id']}").status_code,
            404,
        )
        self.assertEqual(
            other_seller.post(
                f"/seller/product/{product['id']}/delete",
                data={"csrf_token": self.csrf_token(other_seller, "/seller/dashboard")},
            ).status_code,
            404,
        )
        self.assertIn(
            b"Seller Exclusive Backpack",
            self.client.get("/").data,
        )

        logout_token = self.csrf_token(seller_client, "/seller/dashboard")
        seller_client.post("/seller/logout", data={"csrf_token": logout_token})
        login_token = self.csrf_token(seller_client, "/seller/login")
        response = seller_client.post(
            "/seller/login",
            data={
                "csrf_token": login_token,
                "email": "seller@example.test",
                "password": "test-seller-password",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Seller Exclusive Backpack", response.data)
        self.assertEqual(seller_client.get("/account").status_code, 302)

    def test_customer_can_update_profile_and_admin_can_edit_products(self):
        self.register_customer()
        token = self.csrf_token(self.client, "/account")
        response = self.client.post(
            "/account/profile",
            data={
                "csrf_token": token,
                "name": "Updated Customer",
                "email": "updated@example.test",
                "phone": "01800000000",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Updated Customer", response.data)

        admin_client = store.app.test_client()
        token = self.csrf_token(admin_client, "/admin/login")
        admin_client.post(
            "/admin/login",
            data={
                "csrf_token": token,
                "email": "admin@example.test",
                "password": "test-admin-password",
            },
        )
        token = self.csrf_token(admin_client, "/admin")
        response = admin_client.post(
            "/admin",
            data={
                "csrf_token": token,
                "name": "New product",
                "category": "Test",
                "price": "125",
                "old_price": "",
                "image": "",
                "description": "Original description",
            },
            follow_redirects=True,
        )
        self.assertIn(b"New product", response.data)
        conn = store.get_db()
        product_id = conn.execute(
            "SELECT id FROM products WHERE name = 'New product'"
        ).fetchone()["id"]
        conn.close()
        token = self.csrf_token(admin_client, f"/admin/product/{product_id}")
        response = admin_client.post(
            f"/admin/product/{product_id}",
            data={
                "csrf_token": token,
                "name": "Edited product",
                "category": "Test",
                "price": "150",
                "old_price": "200",
                "image": "https://example.test/product.png",
                "description": "Updated description",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Edited product", response.data)

    def test_csrf_required_for_cart_mutations(self):
        product_id = store.get_db().execute("SELECT id FROM products LIMIT 1").fetchone()["id"]
        response = self.client.post(f"/add-to-cart/{product_id}")
        self.assertEqual(response.status_code, 400)

    def test_existing_orders_are_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy_database = os.path.join(directory, "legacy.sqlite3")
            connection = sqlite3.connect(legacy_database)
            connection.execute(
                """CREATE TABLE products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price REAL NOT NULL,
                    old_price REAL,
                    image TEXT NOT NULL,
                    description TEXT
                )"""
            )
            connection.execute(
                """INSERT INTO products
                   (name, category, price, old_price, image, description)
                   VALUES ('Legacy product', 'Test', 50, 75, 'https://example.test/legacy.png', 'Kept product')"""
            )
            connection.execute(
                """CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    address TEXT NOT NULL,
                    payment_method TEXT NOT NULL,
                    total_amount REAL NOT NULL,
                    items TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """INSERT INTO orders
                   (customer_name, phone, address, payment_method, total_amount, items)
                   VALUES ('Legacy customer', '01700000000', 'Dhaka', 'Cash on Delivery', 100, 'Old item')"""
            )
            connection.commit()
            connection.close()

            original_database = store.DB_NAME
            store.DB_NAME = legacy_database
            try:
                store.init_db()
                conn = store.get_db()
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
                }
                order = conn.execute("SELECT * FROM orders WHERE id = 1").fetchone()
                self.assertTrue(
                    {"user_id", "status", "payment_status", "bkash_payment_id", "bkash_trx_id"}
                    <= columns
                )
                self.assertEqual(order["customer_name"], "Legacy customer")
                self.assertEqual(order["payment_status"], "Due")
                product = conn.execute(
                    "SELECT seller_id FROM products WHERE name = 'Legacy product'"
                ).fetchone()
                self.assertIsNone(product["seller_id"])
                self.assertGreaterEqual(
                    conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                    100,
                )
                conn.close()
            finally:
                store.DB_NAME = original_database

    def test_cash_on_delivery_order_is_owned_by_customer(self):
        self.register_customer()
        self.add_first_product(self.client)
        token = self.csrf_token(self.client, "/cart")
        response = self.client.post(
            "/checkout",
            data={
                "csrf_token": token,
                "customer_name": "Test Customer",
                "phone": "01700000000",
                "address": "Dhaka",
                "payment_method": "Cash on Delivery",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Order #", response.data)
        conn = store.get_db()
        order = conn.execute("SELECT * FROM orders").fetchone()
        user = conn.execute("SELECT id FROM users WHERE email = ?", ("customer@example.test",)).fetchone()
        self.assertEqual(order["user_id"], user["id"])
        self.assertEqual(order["payment_status"], "Due")
        self.assertEqual(order["status"], "Pending")
        conn.close()

        admin_client = store.app.test_client()
        token = self.csrf_token(admin_client, "/admin/login")
        admin_client.post(
            "/admin/login",
            data={
                "csrf_token": token,
                "email": "admin@example.test",
                "password": "test-admin-password",
            },
        )
        token = self.csrf_token(admin_client, "/admin")
        admin_client.post(
            f"/admin/order/{order['id']}/status",
            data={"csrf_token": token, "status": "Shipped"},
        )
        admin_client.post(
            f"/admin/order/{order['id']}/payment",
            data={"csrf_token": token, "payment_status": "Paid"},
        )
        conn = store.get_db()
        updated_order = conn.execute("SELECT status, payment_status FROM orders").fetchone()
        self.assertEqual(updated_order["status"], "Shipped")
        self.assertEqual(updated_order["payment_status"], "Paid")
        conn.close()

    def test_bkash_callback_marks_paid_only_after_server_verification(self):
        self.register_customer()
        self.add_first_product(self.client)

        def fake_bkash_request(path, payload):
            if path == "payment/create":
                return {
                    "paymentId": "PAY-TEST-1",
                    "bkashURL": "https://sandbox.payment.bkash.com/checkout",
                }
            conn = store.get_db()
            order = conn.execute("SELECT * FROM orders WHERE bkash_payment_id = ?", ("PAY-TEST-1",)).fetchone()
            conn.close()
            if path == "payment/execute":
                return {
                    "paymentId": "PAY-TEST-1",
                    "transactionStatus": "Completed",
                    "currency": "BDT",
                    "amount": str(order["total_amount"]),
                    "merchantInvoiceNumber": f"ORDER-{order['id']}",
                    "trxId": "TRX-TEST-1",
                }
            raise AssertionError(f"Unexpected bKash API request: {path}")

        token = self.csrf_token(self.client, "/cart")
        with patch.object(store, "bkash_request", side_effect=fake_bkash_request):
            response = self.client.post(
                "/checkout",
                data={
                    "csrf_token": token,
                    "customer_name": "Test Customer",
                    "phone": "01700000000",
                    "address": "Dhaka",
                    "payment_method": "bKash",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertIn("sandbox.payment.bkash.com", response.location)
            response = self.client.get(
                "/payments/bkash/callback?paymentID=PAY-TEST-1&status=success",
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Payment confirmed", response.data)
        conn = store.get_db()
        order = conn.execute("SELECT * FROM orders").fetchone()
        self.assertEqual(order["payment_status"], "Paid")
        self.assertEqual(order["bkash_trx_id"], "TRX-TEST-1")
        conn.close()

    def test_bkash_callback_rejects_mismatched_amount(self):
        self.register_customer()
        self.add_first_product(self.client)

        def fake_bkash_request(path, payload):
            if path == "payment/create":
                return {
                    "paymentId": "PAY-TEST-2",
                    "bkashURL": "https://sandbox.payment.bkash.com/checkout",
                }
            conn = store.get_db()
            order = conn.execute("SELECT * FROM orders WHERE bkash_payment_id = ?", ("PAY-TEST-2",)).fetchone()
            conn.close()
            if path == "payment/execute":
                return {
                    "paymentId": "PAY-TEST-2",
                    "transactionStatus": "Completed",
                    "currency": "BDT",
                    "amount": "1.00",
                    "merchantInvoiceNumber": f"ORDER-{order['id']}",
                    "trxId": "TRX-WRONG-AMOUNT",
                }
            if path == "query/payment":
                return {"paymentId": "PAY-TEST-2", "transactionStatus": "Initiated"}
            raise AssertionError(f"Unexpected bKash API request: {path}")

        token = self.csrf_token(self.client, "/cart")
        with patch.object(store, "bkash_request", side_effect=fake_bkash_request):
            self.client.post(
                "/checkout",
                data={
                    "csrf_token": token,
                    "customer_name": "Test Customer",
                    "phone": "01700000000",
                    "address": "Dhaka",
                    "payment_method": "bKash",
                },
            )
            response = self.client.get(
                "/payments/bkash/callback?paymentID=PAY-TEST-2&status=success",
                follow_redirects=True,
            )
        self.assertIn(b"Payment is not confirmed yet", response.data)
        conn = store.get_db()
        order = conn.execute("SELECT * FROM orders").fetchone()
        self.assertEqual(order["payment_status"], "Initiated")
        self.assertIsNone(order["bkash_trx_id"])
        conn.close()

    def test_bkash_api_reuses_and_refreshes_server_side_tokens(self):
        def response(data):
            mocked = Mock()
            mocked.raise_for_status.return_value = None
            mocked.json.return_value = data
            return mocked

        with patch.object(
            store.requests,
            "post",
            side_effect=[
                response(
                    {
                        "statusCode": "0000",
                        "id_token": "access-token-1",
                        "refresh_token": "refresh-token-1",
                        "expires_in": 3600,
                    }
                ),
                response({"paymentId": "PAY-TEST"}),
                response(
                    {
                        "statusCode": "0000",
                        "id_token": "access-token-2",
                        "refresh_token": "refresh-token-2",
                        "expires_in": 3600,
                    }
                ),
                response({"paymentId": "PAY-TEST", "transactionStatus": "Initiated"}),
            ],
        ) as api_post:
            result = store.bkash_request("payment/create", {"amount": "10.00"})
            self.assertEqual(result["paymentId"], "PAY-TEST")
            conn = store.get_db()
            conn.execute(
                "UPDATE integration_tokens SET expires_at = 0 WHERE provider = 'bkash'"
            )
            conn.commit()
            conn.close()
            result = store.bkash_request("query/payment", {"paymentId": "PAY-TEST"})
            self.assertEqual(result["transactionStatus"], "Initiated")

        self.assertEqual(api_post.call_count, 4)
        grant_call = api_post.call_args_list[0]
        self.assertTrue(grant_call.args[0].endswith("/auth/grant-token"))
        self.assertEqual(grant_call.kwargs["headers"]["username"], "test-merchant")
        create_call = api_post.call_args_list[1]
        self.assertEqual(create_call.kwargs["headers"]["Authorization"], "access-token-1")
        refresh_call = api_post.call_args_list[2]
        self.assertTrue(refresh_call.args[0].endswith("/auth/refresh-token"))
        self.assertEqual(refresh_call.kwargs["json"]["refresh_token"], "refresh-token-1")
        query_call = api_post.call_args_list[3]
        self.assertEqual(query_call.kwargs["headers"]["Authorization"], "access-token-2")


if __name__ == "__main__":
    unittest.main()
