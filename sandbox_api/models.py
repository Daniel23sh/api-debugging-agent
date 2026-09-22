from decimal import Decimal

from pydantic import BaseModel, Field


class Product(BaseModel):
    id: int
    name: str
    price: Decimal


class Inventory(BaseModel):
    product_id: int
    quantity: int


class InventoryUpdate(BaseModel):
    quantity: int = Field(ge=0)


class OrderCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class Order(BaseModel):
    id: int
    product_id: int
    quantity: int
    status: str
    total: Decimal


class PaymentCreate(BaseModel):
    order_id: int


class Payment(BaseModel):
    id: int
    order_id: int
    amount: Decimal
    status: str


class RequestLog(BaseModel):
    id: int
    request_id: str
    event_type: str
    method: str
    path: str
    status_code: int | None = None
    error_type: str | None = None
    error_message: str | None = None
