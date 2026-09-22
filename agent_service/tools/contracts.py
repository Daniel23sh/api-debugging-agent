from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"


class ToolErrorType(StrEnum):
    ENDPOINT_NOT_ALLOWED = "ENDPOINT_NOT_ALLOWED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_HTTP_ERROR = "TOOL_HTTP_ERROR"
    TOOL_DATABASE_ERROR = "TOOL_DATABASE_ERROR"
    LOG_NOT_FOUND = "LOG_NOT_FOUND"
    SOURCE_RESOLUTION_ERROR = "SOURCE_RESOLUTION_ERROR"


DataT = TypeVar("DataT")


class ToolResult(BaseModel, Generic[DataT]):
    model_config = ConfigDict(extra="forbid")

    success: bool
    tool_name: str
    data: DataT | None = None
    error_type: ToolErrorType | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> "ToolResult[DataT]":
        if self.success:
            if self.data is None or self.error_type is not None or self.error_message:
                raise ValueError("successful tool results require data and no error")
        elif self.data is not None or self.error_type is None or not self.error_message:
            raise ValueError("failed tool results require an error and no data")
        return self
