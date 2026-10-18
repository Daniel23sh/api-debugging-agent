"""Conservative decision-level checks; uncertainty is left for semantic review."""

import re
from uuid import UUID

from agent_service.agent.dispatch import TOOL_REGISTRY, prepare_tool_call
from agent_service.agent.schemas import Observation, ToolCallDecision, ToolCallRecord
from agent_service.tools.contracts import HttpMethod
from agent_service.tools.endpoints import normalize_approved_endpoint
from evaluation.contracts import (
    ArgumentsLabel,
    DecisionRecord,
    EvaluationRun,
    EvaluatorName,
    EvaluatorResult,
    ResultState,
    SelectionLabel,
)
from evaluation.dataset import EvaluationCase


def _result(
    evaluator: EvaluatorName,
    case: EvaluationCase,
    run: EvaluationRun,
    decision: DecisionRecord,
    explanation: str,
    label: SelectionLabel | ArgumentsLabel | None = None,
    *,
    state: ResultState = ResultState.UNDETERMINED,
) -> EvaluatorResult:
    return EvaluatorResult(
        evaluator=evaluator,
        unit="decision",
        case_id=case.case_id,
        session_id=run.session_id,
        trace_id=run.trace_id,
        span_id=decision.span_id,
        decision_id=decision.decision_id,
        state=ResultState.SCORED if label is not None else state,
        label=label,
        explanation=explanation,
    )


def _check_links(
    case: EvaluationCase, run: EvaluationRun, decision: DecisionRecord
) -> None:
    if case.case_id != run.case_id:
        raise ValueError("case and run identifiers differ")
    if not any(item == decision for item in run.decisions):
        raise ValueError("decision is not part of the run")


def _prior(run: EvaluationRun, decision: DecisionRecord) -> list[Observation]:
    ids = set(decision.prior_observation_ids)
    return [item.observation for item in run.observations if item.observation_id in ids]


def _known_request_ids(
    case: EvaluationCase, observations: list[Observation]
) -> set[str]:
    ids = {
        str(UUID(match))
        for match in re.findall(
            r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b",
            case.user_report,
        )
    }
    for observation in observations:
        if (
            observation.tool_name == "execute_api_request"
            and observation.success
            and observation.request_id
        ):
            try:
                ids.add(str(UUID(observation.request_id)))
            except ValueError:
                pass
    return ids


def _has_runtime_failure(observations: list[Observation]) -> bool:
    return any(
        item.tool_name == "execute_api_request"
        and item.success
        and isinstance(item.data, dict)
        and isinstance(item.data.get("status_code"), int)
        and item.data["status_code"] >= 500
        for item in observations
    )


def evaluate_tool_selection(
    case: EvaluationCase, run: EvaluationRun, decision: DecisionRecord
) -> EvaluatorResult:
    """Score only clear contextual choices, independently of parameter validity."""
    _check_links(case, run, decision)
    evaluator = EvaluatorName.TOOL_SELECTION

    def result(explanation: str, label: SelectionLabel | None = None, **kwargs):
        return _result(evaluator, case, run, decision, explanation, label, **kwargs)

    call = decision.decision
    if call is None:
        return result("The interrupted decision did not return an action.")
    if not isinstance(call, ToolCallDecision):
        return result(
            "No tool selection was returned.", state=ResultState.NOT_APPLICABLE
        )
    if call.tool_name not in TOOL_REGISTRY:
        return result(
            "The selected tool is not registered.", SelectionLabel.INVALID_TOOL
        )

    observations = _prior(run, decision)
    if case.endpoint is None and not observations and not _known_request_ids(case, []):
        return result(
            "The report provides no operation or request ID to investigate.",
            SelectionLabel.UNNECESSARY,
        )

    successful = [item for item in observations if item.success]
    if call.tool_name == "inspect_server_logs":
        if _known_request_ids(case, observations) and (
            _has_runtime_failure(observations)
            or re.search(
                r"\b5\d\d\b|internal server error", case.user_report, re.IGNORECASE
            )
        ):
            return result(
                "A known request ID and runtime failure support inspecting logs.",
                SelectionLabel.APPROPRIATE,
            )
        return result(
            "Log relevance requires semantic review of the available context."
        )

    if (
        call.tool_name == "inspect_api_spec"
        and case.endpoint is not None
        and not any(item.tool_name == call.tool_name for item in successful)
    ):
        return result(
            "The reported operation has not yet had its contract inspected.",
            SelectionLabel.APPROPRIATE,
        )
    if (
        call.tool_name == "execute_api_request"
        and case.reproduction_request is not None
        and not any(item.tool_name == call.tool_name for item in successful)
    ):
        return result(
            "The reported behavior has not yet been reproduced by HTTP.",
            SelectionLabel.APPROPRIATE,
        )

    # Stable read-only inspection can be redundant; repeated HTTP may change state.
    if call.tool_name in {"inspect_api_spec", "inspect_endpoint_implementation"}:
        prepared = prepare_tool_call(call)
        if isinstance(prepared, ToolCallRecord):
            method, path = prepared.arguments["method"], prepared.arguments["path"]
            route = normalize_approved_endpoint(HttpMethod(method), path)
            if route is not None and any(
                item.tool_name == call.tool_name
                and isinstance(item.data, dict)
                and item.data.get("method") == method
                and item.data.get("path") in {path, route}
                for item in successful
            ):
                return result(
                    "The same stable endpoint inspection is already available.",
                    SelectionLabel.UNNECESSARY,
                )
    return result(
        "Several investigative choices are possible; semantic review is needed."
    )


def evaluate_tool_arguments(
    case: EvaluationCase, run: EvaluationRun, decision: DecisionRecord
) -> EvaluatorResult:
    """Validate permission boundaries and known requests without business validation."""
    _check_links(case, run, decision)
    evaluator = EvaluatorName.TOOL_ARGUMENTS

    def result(explanation: str, label: ArgumentsLabel | None = None, **kwargs):
        return _result(evaluator, case, run, decision, explanation, label, **kwargs)

    call = decision.decision
    if call is None:
        return result("The interrupted decision did not return arguments.")
    if not isinstance(call, ToolCallDecision) or call.tool_name not in TOOL_REGISTRY:
        return result(
            "No registered tool arguments are available.",
            state=ResultState.NOT_APPLICABLE,
        )
    prepared = prepare_tool_call(call)
    if not isinstance(prepared, ToolCallRecord):
        return result("Arguments violate the tool schema.", ArgumentsLabel.INCORRECT)
    args = prepared.arguments
    if call.tool_name == "inspect_server_logs":
        request_id = args["request_id"]
        if request_id in _known_request_ids(case, _prior(run, decision)):
            return result(
                "The request ID comes from the report or an earlier HTTP observation.",
                ArgumentsLabel.CORRECT,
            )
        return result(
            "The request ID is not present in the report or earlier HTTP observations.",
            ArgumentsLabel.INCORRECT,
        )

    method, path = HttpMethod(args["method"]), args["path"]
    route = normalize_approved_endpoint(method, path)
    if route is None or (
        call.tool_name == "execute_api_request" and ("{" in path or "}" in path)
    ):
        return result(
            "The endpoint is not permitted for this tool.", ArgumentsLabel.INCORRECT
        )

    requests = [*case.setup_requests]
    if case.reproduction_request is not None:
        requests.append(case.reproduction_request)
    if call.tool_name == "execute_api_request":
        if any(
            method.value == request.method.value
            and path == request.path
            and args["body"] == request.body
            for request in requests
        ):
            return result(
                "Arguments match a canonical setup or reproduction request.",
                ArgumentsLabel.CORRECT,
            )
    elif any(
        method.value == request.method.value
        and route
        == normalize_approved_endpoint(HttpMethod(request.method.value), request.path)
        for request in requests
    ):
        return result(
            "Arguments inspect the contract or implementation of a case operation.",
            ArgumentsLabel.CORRECT,
        )
    return result("The approved exploratory request is not a canonical case request.")
