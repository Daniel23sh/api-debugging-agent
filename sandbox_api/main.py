from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

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

REQUEST_ID_HEADER = "X-Request-ID"


def create_app(database_path: str | Path = "sandbox.db") -> FastAPI:
    database = Database(database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(title="APILens Sandbox API", lifespan=lifespan)

    @app.middleware("http")
    async def correlate_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid4())
        method = request.method
        path = request.url.path
        database.add_request_log(
            request_id=request_id,
            event_type="request_received",
            method=method,
            path=path,
        )

        try:
            response = await call_next(request)
        except Exception as error:  # noqa: BLE001 - log every unhandled request error
            database.add_request_log(
                request_id=request_id,
                event_type="request_failed",
                method=method,
                path=path,
                status_code=500,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"},
                headers={REQUEST_ID_HEADER: request_id},
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        database.add_request_log(
            request_id=request_id,
            event_type="response_sent",
            method=method,
            path=path,
            status_code=response.status_code,
            error_type="HTTP_ERROR" if response.status_code >= 400 else None,
        )
        return response

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
