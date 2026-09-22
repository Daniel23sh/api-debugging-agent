import re

from agent_service.tools.contracts import HttpMethod
from sandbox_api.source_map import APPROVED_SOURCE_MAP


def normalize_approved_endpoint(method: HttpMethod, path: str) -> str | None:
    if "?" in path or "#" in path:
        return None
    for approved_method, route_template in APPROVED_SOURCE_MAP:
        if approved_method != method.value:
            continue
        pattern = re.sub(r"\\\{[^/{}]+\\\}", "[^/]+", re.escape(route_template))
        if re.fullmatch(pattern, path):
            return route_template
    return None
