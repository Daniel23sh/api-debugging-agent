import importlib

import pytest
from pydantic import ValidationError

from agent_service.tools.contracts import ToolErrorType
from agent_service.tools.inspect_endpoint_implementation import (
    MAX_SOURCE_CHARS,
    TRUSTED_SOURCE_TARGETS,
    InspectEndpointImplementationArgs,
    inspect_endpoint_implementation,
)
from sandbox_api.ground_truth import BUG_GROUND_TRUTH
from sandbox_api.source_map import APPROVED_SOURCE_MAP, SourceSection


def test_input_contains_only_method_and_path() -> None:
    args = InspectEndpointImplementationArgs(method="get", path=" /products/1 ")
    assert args.method.value == "GET"
    assert args.path == "/products/1"

    forbidden_inputs = [
        {"filename": "sandbox_api/main.py"},
        {"module": "sandbox_api.main"},
        {"line_start": 1},
        {"section": SourceSection.GET_PRODUCT_ROUTE},
        {"object_name": "get_product"},
    ]
    for forbidden in forbidden_inputs:
        with pytest.raises(ValidationError):
            InspectEndpointImplementationArgs(
                method="GET",
                path="/products/1",
                **forbidden,
            )


@pytest.mark.parametrize(
    ("method", "path", "expected_path"),
    [
        ("GET", "/products/{product_id}", "/products/{product_id}"),
        ("GET", "/products/1", "/products/{product_id}"),
        ("PATCH", "/inventory/1", "/inventory/{product_id}"),
        ("GET", "/orders/1", "/orders/{order_id}"),
        ("POST", "/orders", "/orders"),
    ],
)
def test_approved_templates_and_concrete_paths_are_inspectable(
    method: str,
    path: str,
    expected_path: str,
) -> None:
    result = inspect_endpoint_implementation(
        InspectEndpointImplementationArgs(method=method, path=path)
    )

    assert result.success is True
    assert result.data is not None
    assert result.data.path == expected_path
    assert result.data.sections


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/products/1"),
        ("GET", "/admin/debug"),
        ("GET", "/etc/passwd"),
    ],
)
def test_unsupported_endpoints_return_endpoint_not_allowed(
    method: str,
    path: str,
) -> None:
    result = inspect_endpoint_implementation(
        InspectEndpointImplementationArgs(method=method, path=path)
    )

    assert result.success is False
    assert result.data is None
    assert result.error_type is ToolErrorType.ENDPOINT_NOT_ALLOWED


def test_returned_identities_match_source_map_and_every_section_resolves() -> None:
    resolved: set[SourceSection] = set()
    for (method, path), expected_sections in APPROVED_SOURCE_MAP.items():
        result = inspect_endpoint_implementation(
            InspectEndpointImplementationArgs(method=method, path=path)
        )
        assert result.data is not None
        actual_sections = tuple(section.section for section in result.data.sections)
        assert actual_sections == expected_sections
        resolved.update(actual_sections)

    assert resolved == set(SourceSection) == set(TRUSTED_SOURCE_TARGETS)


def test_route_database_and_model_sources_are_returned() -> None:
    result = inspect_endpoint_implementation(
        InspectEndpointImplementationArgs(method="POST", path="/orders")
    )
    assert result.data is not None
    sections = {section.section: section.source for section in result.data.sections}

    assert "@app.post" in sections[SourceSection.CREATE_ORDER_ROUTE]
    assert "def create_order" in sections[SourceSection.CREATE_ORDER_ROUTE]
    assert 'inventory["quantity"]' in sections[SourceSection.CREATE_ORDER_DATABASE]
    assert "class OrderCreate" in sections[SourceSection.ORDER_CREATE_MODEL]


def test_source_output_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module(
        "agent_service.tools.inspect_endpoint_implementation"
    )
    monkeypatch.setattr(
        module.inspect,
        "getsource",
        lambda _: "x" * (MAX_SOURCE_CHARS + 10),
    )

    result = inspect_endpoint_implementation(
        InspectEndpointImplementationArgs(method="POST", path="/orders")
    )

    assert result.data is not None
    assert all(
        len(section.source) == MAX_SOURCE_CHARS for section in result.data.sections
    )
    assert all(section.truncated for section in result.data.sections)


def test_source_resolution_failure_is_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(
        "agent_service.tools.inspect_endpoint_implementation"
    )

    def fail(_: object) -> str:
        raise OSError("internal source location must not leak")

    monkeypatch.setattr(module.inspect, "getsource", fail)
    result = inspect_endpoint_implementation(
        InspectEndpointImplementationArgs(method="GET", path="/products/1")
    )

    assert result.success is False
    assert result.error_type is ToolErrorType.SOURCE_RESOLUTION_ERROR
    assert "internal source location" not in result.error_message


def test_every_seeded_bug_endpoint_has_implementation_evidence() -> None:
    for bug in BUG_GROUND_TRUTH:
        result = inspect_endpoint_implementation(
            InspectEndpointImplementationArgs(
                method=bug.method,
                path=bug.request_path,
            )
        )
        assert result.success is True
        assert result.data is not None
        assert result.data.sections
