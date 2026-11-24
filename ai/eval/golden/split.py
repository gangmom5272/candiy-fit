"""골든셋 문항을 개선용(dev)과 확인용(holdout)으로 나눈다.

개선 작업은 개선용 문항의 실패만 읽고 고친다. 확인용 문항은 마일스톤 측정에서만 실행하고 결과는 집계만 쓴다.
나눈 결과는 split.json 에 문항 ID 목록으로 둔다. 문항 파일은 고치지 않는다.

ai/ 디렉터리에서 실행한다.
    uv run python -m eval.golden.split      # 최신 버전 문항 파일로 split.json 을 다시 만든다

나누는 규칙은 RULES 이고 split.json 에도 같은 글을 넣는다.
- 확인용은 버전마다 문항의 3분의 1(반올림)이다.
- 페르소나, 질문 종류(scoring.question_kind), v2 형식 안전 문항의 경계 범주를 묶음으로 보고 양쪽에 고르게 넣는다.
  문항은 seed 로 섞은 순서로 고르고, 묶음마다 확인용 수가 묶음 크기의 3분의 1 올림을 넘지 않고 개선용에도 하나
  이상 들어가게 한다. 먼저 문항이 2개 이상인 묶음마다 문항이 적은 묶음부터 확인용에 하나씩 넣고, 그다음 섞은
  순서대로 채운다 (make_split).
- 기준 코드(rubric.must·rubric.must_not)를 쓰는 문항이 그 버전에 하나뿐이면 개선용에 둔다. 확인용에 들어가면 그
  기준의 채점을 실제 답변으로 확인할 수 없어서다. 그 문항이 확인용에 들어갔으면 개선용으로 옮기고, 확인용 수를
  유지하도록 개선용 문항 하나를 확인용으로 옮긴다 (keep_in_dev). split.json 의 moves 에 옮긴 문항을 적는다.
- split.json 에는 나눌 때 문항 파일의 sha256 을 함께 둔다. 문항 파일이 바뀌면 실행기가 멈춘다.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from eval.golden.items import LATEST, GoldenItem, Version, load_set
from eval.golden.scoring import question_kind

SPLIT_FILE = Path(__file__).resolve().parent / "split.json"
SEED = 20261030
HOLDOUT_SHARE = Fraction(1, 3)
SPLITS = ("dev", "holdout", "all")
SPLIT_LABELS = {"dev": "개선용", "holdout": "확인용"}
RULES = (
    "확인용은 버전마다 문항의 3분의 1(반올림)이다. 페르소나, 질문 종류, 안전 문항의 경계 범주를 묶음으로 보고, "
    "seed 로 섞은 순서로 고르되 묶음마다 확인용이 묶음 크기의 3분의 1 올림을 넘지 않고 개선용에도 하나 이상 들어가게 "
    "한다. 먼저 문항이 2개 이상인 묶음마다 문항이 적은 묶음부터 확인용에 하나씩 넣고, 그다음 섞은 순서대로 채운다.",
    "기준 코드(rubric.must·rubric.must_not)를 쓰는 문항이 그 버전에 하나뿐이면 개선용에 둔다. 그 문항이 확인용에 "
    "들어갔으면 개선용으로 옮기고, 확인용 수를 유지하도록 질문 종류가 개선용에 2개 이상 있고 묶음 상한을 넘지 않는 "
    "개선용 문항 하나를 확인용으로 옮긴다. 옮길 문항은 확인용이 없는 묶음(문항 2개 이상)을 문항이 적은 묶음부터 "
    "채우는 문항을 seed 로 섞은 순서에서 고르고, 채울 묶음이 없으면 섞은 순서의 첫 후보를 고른다.",
)


def groups(item: GoldenItem) -> list[str]:
    """양쪽에 고르게 넣을 묶음. 페르소나, 질문 종류, 안전 문항이면 경계 범주다."""
    out = [f"persona:{item.persona}", f"kind:{question_kind(item)}"]
    if item.category == "safety" and item.expected_guard is not None:
        out.append(f"guard:{item.expected_guard.category or 'none'}")
    return out


def _seeded_order(items: Sequence[GoldenItem], seed: int) -> list[GoldenItem]:
    order = list(items)
    random.Random(seed).shuffle(order)
    return order


def _limits(items: Sequence[GoldenItem], share: Fraction) -> tuple[Counter[str], dict[str, int]]:
    """묶음 크기와 묶음마다 확인용에 넣을 수 있는 최대 수."""
    size = Counter(g for item in items for g in groups(item))
    return size, {g: min(math.ceil(n * share), n - 1) for g, n in size.items()}


def make_split(
    items: Sequence[GoldenItem], seed: int = SEED, share: Fraction = HOLDOUT_SHARE
) -> tuple[list[str], list[str]]:
    """(개선용 문항 ID, 확인용 문항 ID)를 파일 순서로 반환한다. 방법은 RULES 의 첫 규칙이다."""
    order = _seeded_order(items, seed)
    target = round(len(items) * share)
    size, cap = _limits(items, share)
    held: Counter[str] = Counter()
    holdout: set[str] = set()

    def fits(item: GoldenItem) -> bool:
        return item.id not in holdout and all(held[g] < cap[g] for g in groups(item))

    def take(item: GoldenItem) -> None:
        holdout.add(item.id)
        held.update(groups(item))

    for group in sorted(size, key=lambda g: (size[g], g)):
        if size[group] < 2 or held[group] or len(holdout) >= target:
            continue
        if (item := next((i for i in order if group in groups(i) and fits(i)), None)) is not None:
            take(item)
    for item in order:
        if len(holdout) < target and fits(item):
            take(item)
    return [i.id for i in items if i.id not in holdout], [i.id for i in items if i.id in holdout]


def sole_code_items(items: Sequence[GoldenItem]) -> dict[str, list[str]]:
    """기준 코드(rubric.must·rubric.must_not)를 그 버전에서 혼자 쓰는 문항 ID → 그 코드. 파일 순서다."""
    used = Counter(code for item in items for code in {*item.must, *item.must_not})
    out: dict[str, list[str]] = {}
    for item in items:
        if codes := sorted(code for code in {*item.must, *item.must_not} if used[code] == 1):
            out[item.id] = codes
    return out


def keep_in_dev(
    items: Sequence[GoldenItem],
    dev: Sequence[str],
    holdout: Sequence[str],
    keep: dict[str, list[str]],
    seed: int = SEED,
    share: Fraction = HOLDOUT_SHARE,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """keep 의 문항이 확인용에 있으면 개선용으로 옮기고 대신 개선용 문항 하나를 확인용으로 옮긴다.

    (개선용 문항 ID, 확인용 문항 ID, 옮긴 기록)을 반환한다. 옮길 문항을 고르는 방법은 RULES 의 두 번째 규칙이다.
    대신 옮길 문항이 없으면 ValueError 를 발생시킨다.
    """
    order = _seeded_order(items, seed)
    size, cap = _limits(items, share)
    held_ids = set(holdout)
    moves: list[dict[str, Any]] = []
    for item_id in [i.id for i in items if i.id in keep and i.id in held_ids]:
        held_ids.discard(item_id)
        held = Counter(g for i in items if i.id in held_ids for g in groups(i))
        dev_kinds = Counter(question_kind(i) for i in items if i.id not in held_ids)
        candidates = [
            i
            for i in order
            if i.id not in held_ids
            and i.id not in keep
            and dev_kinds[question_kind(i)] >= 2
            and all(held[g] < cap[g] for g in groups(i))
        ]
        empty = sorted((g for g, n in size.items() if n >= 2 and not held[g]), key=lambda g: (size[g], g))
        chosen = next((i for g in empty for i in candidates if g in groups(i)), None)
        if chosen is None and candidates:
            chosen = candidates[0]
        if chosen is None:
            raise ValueError(f"{item_id} 대신 확인용으로 옮길 문항이 없다")
        held_ids.add(chosen.id)
        moves.append(
            {
                "to_dev": item_id,
                "codes": keep[item_id],
                "to_holdout": chosen.id,
                "filled": [g for g in empty if g in groups(chosen)],
            }
        )
    return [i.id for i in items if i.id not in held_ids], [i.id for i in items if i.id in held_ids], moves


def build(versions: Sequence[Version] = LATEST, seed: int = SEED) -> dict[str, Any]:
    """버전마다 나눈 결과. 규칙, 나눌 때 문항 파일의 경로·sha256, 기준 코드를 혼자 쓰는 문항과 옮긴 문항을 담는다."""
    sets = {}
    for version in versions:
        items, info = load_set(version)
        keep = sole_code_items(items)
        dev, holdout, moves = keep_in_dev(items, *make_split(items, seed), keep, seed)
        sets[version] = {
            "path": info["path"],
            "sha256": info["sha256"],
            "dev": dev,
            "holdout": holdout,
            "sole_code_items": keep,
            "moves": moves,
        }
    return {"seed": seed, "holdout_share": str(HOLDOUT_SHARE), "rules": list(RULES), "sets": sets}


def load_split(path: Path = SPLIT_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def split_sha256(split: dict[str, Any]) -> str:
    """나누기 내용의 sha256. 파일 줄바꿈과 관계없이 같은 나누기면 같도록 키 정렬·공백 없는 JSON 으로 계산한다."""
    text = json.dumps(split, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def membership(split: dict[str, Any], version: str, sha256: str) -> dict[str, str] | None:
    """문항 ID → dev·holdout. 이 버전의 나누기가 없으면 None 이다.

    문항 파일 sha256 이 나눌 때와 다르면 문항 ID 목록을 믿을 수 없어 ValueError 를 발생시킨다.
    """
    entry = split["sets"].get(version)
    if entry is None:
        return None
    if entry["sha256"] != sha256:
        raise ValueError(
            f"{version} 문항 파일 sha256 이 나눌 때({entry['sha256'][:12]})와 다르다 ({sha256[:12]}). "
            "split.json 을 다시 만들어야 한다"
        )
    return {**dict.fromkeys(entry["dev"], "dev"), **dict.fromkeys(entry["holdout"], "holdout")}


def main() -> None:
    split = build()
    SPLIT_FILE.write_text(json.dumps(split, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for version, entry in split["sets"].items():
        print(f"{version}: 개선용 {len(entry['dev'])} · 확인용 {len(entry['holdout'])} (seed {split['seed']})")
        for move in entry["moves"]:
            print(f"  {move['to_dev']} → 개선용 ({', '.join(move['codes'])}), {move['to_holdout']} → 확인용")
    print(f"저장: {SPLIT_FILE}")


if __name__ == "__main__":
    main()
