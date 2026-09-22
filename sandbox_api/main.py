from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status

from sandbox_api.db import Database
from sandbox_api.models import (
    Inventory,
    InventoryUpdate,
    Order,
    OrderCreate,
    Payment,
    PaymentCreate,
    Product,
)


def create_app(database_path: str | Path = "sandbox.db") -> FastAPI:
    database = Database(database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(title="APILens Sandbox API", lifespan=lifespan)

    @app.get("/products/{product_id}", response_model=Product)
    def get_product(product_id: int) -> Product:
        product = database.get_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product

    @app.patch("/inventory/{product_id}", response_model=Inventory)
    def update_inventory(product_id: int, update: InventoryUpdate) -> Inventory:
        inventory = database.update_inventory(product_id, update.quantity)
        if inventory is None:
            raise HTTPException(status_code=404, detail="Inventory not found")
        return inventory

    @app.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED)
    def create_order(order: OrderCreate) -> Order:
        try:
            return database.create_order(order.product_id, order.quantity)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/orders/{order_id}", response_model=Order)
    def get_order(order_id: int) -> Order:
        order = database.get_order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order

    @app.post(
        "/payments",
        response_model=Payment,
        status_code=status.HTTP_201_CREATED,
    )
    def create_payment(payment: PaymentCreate) -> Payment:
        created_payment = database.create_payment(payment.order_id)
        if created_payment is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return created_payment

    return app


app = create_app()
