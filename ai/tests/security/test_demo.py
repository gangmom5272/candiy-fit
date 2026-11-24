import json

import pytest

from coach_ai.security.demo import CASES, main, run_demo
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE

pytestmark = pytest.mark.security

EXPECTED = [
    ("allow", None),
    ("allow", None),
    ("deny", "unknown_tool"),
    ("deny", "extra_field"),
    ("deny", "invalid_args"),
    ("deny", "invalid_args"),
    ("allow", None),
    ("deny", "duplicate"),
    *[("allow", None)] * 6,
    ("deny", "call_limit"),
    ("deny", "owner_mismatch"),
    ("allow", "timeout"),
    ("allow", "user_not_linked"),
]


async def test_demo_prints_one_row_per_call_and_the_call_log(tmp_path):
    path = tmp_path / "tool_calls.jsonl"
    report = await run_demo(path, timeout=0.05)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(r["decision"], r["reason"]) for r in records] == EXPECTED
    assert len(records) == sum(len(case.calls) for case in CASES)

    table = [line for line in report.splitlines() if line.startswith("| ")][2:]
    assert len(table) == len(records)
    assert sum(DENIED_MESSAGE in row for row in table) == 5
    # 호출 횟수 한도 거부만 사유가 있는 문장을 받는다
    assert [CALL_LIMIT_MESSAGE in row for row in table] == [r["reason"] == "call_limit" for r in records]
    for code in ("forbidden", "data_unavailable", "user_not_linked"):
        assert f"(요청 종료: {code})" in report
    assert str(path) in report and records[0]["request_id"] in report


def test_demo_refuses_to_append_to_existing_log(tmp_path):
    path = tmp_path / "tool_calls.jsonl"
    path.write_text("old\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--log", str(path)])
    assert path.read_text(encoding="utf-8") == "old\n"
