import re

from agent_service.tools.contracts import HttpMethod
from sandbox_api.source_map import APPROVED_SOURCE_MAP


def normalize_approved_endpoint(method: HttpMethod, path: str) -> str | None:
    # Reject alternate URL representations before matching the approved route.
    if re.search(r"[%\\?#\x00-\x1f\x7f-\x9f]", path):
        return None
    if any(segment in {".", ".."} for segment in path.split("/")):
        return None
    for approved_method, route_template in APPROVED_SOURCE_MAP:
        if approved_method != method.value:
            continue
        pattern = re.sub(r"\\\{[^/{}]+\\\}", "[^/]+", re.escape(route_template))
        if re.fullmatch(pattern, path):
            return route_template
    return None
