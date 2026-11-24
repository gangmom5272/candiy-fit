import uuid
from datetime import date, timedelta

from candiy_pipeline.targets import target_for_member, target_for_test_user
from candiy_pipeline.windows import chunks, query_range

TODAY = date(2026, 10, 18)


def test_initial_range_is_35_days():
    assert query_range(TODAY, initial=True, full=False) == (date(2026, 9, 14), TODAY)


def test_recent_range_is_3_days():
    assert query_range(TODAY, initial=False, full=False) == (date(2026, 10, 16), TODAY)


def test_full_range_is_35_days():
    assert query_range(TODAY, initial=False, full=True) == (date(2026, 9, 14), TODAY)


def test_chunks_cover_range_with_one_day_overlap():
    start, end = query_range(TODAY, initial=True, full=False)
    parts = chunks(start, end)
    assert parts[0][0] == start and parts[-1][1] == end
    assert len(parts) == 6
    for (_, prev_end), (next_start, _) in zip(parts, parts[1:], strict=False):
        assert next_start == prev_end


def test_record_crossing_midnight_fits_in_some_chunk():
    # CANDiY는 시작·종료가 모두 구간 안인 기록만 준다. 모든 날 밤 → 다음 날 아침 기록이 어느 구간엔가 들어가야 한다
    start, end = query_range(TODAY, initial=True, full=False)
    parts = chunks(start, end)
    day = start
    while day < end:
        next_day = date.fromordinal(day.toordinal() + 1)
        assert any(s <= day and next_day <= e for s, e in parts), day
        day = next_day


def test_short_range_is_one_chunk():
    assert chunks(date(2026, 10, 16), TODAY) == [(date(2026, 10, 16), TODAY)]


def test_exact_chunk_size_is_one_chunk():
    assert chunks(date(2026, 10, 12), TODAY) == [(date(2026, 10, 12), TODAY)]


def test_test_user_uuid_is_stable():
    a = target_for_test_user("mock-1013")
    assert a.candiy_id == "mock-1013"
    assert a.member_id == target_for_test_user("mock-1013").member_id
    assert a.member_id != target_for_test_user("mock-1014").member_id


def test_test_user_uuid_is_canonical_v4():
    # 백엔드 토큰은 canonical UUIDv4 회원 ID만 받는다 (S15P31A403-126)
    member_id = target_for_test_user("synth-01").member_id
    assert member_id.version == 4
    assert uuid.UUID(str(member_id)) == member_id and str(member_id) == str(member_id).lower()


def test_member_uses_same_id_for_both():
    member_id = uuid.uuid4()
    t = target_for_member(member_id)
    assert t.member_id == member_id and t.candiy_id == str(member_id)


def test_only_real_member_is_member():
    # 우리 수집 앱 기록(예제 앱과 같은 패키지명)은 실제 회원만 적재한다 (S15P31A403-231)
    assert target_for_member(uuid.uuid4()).is_member
    assert not target_for_test_user("mock-1013").is_member


def test_initial_days_can_be_longer_for_synthetic():
    # 합성 데이터만 90일 (S15P31A403-235). 평소 실행은 그대로 최근 며칠
    assert query_range(TODAY, initial=True, full=False, initial_days=90) == (TODAY - timedelta(days=89), TODAY)
    assert query_range(TODAY, initial=False, full=False, recent_days=2, initial_days=90) == (
        TODAY - timedelta(days=1),
        TODAY,
    )
