from enum import StrEnum
from typing import Final


class SourceSection(StrEnum):
    PRODUCT_MODEL = "model:Product"
    INVENTORY_UPDATE_MODEL = "model:InventoryUpdate"
    ORDER_CREATE_MODEL = "model:OrderCreate"
    ORDER_MODEL = "model:Order"
    PAYMENT_CREATE_MODEL = "model:PaymentCreate"
    GET_PRODUCT_ROUTE = "route:get_product"
    UPDATE_INVENTORY_ROUTE = "route:update_inventory"
    CREATE_ORDER_ROUTE = "route:create_order"
    GET_ORDER_ROUTE = "route:get_order"
    CREATE_PAYMENT_ROUTE = "route:create_payment"
    GET_PRODUCT_DATABASE = "database:Database.get_product"
    UPDATE_INVENTORY_DATABASE = "database:Database.update_inventory"
    CREATE_ORDER_DATABASE = "database:Database.create_order"
    GET_ORDER_DATABASE = "database:Database.get_order"
    CREATE_PAYMENT_DATABASE = "database:Database.create_payment"


APPROVED_SOURCE_MAP: Final[
    dict[tuple[str, str], tuple[SourceSection, ...]]
] = {
    ("GET", "/products/{product_id}"): (
        SourceSection.GET_PRODUCT_ROUTE,
        SourceSection.GET_PRODUCT_DATABASE,
        SourceSection.PRODUCT_MODEL,
    ),
    ("PATCH", "/inventory/{product_id}"): (
        SourceSection.UPDATE_INVENTORY_ROUTE,
        SourceSection.UPDATE_INVENTORY_DATABASE,
        SourceSection.INVENTORY_UPDATE_MODEL,
    ),
    ("POST", "/orders"): (
        SourceSection.CREATE_ORDER_ROUTE,
        SourceSection.CREATE_ORDER_DATABASE,
        SourceSection.ORDER_CREATE_MODEL,
    ),
    ("GET", "/orders/{order_id}"): (
        SourceSection.GET_ORDER_ROUTE,
        SourceSection.GET_ORDER_DATABASE,
        SourceSection.ORDER_MODEL,
    ),
    ("POST", "/payments"): (
        SourceSection.CREATE_PAYMENT_ROUTE,
        SourceSection.CREATE_PAYMENT_DATABASE,
        SourceSection.PAYMENT_CREATE_MODEL,
    ),
}


def get_source_sections(
    method: str,
    path: str,
) -> tuple[SourceSection, ...] | None:
    return APPROVED_SOURCE_MAP.get((method.upper(), path))
