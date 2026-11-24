"""수면 코칭 골든셋의 개선용(dev)·확인용(holdout) 파일을 등록한다.

코칭 골든셋(eval/golden/split.py)은 한 파일의 문항을 나눠 split.json 에 문항 ID 목록으로 둔다. 수면 코칭 골든셋은
개선용과 확인용이 파일로 나뉘어 있어 나누지 않고, 두 파일의 경로·sha256·문항 ID 를 같은 방식으로 등록한다.
파일이 등록할 때와 다르면 실행기가 멈춘다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.golden.sleep.split      # 지금 파일로 split.json 을 다시 만든다 (문항 수와 sha256 만 출력)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from eval.golden.sleep.items import LATEST, SIDE_LABELS, SIDES, display_path, path_of, row_ids
from eval.golden.split import split_sha256 as split_sha256

SPLIT_FILE = Path(__file__).resolve().parent / "split.json"
RULES = (
    "개선용과 확인용은 파일로 나뉜다. 확인용 파일은 이름 끝이 _holdout 이고, 문항마다 개선용 문항과 뜻이 같은 다른 "
    "말투 질문 하나이며 pair 가 짝인 개선용 문항 id 다.",
    "등록할 때 두 파일의 sha256 과 문항 id 를 적는다. 파일이 바뀌면 실행하지 않고 멈추며, 다시 등록해야 한다.",
    "개선 작업은 개선용 문항만 재고 실패만 읽는다. 확인용 문항은 마일스톤 측정에서만 실행하고 결과는 수와 판정만 쓴다.",
)
REGISTER_COMMAND = "uv run python -m eval.golden.sleep.split"


def build(versions: Sequence[str] = (LATEST,)) -> dict[str, Any]:
    """버전마다 개선용·확인용 파일의 경로·sha256·문항 id. 규칙 글도 함께 담는다."""
    sets: dict[str, Any] = {}
    for version in versions:
        sides = {}
        for side in SIDES:
            path = path_of(version, side)
            sides[side] = {
                "path": display_path(path),
                "sha256": _file_sha256(path),
                "ids": row_ids(path),
            }
        sets[version] = sides
    return {"rules": list(RULES), "sets": sets}


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_split(path: Path = SPLIT_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_file(split: dict[str, Any], version: str, side: str, sha256: str) -> None:
    """파일이 등록한 파일과 같은지 확인한다. 등록이 없거나 sha256 이 다르면 ValueError 를 발생시킨다."""
    entry = split["sets"].get(version, {}).get(side)
    label = SIDE_LABELS[side]
    if entry is None:
        raise ValueError(f"수면 골든셋 {version} {label} 파일이 등록되지 않았다. {REGISTER_COMMAND} 로 등록한다")
    if entry["sha256"] != sha256:
        raise ValueError(
            f"수면 골든셋 {version} {label} 파일 sha256 이 등록할 때({entry['sha256'][:12]})와 다르다 "
            f"({sha256[:12]}). 파일을 확인하고 {REGISTER_COMMAND} 로 다시 등록한다"
        )


def main() -> None:
    split = build()
    SPLIT_FILE.write_text(json.dumps(split, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for version, sides in split["sets"].items():
        counts = " · ".join(f"{SIDE_LABELS[s]} {len(e['ids'])}문항 sha256 {e['sha256'][:12]}" for s, e in sides.items())
        print(f"{version}: {counts}")
    print(f"저장: {SPLIT_FILE} (등록 sha256 {split_sha256(split)[:12]})")


if __name__ == "__main__":
    main()
