from typing import Annotated, Literal
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.routing import ApiRoute
from app.core.schemas import ApiRequest, DataResponse
from app.core.validation import validation_details


class ProfileRequest(ApiRequest):
    birth_year: int = Field(ge=1900)
    height_cm: float = Field(gt=0)
    gender: Literal["MALE", "FEMALE"]


class QueryParams(BaseModel):
    page_size: int = Field(alias="pageSize", default=1)


class TextRequest(ApiRequest):
    text: str


@pytest.fixture
def strict_routes(app):
    router = APIRouter(prefix="/api/v1/probe", route_class=ApiRoute)

    @router.post("")
    async def profile(body: ProfileRequest):
        return DataResponse(data=body.model_dump(by_alias=True))

    @router.post("/text")
    async def text(body: TextRequest):
        return DataResponse(data=body.model_dump())

    @router.get("")
    async def listing(limit: int = 10):
        return DataResponse(data=limit)

    @router.get("/model")
    async def modeled(query: Annotated[QueryParams, Query()]):
        return DataResponse(data=query.page_size)

    async def query_dependency(cursor: str | None = None):
        return cursor

    @router.get("/dependency")
    async def with_dependency(cursor=Depends(query_dependency)):
        return DataResponse(data=cursor)

    @router.get("/{item_id}")
    async def item(item_id: UUID):
        return DataResponse(data=item_id)

    app.include_router(router)


async def test_valid_public_alias_and_queries(client, strict_routes):
    result = await client.post(
        "/api/v1/probe", json={"birthYear": 2000, "heightCm": 165.2, "gender": "FEMALE"}
    )
    assert result.status_code == 200
    assert result.json()["data"]["birthYear"] == 2000
    assert (await client.get("/api/v1/probe?limit=2")).json() == {"data": 2}
    assert (await client.get("/api/v1/probe/model?pageSize=3")).json() == {"data": 3}
    assert (await client.get("/api/v1/probe/dependency?cursor=abc")).json() == {
        "data": "abc"
    }


@pytest.mark.parametrize(
    ("raw", "field", "reason"),
    [
        ('{"a":1,"a":2}', "a", "duplicate"),
        ('{"a":[{"b":1,"b":2}]}', "a[0].b", "duplicate"),
        ('{"a":NaN}', "body", "invalid_json"),
        ('{"a":Infinity}', "body", "invalid_json"),
        ('{"a":-Infinity}', "body", "invalid_json"),
        ('{"a":1e999}', "body", "invalid_json"),
        ("{", "body", "invalid_json"),
        (" ", "body", "invalid_json"),
        ("null", "body", "null_not_allowed"),
        ("[]", "body", "invalid_type"),
        ('"synthetic-secret"', "body", "invalid_type"),
    ],
)
async def test_strict_json(client, strict_routes, raw, field, reason):
    response = await client.post(
        "/api/v1/probe", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    assert "synthetic-secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "raw",
    [
        rb'{"\ud800":1,"\ud800":2}',
        rb'{"\udfff":1}',
        rb'{"outer":[{"\ud800":1}]}',
        rb'{"text":"\ud800"}',
        rb'{"text":"\udfff"}',
        rb'{"outer":["\ud800"]}',
        rb'"\udfff"',
        b'{"text":"\xff"}',
    ],
)
async def test_invalid_unicode_is_safe_json_error(client, strict_routes, caplog, raw):
    response = await client.post(
        "/api/v1/probe/text", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "INVALID_REQUEST",
        "message": "요청 내용을 확인해 주세요.",
        "details": [{"field": "body", "reason": "invalid_json"}],
    }
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert not caplog.records


@pytest.mark.parametrize(
    "raw",
    [
        rb'{"text":"\ud83d\ude00"}',
        '{"text":"😀"}'.encode(),
    ],
)
async def test_valid_unicode_surrogate_pair_and_utf8_are_preserved(
    client, strict_routes, raw
):
    response = await client.post(
        "/api/v1/probe/text", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"text": "😀"}}


@pytest.mark.parametrize("value", [True, "2000", 2000.5])
async def test_numeric_types_are_strict(client, strict_routes, value):
    response = await client.post(
        "/api/v1/probe", json={"birthYear": value, "heightCm": 165, "gender": "MALE"}
    )
    assert response.json()["error"]["details"] == [
        {"field": "birthYear", "reason": "invalid_type"}
    ]


async def test_multiple_field_errors_and_no_snake_alias(client, strict_routes):
    response = await client.post(
        "/api/v1/probe", json={"birth_year": 2000, "heightCm": None, "gender": "OTHER"}
    )
    assert {(d["field"], d["reason"]) for d in response.json()["error"]["details"]} == {
        ("birthYear", "missing"),
        ("birth_year", "extra_forbidden"),
        ("heightCm", "null_not_allowed"),
        ("gender", "invalid_choice"),
    }


async def test_unknown_null_and_disallowed_null_keep_distinct_reasons(
    client, strict_routes
):
    response = await client.post(
        "/api/v1/probe",
        json={"birthYear": 2000, "heightCm": None, "gender": "FEMALE", "extra": None},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert {(d["field"], d["reason"]) for d in response.json()["error"]["details"]} == {
        ("heightCm", "null_not_allowed"),
        ("extra", "extra_forbidden"),
    }


@pytest.mark.parametrize(
    ("url", "field", "reason"),
    [
        ("?limit=1&limit=2", "limit", "duplicate"),
        ("?userId=synthetic-secret", "userId", "extra_forbidden"),
        ("/model?pageSize=1&pageSize=2", "pageSize", "duplicate"),
        ("/model?other=1", "other", "extra_forbidden"),
        ("/dependency?cursor=a&cursor=b", "cursor", "duplicate"),
        ("/bad-uuid", "item_id", "invalid_format"),
    ],
)
async def test_query_and_path_errors(client, strict_routes, url, field, reason):
    response = await client.get("/api/v1/probe" + url)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    assert "synthetic-secret" not in response.text


@pytest.mark.parametrize("raw", [" ", "null", "{}"])
async def test_get_body_forbidden(client, strict_routes, raw):
    response = await client.request("GET", "/api/v1/probe", content=raw)
    assert response.json()["error"]["details"] == [
        {"field": "body", "reason": "extra_forbidden"}
    ]


@pytest.mark.parametrize(
    "content_type", [None, "text/plain", "application/problem+json"]
)
async def test_json_content_type_required(client, strict_routes, content_type):
    headers = {"Content-Type": content_type} if content_type else {}
    response = await client.post("/api/v1/probe", content="{}", headers=headers)
    assert response.json()["error"]["details"][0]["reason"] == "invalid_format"


def test_custom_reasons_and_safe_unknown_errors():
    details = validation_details(
        [
            {
                "type": "decimal_places",
                "loc": ("body", "heightCm"),
                "input": 1.111,
                "ctx": {"secret": "hidden"},
            },
            {
                "type": "unrecognized",
                "loc": ("body",),
                "input": "hidden",
                "msg": "hidden",
            },
        ]
    )
    assert [d.reason for d in details] == ["decimal_places", "invalid_format"]
    assert "hidden" not in str(details)
