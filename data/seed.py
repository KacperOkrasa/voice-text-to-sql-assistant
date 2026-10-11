"""Create a deterministic e-commerce demo database: python -m data.seed."""

import argparse
import random
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from src.db import DEFAULT_DB_PATH


SCHEMA = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0)
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid', 'shipped', 'delivered', 'cancelled')),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0),
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    UNIQUE (order_id, product_id)
);

CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_order_date ON orders(order_date);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);
"""

# Synthetic customers. example.com addresses are not real contact details.
CUSTOMERS = [
    (1, "Anna Kowalska", "anna.kowalska@example.com", "Warszawa", "2025-01-03"),
    (2, "Piotr Nowak", "piotr.nowak@example.com", "Kraków", "2025-01-05"),
    (3, "Zofia Wiśniewska", "zofia.wisniewska@example.com", "Gdańsk", "2025-01-08"),
    (4, "Michał Wójcik", "michal.wojcik@example.com", "Wrocław", "2025-01-11"),
    (5, "Julia Kamińska", "julia.kaminska@example.com", "Poznań", "2025-01-15"),
    (6, "Jakub Lewandowski", "jakub.lewandowski@example.com", "Łódź", "2025-01-18"),
    (7, "Aleksandra Zielińska", "aleksandra.zielinska@example.com", "Warszawa", "2025-01-21"),
    (8, "Tomasz Szymański", "tomasz.szymanski@example.com", "Katowice", "2025-01-24"),
    (9, "Natalia Woźniak", "natalia.wozniak@example.com", "Gdańsk", "2025-01-28"),
    (10, "Adam Dąbrowski", "adam.dabrowski@example.com", "Lublin", "2025-02-01"),
    (11, "Magdalena Kozłowska", "magdalena.kozlowska@example.com", "Kraków", "2025-02-03"),
    (12, "Mateusz Jankowski", "mateusz.jankowski@example.com", "Wrocław", "2025-02-06"),
    (13, "Karolina Mazur", "karolina.mazur@example.com", "Poznań", "2025-02-09"),
    (14, "Paweł Krawczyk", "pawel.krawczyk@example.com", "Szczecin", "2025-02-12"),
    (15, "Wiktoria Piotrowska", "wiktoria.piotrowska@example.com", "Warszawa", "2025-02-15"),
    (16, "Kamil Grabowski", "kamil.grabowski@example.com", "Łódź", "2025-02-18"),
    (17, "Martyna Pawłowska", "martyna.pawlowska@example.com", "Białystok", "2025-02-21"),
    (18, "Łukasz Michalski", "lukasz.michalski@example.com", "Gdańsk", "2025-02-23"),
    (19, "Olivia Smith", "olivia.smith@example.com", "Kraków", "2025-02-25"),
    (20, "Daniel Brown", "daniel.brown@example.com", "Warszawa", "2025-02-27"),
]

# All monetary values are PLN stored as integer grosze (1 PLN = 100 grosze).
PRODUCTS = [
    (1, "Wireless headphones", "Electronics", 24990),
    (2, "Mechanical keyboard", "Electronics", 32900),
    (3, "Wireless mouse", "Electronics", 12990),
    (4, "Laptop stand", "Office", 15900),
    (5, "Laptop backpack", "Accessories", 21990),
    (6, "USB-C hub", "Electronics", 14990),
    (7, "USB-C cable", "Electronics", 3990),
    (8, "Thermal bottle", "Accessories", 8990),
    (9, "A5 notebook", "Office", 2490),
    (10, "Desk lamp", "Office", 17990),
    (11, "Desk mat", "Office", 5990),
    (12, "Ceramic mug", "Accessories", 4990),
]


def create_demo_database(db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    """Create tables and synthetic records without overwriting an existing file.

    Seeded randomness produces repeatable orders for March-August 2025.
    A failed initialization removes only the newly created database file.
    """
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an accidental reset of an existing database.
    path.touch(exist_ok=False)

    rng = random.Random(42)
    first_order_date = date(2025, 3, 1)

    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA)

            with connection:
                connection.executemany(
                    """
                    INSERT INTO customers
                        (customer_id, full_name, email, city, registered_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    CUSTOMERS,
                )
                connection.executemany(
                    """
                    INSERT INTO products (product_id, name, category, price_cents)
                    VALUES (?, ?, ?, ?)
                    """,
                    PRODUCTS,
                )

                for order_id in range(1, 61):
                    # Two customers have no orders, useful for LEFT JOIN examples.
                    customer_id = ((order_id - 1) * 7) % 18 + 1
                    order_date = first_order_date + timedelta(days=(order_id - 1) * 3)
                    status = rng.choices(
                        ["paid", "shipped", "delivered", "cancelled"],
                        weights=[10, 15, 65, 10],
                        k=1,
                    )[0]
                    connection.execute(
                        """
                        INSERT INTO orders (order_id, customer_id, order_date, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        (order_id, customer_id, order_date.isoformat(), status),
                    )

                    selected_products = rng.sample(PRODUCTS, k=rng.randint(1, 4))
                    for product in selected_products:
                        product_id, _, _, price_cents = product
                        quantity = rng.randint(1, 3)
                        discount_percent = rng.choice([0, 0, 0, 5, 10])
                        unit_price_cents = price_cents * (100 - discount_percent) // 100
                        connection.execute(
                            """
                            INSERT INTO order_items
                                (order_id, product_id, quantity, unit_price_cents)
                            VALUES (?, ?, ?, ?)
                            """,
                            (order_id, product_id, quantity, unit_price_cents),
                        )
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return path


def main() -> None:
    """Create the demo database at the default path or a supplied --db-path."""
    parser = argparse.ArgumentParser(description="Utwórz bazę demo e-commerce.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    try:
        path = create_demo_database(args.db_path)
    except FileExistsError:
        parser.exit(status=1, message="Baza już istnieje. Wybierz inną ścieżkę przez --db-path.\n")

    print(f"Utworzono bazę demo: {path}")


if __name__ == "__main__":
    main()
