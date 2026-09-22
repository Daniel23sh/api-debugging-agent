from pathlib import Path

from fastapi.testclient import TestClient

from sandbox_api.ground_truth import BUG_GROUND_TRUTH
from sandbox_api.main import create_app
from sandbox_api.source_map import (
    APPROVED_SOURCE_MAP,
    SourceSection,
    get_source_sections,
)


def test_source_map_matches_the_public_sandbox_surface(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "sandbox.db")) as client:
        paths = client.get("/openapi.json").json()["paths"]

    openapi_surface = {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
    }
    assert set(APPROVED_SOURCE_MAP) == openapi_surface


def test_source_map_includes_route_data_and_model_evidence() -> None:
    assert get_source_sections("PATCH", "/inventory/{product_id}") == (
        SourceSection.UPDATE_INVENTORY_ROUTE,
        SourceSection.UPDATE_INVENTORY_DATABASE,
        SourceSection.INVENTORY_UPDATE_MODEL,
    )
    assert get_source_sections("POST", "/orders") == (
        SourceSection.CREATE_ORDER_ROUTE,
        SourceSection.CREATE_ORDER_DATABASE,
        SourceSection.ORDER_CREATE_MODEL,
    )
    assert get_source_sections("GET", "/orders/{order_id}") == (
        SourceSection.GET_ORDER_ROUTE,
        SourceSection.GET_ORDER_DATABASE,
        SourceSection.ORDER_MODEL,
    )


def test_every_seeded_bug_endpoint_has_approved_source_sections() -> None:
    for bug in BUG_GROUND_TRUTH:
        assert get_source_sections(bug.method, bug.endpoint)


def test_unapproved_source_access_has_no_mapping() -> None:
    assert get_source_sections("POST", "/products/{product_id}") is None
    assert get_source_sections("GET", "/admin/debug") is None
    assert get_source_sections("GET", "/etc/passwd") is None
