"""조회 기간 (PIPE-02).

| 경우 | 기간 |
| 첫 연동 (initial_sync_done = false) | 오늘 기준 최근 35일 |
| 평소 | 최근 3일 |
| 전체 재조회 (--full, 하루 한 번) | 최근 35일 |

페이지네이션이 없으므로 7일씩 나눠 부른다.

구간은 하루씩 겹친다. CANDiY는 시작·종료가 모두 조회 기간 안에 있는 기록만 돌려주므로, 자정을 넘는 기록
(밤 수면, 23시~0시 심박 등)이 구간 경계에 걸리면 겹치지 않을 때 어느 구간에서도 오지 않는다
(data/docs/candiy-api.md 6.5). 겹쳐서 두 번 오는 기록은 loader가 data_id로 하나로 묶는다.
"""

from datetime import date, timedelta

INITIAL_DAYS = 35
RECENT_DAYS = 3
CHUNK_DAYS = 7


def query_range(
    today: date, *, initial: bool, full: bool, recent_days: int = RECENT_DAYS, initial_days: int = INITIAL_DAYS
) -> tuple[date, date]:
    """recent_days: 평소 실행이 볼 최근 일수. 회원 단위 즉시 동기화(sync.py)는 2일(오늘·어제)만 본다.
    initial_days: 첫 연동·전체 재조회 일수. 실데이터는 35일, 합성 데이터만 더 길게 쓴다 (synthetic.py)
    """
    days = initial_days if initial or full else recent_days
    return today - timedelta(days=days - 1), today


def chunks(start: date, end: date, size: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """start~end를 size일 구간으로 나눈다. 다음 구간은 앞 구간의 마지막 날에서 시작한다 (하루 겹침)."""
    result = []
    cursor = start
    while True:
        chunk_end = min(cursor + timedelta(days=size - 1), end)
        result.append((cursor, chunk_end))
        if chunk_end >= end:
            return result
        cursor = chunk_end
