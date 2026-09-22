import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from sandbox_api.models import Inventory, Order, Payment, Product

PRODUCTS = (
    (1, "Mechanical Keyboard", "79.99"),
    (2, "USB-C Dock", "129.50"),
    (3, "Webcam", "49.00"),
)

INVENTORY = (
    (1, 10),
    (2, 5),
    (3, 0),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL,
    total TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT OR IGNORE INTO products (id, name, price) VALUES (?, ?, ?)",
                PRODUCTS,
            )
            connection.executemany(
                "INSERT OR IGNORE INTO inventory (product_id, quantity) VALUES (?, ?)",
                INVENTORY,
            )

    def get_product(self, product_id: int) -> Product | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, price FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()
        return Product(**dict(row)) if row else None

    def get_inventory(self, product_id: int) -> Inventory | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT product_id, quantity FROM inventory WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        return Inventory(**dict(row)) if row else None

    def update_inventory(self, product_id: int, quantity: int) -> Inventory | None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE inventory SET quantity = ? WHERE product_id = ?",
                (quantity, product_id),
            )
            if cursor.rowcount == 0:
                return None
        return Inventory(product_id=product_id, quantity=quantity)

    def create_order(self, product_id: int, quantity: int) -> Order:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            product = connection.execute(
                "SELECT price FROM products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if product is None:
                raise LookupError("Product not found")

            inventory = connection.execute(
                "SELECT quantity FROM inventory WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if inventory is None:
                raise LookupError("Inventory not found")
            if inventory["quantity"] < quantity:
                raise ValueError("Insufficient inventory")

            total = Decimal(product["price"]) * quantity
            cursor = connection.execute(
                """
                INSERT INTO orders (product_id, quantity, status, total)
                VALUES (?, ?, ?, ?)
                """,
                (product_id, quantity, "created", str(total)),
            )
            connection.execute(
                "UPDATE inventory SET quantity = quantity - ? WHERE product_id = ?",
                (quantity, product_id),
            )
            order_id = cursor.lastrowid

        return Order(
            id=order_id,
            product_id=product_id,
            quantity=quantity,
            status="created",
            total=total,
        )

    def get_order(self, order_id: int) -> Order | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, product_id, quantity, status, total
                FROM orders
                WHERE id = ?
                """,
                (order_id,),
            ).fetchone()
        return Order(**dict(row)) if row else None

    def create_payment(self, order_id: int) -> Payment | None:
        with self.connect() as connection:
            order = connection.execute(
                "SELECT total FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            if order is None:
                return None

            amount = Decimal(order["total"])
            cursor = connection.execute(
                """
                INSERT INTO payments (order_id, amount, status)
                VALUES (?, ?, ?)
                """,
                (order_id, str(amount), "succeeded"),
            )
            payment_id = cursor.lastrowid

        return Payment(
            id=payment_id,
            order_id=order_id,
            amount=amount,
            status="succeeded",
        )
