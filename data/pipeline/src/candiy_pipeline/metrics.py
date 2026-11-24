"""CANDiY 지표 13종 (data/docs/candiy-api.md 3장)."""

METRICS = (
    "steps",
    "heart_rates",
    "oxygen_saturations",
    "sleeps",
    "exercise_sessions",
    "calories_burned",
    "distances",
    "speeds",
    "vo2_maxes",
    "weights",
    "body_fats",
    "basal_metabolic_rates",
    "heights",
)

# 산소포화도는 시점형이라 end 파라미터를 쓸 수 없다. start만 넣고 받은 뒤 직접 거른다.
NO_END_PARAM = frozenset({"oxygen_saturations"})

# CANDiY SDK 예제 앱이 동작 확인용으로 쓴 기록. 테스트 사용자(mock-1013 등)는 적재하지 않는다 (candiy-api.md 6.2).
# 우리 수집 앱(LiNK)도 패키지명이 같다. 워치 기록·키·몸무게를 이 이름으로 Health Connect에 쓰므로
# 실제 회원의 기록은 적재한다 (S15P31A403-231, loader._fetch_records).
EXCLUDED_APPS = frozenset({"io.candiy.healthconnect.quickstart"})

# 출처가 여럿인 지표는 하루(운동)에 출처 하나만 쓰고, 삼성 헬스를 먼저 고른다
# (calculation-rules.md 7.1, S15P31A403-209).
# 폰 Health Connect도 걸음·거리를 짧은 구간으로 쓰는데, 삼성 헬스 하루 기록과 시간이 겹친다.
PRIMARY_APP = "com.sec.android.app.shealth"
