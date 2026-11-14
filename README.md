# Habi Jabi

Flask marketplace with a 100+ product catalog, separate customer, seller, and platform-admin accounts, seller-owned product listings, SQLite-backed orders, and bKash tokenized checkout.

## Run locally

```sh
python3 -m pip install -r requirements.txt
export SECRET_KEY="use-a-long-random-value"
export ADMIN_EMAIL="store-admin@example.com"
export ADMIN_PASSWORD="use-a-unique-admin-password"
python3 app.py
```

The first startup creates the SQLite database, preserves existing records, and seeds the catalog to at least 100 items. The platform admin account is provisioned from `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Customers use **Join**; sellers use **Sell on Habi Jabi** to create their own account and publish products. Each seller can edit or remove only their own listings.

## bKash setup

Obtain merchant API credentials from bKash and set them in the server environment. Do not put credentials in source files or browser code.

```sh
export BKASH_APP_KEY="your-app-key"
export BKASH_APP_SECRET="your-app-secret"
export BKASH_USERNAME="your-merchant-username"
export BKASH_PASSWORD="your-merchant-password"
export PUBLIC_BASE_URL="http://127.0.0.1:5000"
```

The app defaults to bKash's sandbox API. `PUBLIC_BASE_URL` is required for bKash checkout so the payment callback always points to a configured origin; localhost HTTP is accepted for local sandbox testing. For production, set `BKASH_BASE_URL` to the production API base supplied for your merchant account, set `PUBLIC_BASE_URL` to the site's public HTTPS origin, and use `COOKIE_SECURE=true`. Payment is marked paid only after the server executes or queries the bKash payment and verifies the returned transaction, amount, and currency.

The sandbox needs sandbox merchant credentials; checkout stays unavailable until all four bKash credentials are configured. Cash on delivery remains available.
