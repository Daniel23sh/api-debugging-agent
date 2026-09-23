from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
JsonObject = dict[str, JsonValue]


class EvidenceSource(StrEnum):
    API_SPEC = "api_spec"
    API_EXECUTION = "api_execution"
    SERVER_LOGS = "server_logs"
    ENDPOINT_IMPLEMENTATION = "endpoint_implementation"


class DiagnosisStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NO_BUG_FOUND = "NO_BUG_FOUND"
    INCONCLUSIVE = "INCONCLUSIVE"
    LIMIT_REACHED = "LIMIT_REACHED"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: EvidenceSource
    finding: NonEmptyStr
    reference: NonEmptyStr | None = None


class DebugDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DiagnosisStatus
    affected_endpoint: NonEmptyStr | None = None
    root_cause: NonEmptyStr | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_evidence: list[NonEmptyStr] = Field(default_factory=list)
    suggested_fix: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_status_semantics(self) -> "DebugDiagnosis":
        if self.status is DiagnosisStatus.RESOLVED:
            if not all(
                (
                    self.affected_endpoint,
                    self.root_cause,
                    self.evidence,
                    self.suggested_fix,
                )
            ):
                raise ValueError(
                    "RESOLVED requires an endpoint, root cause, evidence, and fix"
                )
        elif self.status is DiagnosisStatus.NO_BUG_FOUND:
            if not self.evidence:
                raise ValueError("NO_BUG_FOUND requires evidence")
            if self.root_cause is not None or self.suggested_fix is not None:
                raise ValueError("NO_BUG_FOUND cannot include a root cause or fix")
        elif self.status is DiagnosisStatus.INCONCLUSIVE:
            if not self.missing_evidence:
                raise ValueError("INCONCLUSIVE requires missing evidence")
            if self.root_cause is not None:
                raise ValueError("INCONCLUSIVE cannot include a root cause")
        elif not self.evidence and not self.missing_evidence:
            raise ValueError("LIMIT_REACHED requires evidence or missing evidence")
        return self


class ToolCallDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["tool_call"]
    tool_name: NonEmptyStr
    arguments: JsonObject


class FinalAnswerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["final_answer"]
    diagnosis: DebugDiagnosis


AgentDecision = Annotated[
    ToolCallDecision | FinalAnswerDecision,
    Field(discriminator="kind"),
]


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    tool_name: NonEmptyStr
    arguments: JsonObject


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    tool_name: NonEmptyStr
    success: bool
    data: JsonObject | None = None
    error: JsonObject | None = None
    request_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> "Observation":
        if self.success:
            if self.data is None or self.error is not None:
                raise ValueError("successful observations require data and no error")
        elif self.data is not None or not self.error:
            raise ValueError("failed observations require an error and no data")
        return self
