"""요일 기준일 집계(eval/weekday_base_dates.py) 테스트. 모의 측정 결과를 쓰고 LLM 을 호출하지 않는다."""

import json
from datetime import date

import pytest

from eval import weekday_base_dates as wb

V12 = wb.DATASETS_DIR / "tool_selection_v1.2.jsonl"
V22 = wb.DATASETS_DIR / "tool_selection_ai_period_v2.2.jsonl"
ROW = {"dataset": "weekday_items", "category": "tool"}
SAT_STEPS = {**ROW, "id": "synth-04-304", "question": "지난 토요일에 몇 걸음 걸었어?"}
WED_HEART = {**ROW, "id": "synth-05-304", "question": "지난 수요일 심박 평균 알려 줘"}
OTHER = {**ROW, "id": "synth-01-304", "question": "지난주 수요일 걸음 알려 줘"}


def record(base: dict, run: int, correct, period: str | None = None, **extra) -> dict:
    """측정 기록 한 줄. period 가 있으면 걸음 기간 요약 호출 하나를 담는다."""
    calls = [] if period is None else [{"name": "get_metric_summary", "args": {"metric": "steps", "period": period}}]
    error = None if correct else ("llm_error" if correct is None else "wrong_args")
    return {**base, "run": run, "correct": correct, "error": error, "detail": {}, "calls": calls, **extra}


def test_subset_copies_scored_last_weekday_items_verbatim():
    chosen = wb.subset_lines()
    assert [key for key, _ in chosen] == [
        "tool_selection_v1.2/synth-06-019",
        "tool_selection_v1.2/synth-07-008",
        "tool_selection_ai_period_v2.2/synth-07-304",
        "tool_selection_ai_period_v2.2/synth-04-304",
        "tool_selection_ai_period_v2.2/synth-05-304",
        "tool_selection_ai_period_v2.2/synth-10-304",
    ]
    sources = V12.read_text(encoding="utf-8").splitlines() + V22.read_text(encoding="utf-8").splitlines()
    assert all(line in sources for _, line in chosen)
    # 채점하지 않는 proposed 와 "지난주 X요일"·날짜를 함께 말한 문항은 옮기지 않는다
    assert all(json.loads(line)["category"] == "tool" for _, line in chosen)


def test_write_subset_writes_one_line_per_item(tmp_path):
    path = tmp_path / "weekday" / "items.jsonl"
    keys = wb.write_subset(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(keys) == 6
    assert [json.loads(line)["id"] for line in lines] == [key.split("/")[1] for key in keys]


def test_base_dates_are_one_week_without_dataset_day():
    assert [d.weekday() for d in wb.BASE_DATES] == list(range(7))
    assert date(2026, 10, 22) not in wb.BASE_DATES and {d.month for d in wb.BASE_DATES} == {10}
    assert wb.REPEAT == 10


@pytest.mark.parametrize(
    ("rec", "form"),
    [
        (record(SAT_STEPS, 1, True, "weekday:sat"), "correct"),
        (record(SAT_STEPS, 1, None), "error"),
        (record(SAT_STEPS, 1, False, "weeks_ago:0"), "this_week"),
        (record(SAT_STEPS, 1, False, "this_week"), "this_week"),
        (record(SAT_STEPS, 1, False, "weeks_ago:0.sat"), "this_week_day"),
        (record(SAT_STEPS, 1, False, "weeks_ago:0.sun"), "other"),
        (record(SAT_STEPS, 1, False, "weeks_ago:1.sat"), "other"),
        (record(SAT_STEPS, 1, False, "date:10-17"), "direct_date"),
        (record(SAT_STEPS, 1, False, "dates:10-16..10-18"), "direct_date"),
        (record(SAT_STEPS, 1, False, "weeks_ago:1"), "other"),
        (record(SAT_STEPS, 1, False), "other"),
        (
            record(
                SAT_STEPS, 1, False,
                calls=[{"name": "compare_periods", "args": {"metric": "steps", "current": "weeks_ago:0",
                                                            "baseline": "weeks_ago:1"}}],
            ),
            "this_week",
        ),
        (record(SAT_STEPS, 1, False, calls=[{"name": "get_metric_summary", "args": "{oops"}]), "other"),
    ],
)  # fmt: skip
def test_form_of_splits_wrong_runs_by_period(rec, form):
    assert wb.form_of(rec) == form


@pytest.mark.parametrize(
    ("asked", "base", "pos"),
    [
        ("wed", date(2026, 10, 15), "passed"),
        ("thu", date(2026, 10, 15), "today"),
        ("sat", date(2026, 10, 15), "ahead"),
        ("sat", date(2026, 10, 18), "passed"),
        ("sat", date(2026, 10, 17), "today"),
        ("sun", date(2026, 10, 12), "ahead"),
    ],
)
def test_position_compares_weekdays_within_monday_week(asked, base, pos):
    assert wb.position(asked, base) == pos


def measurement(today: str, records: list[dict], **fields) -> tuple[dict, list[dict]]:
    report = {
        "measured_at": f"{today}T10:00:00", "commit": "abc1234", "today": today, "prompt_version": "v1.10",
        "prompt_sha256": "7d3f14b189a1", "tools_sha256": "0b229962e04b", "repeat": 2, "period_scoring": "range",
        "temperature": None, "datasets": [{"name": "weekday_items", "sha256": "f" * 64}], **fields,
    }  # fmt: skip
    return report, records


THURSDAY = measurement(
    "2026-10-15",
    [
        record(SAT_STEPS, 1, False, "weeks_ago:0"),
        record(SAT_STEPS, 2, False, "date:10-10"),
        record(WED_HEART, 1, True, "weekday:wed"),
        record(WED_HEART, 2, False, "weeks_ago:1"),
        record(OTHER, 1, False, "weeks_ago:0"),
    ],
)
SUNDAY = measurement(
    "2026-10-18",
    [
        record(SAT_STEPS, 1, True, "days_ago:1"),
        record(SAT_STEPS, 2, False, "weeks_ago:0"),
        record(WED_HEART, 1, None),
        record(WED_HEART, 2, True, "weekday:wed"),
    ],
)


def test_aggregate_counts_forms_per_base_date_and_asked_weekday():
    cells = wb.aggregate([THURSDAY, SUNDAY])
    # "지난주 X요일" 문항(OTHER)은 세지 않는다
    assert list(cells) == [
        (date(2026, 10, 15), "wed"), (date(2026, 10, 15), "sat"), (date(2026, 10, 18), "wed"),
        (date(2026, 10, 18), "sat"),
    ]  # fmt: skip
    thursday_sat = cells[(date(2026, 10, 15), "sat")]
    assert (thursday_sat.runs, thursday_sat.counts["this_week"], thursday_sat.counts["direct_date"]) == (2, 1, 1)
    assert thursday_sat.wrong_periods == {"weeks_ago:0": 1, "date:10-10": 1}
    assert cells[(date(2026, 10, 15), "wed")].counts == {"correct": 1, "other": 1}
    assert cells[(date(2026, 10, 18), "wed")].counts == {"error": 1, "correct": 1}
    assert cells[(date(2026, 10, 18), "sat")].counts == {"correct": 1, "this_week": 1}


def test_by_position_separates_weekday_not_yet_come_from_passed():
    table = wb.by_position(wb.aggregate([THURSDAY, SUNDAY]))
    # 목요일에 토요일은 아직 안 왔고, 일요일에 토요일은 이번 주에 지났다. 수요일은 두 기준일 모두 지났다
    assert list(table) == [("wed", "passed"), ("sat", "passed"), ("sat", "ahead")]
    assert table[("sat", "ahead")] == {"this_week": 1, "direct_date": 1}
    assert table[("sat", "passed")] == {"correct": 1, "this_week": 1}
    assert table[("wed", "passed")] == {"correct": 2, "other": 1, "error": 1}


def test_markdown_shows_this_week_rate_table_and_cells():
    md = wb.to_markdown([THURSDAY, SUNDAY])
    assert "| 기준일 | 지난 수요일 | 지난 토요일 |" in md
    assert "| 2026-10-15(목) | 0/2 (0%) | 1/2 (50%) |" in md
    assert "| 2026-10-18(일) | 0/2 (0%) | 1/2 (50%) |" in md
    assert "| 실행 | 맞음 | 이번 주 전체 | 이번 주 그 요일 | 날짜 직접 | 그 밖 | 오류 |" in md
    assert "| 지난 토요일 | 이번 주에 아직 안 옴 | 2 | 0 | 1 | 0 | 1 | 0 | 0 |" in md
    assert "| 2026-10-15(목) | 토 | 이번 주에 아직 안 옴 | synth-04-304 | 2 | 0 | 1 | 0 | 1 | 0 | 0 |" in md
    assert "`weeks_ago:0` 1, `date:10-10` 1" in md
    assert "측정끼리 다른 조건" not in md


def test_this_week_day_is_counted_apart_from_whole_week():
    thursday = measurement(
        "2026-10-15",
        [
            record(SAT_STEPS, 1, False, "weeks_ago:0.sat"),
            record(SAT_STEPS, 2, False, "weeks_ago:0"),
            record(WED_HEART, 1, False, "weeks_ago:0.sat"),
        ],
    )
    cells = wb.aggregate([thursday])
    assert cells[(date(2026, 10, 15), "sat")].counts == {"this_week_day": 1, "this_week": 1}
    assert cells[(date(2026, 10, 15), "sat")].wrong_periods == {"weeks_ago:0.sat": 1, "weeks_ago:0": 1}
    # 묻는 요일이 아닌 이번 주 요일은 그 밖이다
    assert cells[(date(2026, 10, 15), "wed")].counts == {"other": 1}
    assert wb.by_position(cells)[("sat", "ahead")] == {"this_week_day": 1, "this_week": 1}

    md = wb.to_markdown([thursday])
    # 이번 주 전체 비율표에는 이번 주 그 요일을 넣지 않는다
    assert "| 2026-10-15(목) | 0/1 (0%) | 1/2 (50%) |" in md
    assert "| 2026-10-15(목) | 토 | 이번 주에 아직 안 옴 | synth-04-304 | 2 | 0 | 1 | 1 | 0 | 0 | 0 |" in md
    cell = next(c for c in wb.to_json([thursday])["cells"] if c["asked"] == "sat")
    assert (cell["this_week"], cell["this_week_day"], cell["other"]) == (1, 1, 0)


def test_markdown_warns_when_measurement_conditions_differ():
    changed = measurement("2026-10-18", SUNDAY[1], prompt_sha256="000000000000")
    md = wb.to_markdown([THURSDAY, changed])
    assert "- 측정끼리 다른 조건이 있다: prompt_sha256. 칸끼리 비교하기 전에 확인한다" in md


def save_measurement(folder, stamp: str, m: tuple[dict, list[dict]]):
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / f"{stamp}_tool_selection.json"
    report_path.write_text(json.dumps(m[0], ensure_ascii=False), encoding="utf-8")
    records_path = folder / f"{stamp}_tool_selection_records.jsonl"
    records_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in m[1]), encoding="utf-8")
    return report_path, records_path


def test_report_reads_saved_measurements_and_saves_md_json(tmp_path, capsys):
    thursday, _ = save_measurement(tmp_path / "2026-10-15", "20261104-1000", THURSDAY)
    _, sunday_records = save_measurement(tmp_path / "2026-10-18", "20261104-1000", SUNDAY)
    out = tmp_path / "out"
    wb.main(["report", str(thursday), str(sunday_records), "--save", "--out", str(out)])

    printed = capsys.readouterr().out
    assert (out / "weekday_base_dates.md").read_text(encoding="utf-8") == printed
    body = json.loads((out / "weekday_base_dates.json").read_text(encoding="utf-8"))
    assert [m["today"] for m in body["measurements"]] == ["2026-10-15", "2026-10-18"]
    cell = next(c for c in body["cells"] if (c["base"], c["asked"]) == ("2026-10-15", "sat"))
    assert (cell["base_weekday"], cell["position"], cell["runs"], cell["this_week"]) == ("thu", "ahead", 2, 1)
    assert body["differences"] == []


def test_report_stops_when_pair_file_is_missing(tmp_path):
    report_path, records_path = save_measurement(tmp_path, "20261104-1000", THURSDAY)
    records_path.unlink()
    with pytest.raises(SystemExit, match="같은 측정의 파일이 없습니다"):
        wb.main(["report", str(report_path)])


def test_subset_command_prints_saved_items(tmp_path, capsys):
    path = tmp_path / "items.jsonl"
    wb.main(["subset", "--out", str(path)])
    out = capsys.readouterr().out
    assert f"저장: {path} (6문항)" in out and "- tool_selection_v1.2/synth-06-019" in out
