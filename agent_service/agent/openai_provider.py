"""OpenAI Responses translation for one APILens debug session."""

import json
import os
from typing import Literal

from openai import (
    APIError,
    AsyncOpenAI,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
    pydantic_function_tool,
)
from pydantic import ValidationError

from agent_service.agent.dispatch import TOOL_REGISTRY
from agent_service.agent.loop import DecisionProviderError
from agent_service.agent.schemas import (
    AgentDecision,
    DebugDiagnosis,
    FinalAnswerDecision,
    ToolCallDecision,
)
from agent_service.agent.state import DebugSessionState

SYSTEM_INSTRUCTIONS = """Investigate before concluding. Use a tool only when it adds useful evidence.
Base each diagnosis on observed evidence. Continue when evidence is insufficient and
a useful action remains; stop when enough evidence exists. Return INCONCLUSIVE
instead of inventing details. Avoid unnecessary repeated calls. Do not modify code
or system state. LIMIT_REACHED is application-controlled; never select it."""
CORRECTION = "The previous output could not be accepted. Return exactly one valid tool call or one structured final diagnosis under the same contracts."
MAX_FUNCTION_OUTPUT_CHARS = 20_000

TOOL_DESCRIPTIONS = {
    "inspect_api_spec": "Read the documented request and response contract for an approved endpoint.",
    "execute_api_request": "Run one approved Sandbox HTTP request and observe its response and request ID.",
    "inspect_server_logs": "Read server logs for a request ID returned by an API execution.",
    "inspect_endpoint_implementation": "Read the approved source sections for an endpoint.",
}


class _ModelDiagnosis(DebugDiagnosis):
    status: Literal["RESOLVED", "NO_BUG_FOUND", "INCONCLUSIVE"]


class _InvalidModelOutput(ValueError):
    pass


def _tools() -> tuple[list[dict], dict[str, set[str]]]:
    tools: list[dict] = []
    encoded_fields: dict[str, set[str]] = {}
    for name, registration in TOOL_REGISTRY.items():
        function = pydantic_function_tool(
            registration.args_model,
            name=name,
            description=TOOL_DESCRIPTIONS[name],
        )["function"]
        parameters = function["parameters"]
        encoded_fields[name] = set()
        for field, schema in parameters["properties"].items():
            variants = schema.get("anyOf", [schema])
            if any(
                variant.get("type") == "object"
                and isinstance(variant.get("additionalProperties"), dict)
                for variant in variants
            ):
                # Strict schemas cannot generate arbitrary object keys. Keep the
                # Pydantic Args model authoritative after decoding the JSON string.
                encoded_fields[name].add(field)
                nullable = any(variant.get("type") == "null" for variant in variants)
                parameters["properties"][field] = {
                    "type": ["string", "null"] if nullable else "string",
                    "description": "JSON-encoded object" + (" or null" if nullable else ""),
                }
        definitions = parameters.get("$defs", {})
        used = json.dumps(parameters["properties"])
        parameters["$defs"] = {
            key: value
            for key, value in definitions.items()
            if f"#/$defs/{key}" in used
        }
        if not parameters["$defs"]:
            del parameters["$defs"]
        tools.append({
            "type": "function",
            "name": name,
            "description": function["description"],
            "parameters": parameters,
            "strict": True,
        })
    return tools, encoded_fields


def _function_output(state: DebugSessionState, start: int) -> str:
    observations = [item.model_dump(mode="json") for item in state.observations[start:]]
    if not observations:
        raise DecisionProviderError(
            "MODEL_CONTEXT_ERROR", "No observation exists for the pending tool call"
        )
    output = json.dumps({"observations": observations}, ensure_ascii=False)
    if len(output) > MAX_FUNCTION_OUTPUT_CHARS:
        for item in observations:
            if item["data"] is not None:
                item["data"] = {"truncated": True}
            if item["error"] and isinstance(item["error"].get("message"), str):
                item["error"]["message"] = item["error"]["message"][:500]
        output = json.dumps(
            {"observations": observations, "data_truncated": True},
            ensure_ascii=False,
        )
        if len(output) > MAX_FUNCTION_OUTPUT_CHARS:
            observations = [{
                "tool_name": item["tool_name"][:100],
                "success": item["success"],
                "request_id": item["request_id"][:100] if item["request_id"] else None,
                "error": {
                    "type": item["error"].get("type"),
                    "message": str(item["error"].get("message", ""))[:500],
                } if item["error"] else None,
                "data": {"truncated": True} if item["data"] is not None else None,
            } for item in observations]
            output = json.dumps(
                {"observations": observations, "data_truncated": True},
                ensure_ascii=False,
            )
    return output


class OpenAIDecisionProvider:
    """Translate Responses output into internal decisions; never execute tools."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self._client = client if client is not None else AsyncOpenAI(max_retries=0)
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-6-sol")
        self.reasoning_effort = reasoning_effort or os.getenv(
            "OPENAI_REASONING_EFFORT", "medium"
        )
        self._tools, self._encoded_fields = _tools()
        self._session_id: str | None = None
        self._response_id: str | None = None
        self._pending_call_id: str | None = None
        self._observation_cursor = 0

    async def next_decision(self, state: DebugSessionState) -> AgentDecision:
        if self._session_id is None:
            self._session_id = state.session_id
        elif self._session_id != state.session_id:
            raise DecisionProviderError(
                "MODEL_CONTEXT_ERROR", "Decision provider belongs to another session"
            )

        previous_id = self._response_id
        if self._pending_call_id is None:
            input_data: str | list[dict] = state.issue
        else:
            input_data = [{
                "type": "function_call_output",
                "call_id": self._pending_call_id,
                "output": _function_output(state, self._observation_cursor),
            }]

        for correction_attempt in range(2):
            try:
                request = {
                    "model": self.model,
                    "reasoning": {"effort": self.reasoning_effort},
                    "instructions": SYSTEM_INSTRUCTIONS,
                    "input": input_data,
                    "tools": self._tools,
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                    "text_format": _ModelDiagnosis,
                    "store": True,
                }
                if previous_id is not None:
                    request["previous_response_id"] = previous_id
                response = await self._client.responses.parse(**request)
            except APIError as error:
                raise DecisionProviderError(
                    "MODEL_API_ERROR", "OpenAI API request failed"
                ) from error
            except (
                ValidationError,
                json.JSONDecodeError,
                LengthFinishReasonError,
                ContentFilterFinishReasonError,
            ):
                response = None

            # The function output has now been supplied; an invalid response
            # is corrected in the same model conversation without changing state.
            self._pending_call_id = None
            try:
                if response is None:
                    raise _InvalidModelOutput("Structured output could not be parsed")
                decision = self._decision_from_response(response)
            except (ValidationError, _InvalidModelOutput, json.JSONDecodeError):
                if correction_attempt:
                    raise DecisionProviderError(
                        "MODEL_OUTPUT_VALIDATION_ERROR",
                        "Model did not return one valid decision after correction",
                    ) from None
                if isinstance(input_data, str):
                    input_data = [
                        {"role": "user", "content": input_data},
                        {"role": "user", "content": CORRECTION},
                    ]
                else:
                    input_data = [*input_data, {"role": "user", "content": CORRECTION}]
                continue

            self._response_id = response.id
            if isinstance(decision, ToolCallDecision):
                self._pending_call_id = next(
                    item.call_id for item in response.output
                    if item.type == "function_call"
                )
                self._observation_cursor = len(state.observations)
            return decision

        raise AssertionError("Corrective loop must return or raise")

    def _decision_from_response(self, response: object) -> AgentDecision:
        if getattr(response, "status", None) != "completed" or not isinstance(
            getattr(response, "id", None), str
        ):
            raise _InvalidModelOutput("Response is incomplete")
        output = getattr(response, "output", None)
        if not isinstance(output, list):
            raise _InvalidModelOutput("Response has no output items")
        actions = [item for item in output if getattr(item, "type", None) != "reasoning"]
        if len(actions) != 1:
            raise _InvalidModelOutput("Expected exactly one action")
        action = actions[0]
        if action.type == "function_call":
            if not isinstance(getattr(action, "name", None), str) or not action.name:
                raise _InvalidModelOutput("Function call has no tool name")
            if not isinstance(getattr(action, "call_id", None), str) or not action.call_id:
                raise _InvalidModelOutput("Function call has no call ID")
            if not isinstance(getattr(action, "arguments", None), str):
                raise _InvalidModelOutput("Function arguments are not JSON text")
            arguments = json.loads(action.arguments)
            if not isinstance(arguments, dict):
                raise _InvalidModelOutput("Function arguments must be an object")
            for field in self._encoded_fields.get(action.name, ()):
                value = arguments.get(field)
                if value is not None:
                    if not isinstance(value, str):
                        raise _InvalidModelOutput("JSON-encoded argument is not text")
                    arguments[field] = json.loads(value)
                    if not isinstance(arguments[field], dict):
                        raise _InvalidModelOutput("JSON-encoded argument is not an object")
            return ToolCallDecision(
                kind="tool_call", tool_name=action.name, arguments=arguments
            )
        if action.type == "message":
            content = getattr(action, "content", None)
            if not isinstance(content, list) or len(content) != 1 or getattr(
                content[0], "type", None
            ) != "output_text":
                raise _InvalidModelOutput("Expected one structured final message")
            if getattr(response, "output_parsed", None) is None:
                raise _InvalidModelOutput("Structured final diagnosis is missing")
            parsed = _ModelDiagnosis.model_validate(response.output_parsed)
            return FinalAnswerDecision(
                kind="final_answer",
                diagnosis=DebugDiagnosis.model_validate(parsed.model_dump()),
            )
        raise _InvalidModelOutput("Unsupported response item")
