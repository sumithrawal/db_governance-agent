-- Small, realistic seeded schema for testing the governance tools.
--
-- Includes:
--   - real foreign keys across 8 tables
--   - an intentionally duplicate index (orders_customer_id_idx /
--     orders_customer_id_idx2) for unused_objects duplicate-index detection
--   - one table (legacy_notes) that is never queried below, for
--     unused-object detection
--   - a handful of SELECTs against the other tables to generate
--     pg_stat_user_tables activity before you ever call the API

CREATE TABLE categories (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE
);

CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
);
CREATE INDEX products_category_id_idx ON products (category_id);

CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Intentional duplicate: both index the same leading column on the same table.
CREATE INDEX orders_customer_id_idx ON orders (customer_id);
CREATE INDEX orders_customer_id_idx2 ON orders (customer_id);

CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);
CREATE INDEX order_items_order_id_idx ON order_items (order_id);
CREATE INDEX order_items_product_id_idx ON order_items (product_id);

CREATE TABLE payments (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    method      TEXT NOT NULL,
    paid_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payments_order_id_idx ON payments (order_id);

CREATE TABLE shipping_addresses (
    id              SERIAL PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(id),
    address_line1   TEXT NOT NULL,
    city            TEXT NOT NULL,
    postal_code     TEXT NOT NULL,
    country         TEXT NOT NULL
);
CREATE INDEX shipping_addresses_customer_id_idx ON shipping_addresses (customer_id);

-- Never queried by the SELECTs below or by any application code — a
-- deliberately "unused" table for testing unused_objects detection.
-- Note: no PRIMARY KEY/UNIQUE constraint on purpose. Building such a
-- constraint's index requires a heap scan, which would itself bump
-- pg_stat_user_tables.seq_scan and make the table look "active" from DDL
-- alone, before any query ever touched it.
CREATE TABLE legacy_notes (
    id          SERIAL,
    note        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- Data ---

INSERT INTO categories (name) VALUES
    ('Electronics'), ('Books'), ('Home & Kitchen');

INSERT INTO products (category_id, sku, name, price_cents) VALUES
    (1, 'ELEC-001', 'Wireless Mouse', 2499),
    (1, 'ELEC-002', 'USB-C Hub', 3999),
    (2, 'BOOK-001', 'Postgres Internals', 4599),
    (2, 'BOOK-002', 'Designing Data-Intensive Applications', 5299),
    (3, 'HOME-001', 'French Press', 3299);

INSERT INTO customers (email, name) VALUES
    ('alice@example.com', 'Alice Johnson'),
    ('bob@example.com', 'Bob Nguyen'),
    ('carol@example.com', 'Carol Smith');

INSERT INTO orders (customer_id, status) VALUES
    (1, 'completed'),
    (1, 'completed'),
    (2, 'pending'),
    (3, 'completed');

INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents) VALUES
    (1, 1, 1, 2499),
    (1, 3, 2, 4599),
    (2, 2, 1, 3999),
    (3, 5, 1, 3299),
    (4, 4, 1, 5299);

INSERT INTO payments (order_id, amount_cents, method) VALUES
    (1, 11697, 'card'),
    (2, 3999, 'card'),
    (4, 5299, 'paypal');

INSERT INTO shipping_addresses (customer_id, address_line1, city, postal_code, country) VALUES
    (1, '1 Market St', 'San Francisco', '94105', 'US'),
    (2, '221B Baker St', 'London', 'NW1', 'UK'),
    (3, '10 Rue de Rivoli', 'Paris', '75001', 'FR');

-- --- Generate query activity so pg_stat_user_tables shows these tables as
--     "used" — legacy_notes is deliberately left untouched. ---

SELECT count(*) FROM products;
SELECT * FROM products WHERE category_id = 1;
SELECT * FROM customers WHERE email = 'alice@example.com';
SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id;
SELECT * FROM order_items WHERE order_id = 1;
SELECT * FROM payments WHERE method = 'card';
SELECT * FROM shipping_addresses WHERE country = 'US';
SELECT * FROM categories;
