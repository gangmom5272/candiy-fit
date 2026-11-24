"""CANDiY API 응답 구조 확인용 스크립트.

지표별 조회 결과를 data/raw/ 에 원본 그대로 저장하고, 화면에는 값 없이 구조와 확인용 요약만 출력한다.
건강데이터 값은 화면·로그·AI 도구에 남기지 않는다. 실제 값은 data/raw/ 파일을 직접 열어서 확인한다.

확인용 요약에 나오는 것 (측정값·날짜는 나오지 않는다):
    - 레코드 개수, 출처 앱(app) 종류별 개수
    - 기록 길이 분포 (구간형 지표만. 수면·운동은 길이 자체가 건강데이터라 제외)
    - 기록이 걸친 시간대 수 (0~23시 중 몇 개)
    - data 안 필드별 존재 개수, 목록 필드가 비어 있지 않은 개수
    - 시계열 샘플 간격 중앙값 (초)
    - 칼로리 기록 중 운동 세션과 겹치는 비율

사용법 (저장소 루트에서):
    python data/scripts/explore_candiy_api.py --user user-123 --start 2026-10-01 --end 2026-10-12
    python data/scripts/explore_candiy_api.py --user user-123 --start 2026-10-01 --end 2026-10-12 --metrics steps sleeps
    python data/scripts/explore_candiy_api.py --user user-123 --start 2026-10-01 --end 2026-10-12 --no-structure
"""

import argparse
import base64
import json
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
KST = timezone(timedelta(hours=9))

# CANDiY 조회 API 13종 (SDK 업로드 경로와 같은 이름, data/docs/candiy-api.md 3장)
METRICS = {
    # 1단계
    "steps": "api/steps",
    "heart_rates": "api/heart_rates",
    "oxygen_saturations": "api/oxygen_saturations",
    "sleeps": "api/sleeps",
    # 2단계
    "exercise_sessions": "api/exercise_sessions",
    "calories_burned": "api/calories_burned",
    "distances": "api/distances",
    "speeds": "api/speeds",
    # 3단계
    "vo2_maxes": "api/vo2_maxes",
    "weights": "api/weights",
    "body_fats": "api/body_fats",
    "basal_metabolic_rates": "api/basal_metabolic_rates",
    "heights": "api/heights",
}

# 산소포화도는 종료 시각이 없는 시점형이라 end 파라미터를 쓸 수 없다 (data/docs/candiy-api.md)
NO_END_PARAM = {"oxygen_saturations"}

# 기록 길이 분포를 보여 줄 구간형 지표. 수면·운동 세션 길이는 건강데이터라 출력하지 않는다.
INTERVAL_METRICS = {"steps", "heart_rates", "calories_burned", "distances", "speeds"}

# data 안에 시계열 샘플이 있는 지표
SAMPLE_METRICS = {"heart_rates", "speeds"}


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def request_json(req: urllib.request.Request) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw_error": body[:300]}


def get_token(base_url: str, client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        f"{base_url}/oauth/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    status, body = request_json(req)
    if status != 200 or "access_token" not in body:
        sys.exit(f"토큰 발급 실패: HTTP {status}")
    return body["access_token"]


def describe(value: object, depth: int = 0, max_depth: int = 6) -> object:
    """값을 지우고 구조만 남긴다. 리스트는 첫 원소 구조와 개수만 보여 준다."""
    if depth > max_depth:
        return "..."
    if isinstance(value, dict):
        return {k: describe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "list[0]"
        return {f"list[{len(value)}] of": describe(value[0], depth + 1)}
    if isinstance(value, str):
        # SDK는 data 필드에 JSON 문자열을 넣는다. 펼쳐서 안쪽 구조도 보여 준다.
        if value[:1] in "{[":
            try:
                return {"json-string": describe(json.loads(value), depth + 1)}
            except json.JSONDecodeError:
                pass
        return "str"
    if value is None:
        return "null"
    return type(value).__name__


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_payload(record: dict) -> dict:
    data = record.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def duration_bucket(start: datetime, end: datetime) -> str:
    minutes = (end - start).total_seconds() / 60
    if minutes < 1:
        return "1분 미만"
    if minutes < 59.5:
        return "1~59분"
    if minutes < 60.5:
        return "60분"
    if minutes < 24 * 60 - 1:
        return "1시간 초과~하루 미만"
    return "하루 이상"


def summarize(metric: str, records: list[dict]) -> dict:
    """측정값 없이 6장 확인 항목에 답할 수 있는 요약만 만든다."""
    summary: dict[str, object] = {"레코드 개수": len(records)}
    if not records:
        return summary

    summary["출처 앱"] = dict(Counter(str(r.get("app")) for r in records))
    summary["end 필드가 있는 레코드"] = f"{sum(1 for r in records if r.get('end'))}/{len(records)}"

    starts = [parse_time(r.get("start")) for r in records]
    hours = {s.astimezone(KST).hour for s in starts if s}
    summary["기록 시작이 걸친 시간대"] = f"{len(hours)}/24"

    if metric in INTERVAL_METRICS:
        buckets = Counter(
            duration_bucket(s, e)
            for s, e in ((parse_time(r.get("start")), parse_time(r.get("end"))) for r in records)
            if s and e
        )
        summary["기록 길이 분포"] = dict(buckets)

    payloads = [parse_payload(r) for r in records]
    key_counts: Counter[str] = Counter()
    nonempty_lists: Counter[str] = Counter()
    string_null: Counter[str] = Counter()
    for p in payloads:
        for key, value in p.items():
            key_counts[key] += 1
            if isinstance(value, list) and value:
                nonempty_lists[key] += 1
            if value == "null":
                string_null[key] += 1
    summary["data 필드 존재 개수"] = {k: f"{v}/{len(records)}" for k, v in key_counts.items()}
    if nonempty_lists:
        summary["목록이 비어 있지 않은 개수"] = {k: f"{v}/{len(records)}" for k, v in nonempty_lists.items()}
    if string_null:
        summary['문자열 "null" 개수'] = {k: f"{v}/{len(records)}" for k, v in string_null.items()}

    if metric in SAMPLE_METRICS:
        gaps = []
        sample_counts = []
        for p in payloads:
            times = sorted(t for t in (parse_time(s.get("time")) for s in p.get("samples", []) if isinstance(s, dict)) if t)
            sample_counts.append(len(times))
            gaps.extend((b - a).total_seconds() for a, b in zip(times, times[1:]))
        summary["레코드당 샘플 수 중앙값"] = statistics.median(sample_counts)
        if gaps:
            summary["샘플 간격 중앙값(초)"] = statistics.median(gaps)

    return summary


def intervals(records: list[dict]) -> list[tuple[datetime, datetime]]:
    result = []
    for r in records:
        s, e = parse_time(r.get("start")), parse_time(r.get("end"))
        if s and e:
            result.append((s, e))
    return result


def calories_in_exercise(calories: list[dict], sessions: list[dict]) -> str:
    """칼로리 기록이 운동 중에만 있는지(운동 소모) 하루 종일 있는지(총 소모) 가늠한다."""
    cal = intervals(calories)
    ex = intervals(sessions)
    if not cal:
        return "칼로리 기록 없음"
    overlapped = sum(1 for cs, ce in cal if any(cs < ee and ce > es for es, ee in ex))
    return f"{overlapped}/{len(cal)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="CANDiY API 응답 구조 확인")
    parser.add_argument("--user", required=True, help="SDK에 넘긴 endUserId")
    parser.add_argument("--start", required=True, help="조회 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="조회 종료일 YYYY-MM-DD")
    parser.add_argument("--metrics", nargs="*", default=list(METRICS), choices=list(METRICS))
    parser.add_argument("--no-structure", action="store_true", help="구조 출력을 생략하고 요약만 출력")
    args = parser.parse_args()

    env = load_env(ROOT / ".env")
    base_url = env["CANDIY_BASE_URL"].rstrip("/")
    token = get_token(base_url, env["CANDIY_CLIENT_ID"], env["CANDIY_CLIENT_SECRET"])
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    fetched: dict[str, list[dict]] = {}
    for metric in args.metrics:
        params = {"end_user_id": args.user, "start": args.start}
        if metric not in NO_END_PARAM:
            params["end"] = args.end
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"{base_url}/{METRICS[metric]}?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )
        status, body = request_json(req)

        out = RAW_DIR / f"{metric}_{args.start}_{args.end}.json"
        out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n===== {metric}  (HTTP {status}) → {out.relative_to(ROOT)}")
        if not args.no_structure:
            print(json.dumps(describe(body), ensure_ascii=False, indent=2))

        records = body.get("data") if isinstance(body, dict) else None
        if status == 200 and isinstance(records, list):
            fetched[metric] = [r for r in records if isinstance(r, dict)]
            print("--- 요약")
            print(json.dumps(summarize(metric, fetched[metric]), ensure_ascii=False, indent=2))

    if "calories_burned" in fetched and "exercise_sessions" in fetched:
        print("\n===== 교차 확인")
        ratio = calories_in_exercise(fetched["calories_burned"], fetched["exercise_sessions"])
        print(f"운동 세션과 겹치는 칼로리 기록: {ratio}")
        print("  거의 전부 겹치면 운동 소모 칼로리, 일부만 겹치면 하루 총 소모 칼로리로 본다")


if __name__ == "__main__":
    main()
