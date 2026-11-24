"""공개 REST 라우트의 파싱 전 검증. 업무별 자격·값 검증은 호출자가 맡는다."""

from collections import Counter

from fastapi import Request
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.core.schemas import ErrorDetail
from app.core.validation import (
    ensure_no_body,
    invalid_field,
    invalid_request,
    strict_json,
)


class ApiRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        allowed_queries = set()
        dependants = [self.dependant]
        while dependants:
            dependant = dependants.pop()
            dependants.extend(dependant.dependencies)
            for field in dependant.query_params:
                annotation = field.field_info.annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    allowed_queries.update(
                        info.alias or name
                        for name, info in annotation.model_fields.items()
                    )
                else:
                    allowed_queries.add(field.alias)

        async def validated(request: Request):
            counts = Counter(key for key, _ in request.query_params.multi_items())
            details = []
            for key, count in counts.items():
                if key not in allowed_queries:
                    details.append(ErrorDetail(field=key, reason="extra_forbidden"))
                elif count > 1:
                    details.append(ErrorDetail(field=key, reason="duplicate"))
            if details:
                raise invalid_request(details)
            if request.method == "GET" or self.body_field is None:
                await ensure_no_body(request)
            else:
                raw = await request.body()
                if raw:
                    if (
                        request.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                        != "application/json"
                    ):
                        raise invalid_field("body", "invalid_format")
                    if strict_json(raw) is None:
                        raise invalid_field("body", "null_not_allowed")
            return await handler(request)

        return validated
