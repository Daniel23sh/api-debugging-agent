import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CaseKind(StrEnum):
    BUG = "bug"
    NO_BUG = "no_bug"
    INCONCLUSIVE = "inconclusive"


class BugCategory(StrEnum):
    VALIDATION_MISMATCH = "validation_mismatch"
    WRONG_STATUS_OR_RESPONSE = "wrong_status_or_response"
    BUSINESS_LOGIC = "business_logic"
    RUNTIME_EXCEPTION = "runtime_exception"
    FIELD_SCHEMA_MISMATCH = "field_schema_mismatch"


class ExpectedFinalStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NO_BUG_FOUND = "NO_BUG_FOUND"
    INCONCLUSIVE = "INCONCLUSIVE"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"


class ToolName(StrEnum):
    INSPECT_API_SPEC = "inspect_api_spec"
    EXECUTE_API_REQUEST = "execute_api_request"
    INSPECT_SERVER_LOGS = "inspect_server_logs"
    INSPECT_ENDPOINT_IMPLEMENTATION = "inspect_endpoint_implementation"


class HttpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    method: HttpMethod
    path: NonEmptyStr
    body: dict[str, JsonValue] | None = None


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    case_id: NonEmptyStr
    case_kind: CaseKind
    bug_id: NonEmptyStr | None = None
    user_report: NonEmptyStr
    endpoint: NonEmptyStr | None = None
    category: BugCategory | None = None
    expected_root_cause: NonEmptyStr | None = None
    expected_status: int | None = Field(default=None, ge=100, le=599)
    actual_status: int | None = Field(default=None, ge=100, le=599)
    required_evidence: list[NonEmptyStr] = Field(default_factory=list)
    acceptable_tools: set[ToolName] = Field(default_factory=set)
    forbidden_or_unnecessary_tools: set[ToolName] = Field(default_factory=set)
    expected_final_status: ExpectedFinalStatus
    fixture_id: NonEmptyStr
    paraphrase_group: NonEmptyStr | None = None
    setup_requests: list[HttpRequest] = Field(default_factory=list)
    reproduction_request: HttpRequest | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "EvaluationCase":
        if (self.expected_status is None) != (self.actual_status is None):
            raise ValueError(
                "expected_status and actual_status must be provided together"
            )

        overlap = self.acceptable_tools & self.forbidden_or_unnecessary_tools
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"tools cannot be both acceptable and forbidden: {names}")

        bug_ground_truth = (self.bug_id, self.category, self.expected_root_cause)
        if self.case_kind is CaseKind.BUG:
            if not all(bug_ground_truth):
                raise ValueError(
                    "bug cases require bug_id, category, and expected_root_cause"
                )
            if self.endpoint is None:
                raise ValueError("bug cases require endpoint")
            if self.reproduction_request is None:
                raise ValueError("bug cases require reproduction_request")
            if self.expected_final_status is not ExpectedFinalStatus.RESOLVED:
                raise ValueError("bug cases must expect RESOLVED")
        else:
            if any(value is not None for value in bug_ground_truth):
                raise ValueError(
                    "non-bug cases cannot include bug_id, category, or "
                    "expected_root_cause"
                )
            expected = (
                ExpectedFinalStatus.NO_BUG_FOUND
                if self.case_kind is CaseKind.NO_BUG
                else ExpectedFinalStatus.INCONCLUSIVE
            )
            if self.expected_final_status is not expected:
                raise ValueError(
                    f"{self.case_kind.value} cases must expect {expected.value}"
                )
            if (
                self.case_kind is CaseKind.NO_BUG
                and self.reproduction_request is None
            ):
                raise ValueError("no_bug cases require reproduction_request")

        return self


def load_evaluation_cases(path: str | Path) -> list[EvaluationCase]:
    with Path(path).open(encoding="utf-8") as dataset_file:
        records = json.load(dataset_file)

    cases = TypeAdapter(list[EvaluationCase]).validate_python(records)
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
    return cases
