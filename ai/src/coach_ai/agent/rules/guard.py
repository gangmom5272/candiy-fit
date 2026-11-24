from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

GuardCategory = Literal["emergency", "mental_crisis", "diagnosis", "medication", "out_of_scope", "adversarial"]


@dataclass(frozen=True, slots=True)
class GuardRule:
    category: GuardCategory
    detect: Callable[[str], bool]
    answer: str
    # 바로 앞 사용자 질문을 함께 검사하는 판정. 없으면 이번 질문만 검사한다
    follow_up: Callable[[str, str], bool] | None = None
    # 질문에 따라 고정 답을 고르는 함수 (이번 질문, 앞 질문에 이어 차단했으면 앞 질문). 없으면 answer 를 쓴다
    choose: Callable[[str, str | None], str] | None = None


@dataclass(frozen=True, slots=True)
class GuardResult:
    category: GuardCategory
    answer: str


def _p(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


# 중간 낱말 허용 거리. 길면 관계없는 낱말과 이어져 일상 문장을 차단한다
_GAP = r"[^.?!,\n]{0,6}"
# "짜 주느라", "짜 줄 시간"처럼 "주·줄" 뒤가 요청이 아닌 일상 문장은 제외한다
_REQUEST = r"\s?(줘|줄래|줄\s?수|주(세요|라|실|시|면|겠))"
# "약"은 혼자 쓰이거나("약먹고"처럼 먹는 말이 붙은 경우 포함) "수면약"처럼 낱말 끝에 올 때만 약물이다. 약밥·약국처럼
# 뒤에 글자가 이어진 낱말과 요약·예약·치약처럼 "약"으로 끝나는 다른 낱말은 앞뒤 글자로 제외하고, 보조약·조절약은
# 조약·절약과 앞 두 글자로 구분한다. "병원예약"처럼 붙여 쓴 말이 있어 낱말 안에서 찾는다
_AFTER_DRUG = (
    r"[^가-힣]|$|을|은|의|에|도|만|으로|이|인|일|입|임|보다|까지|부터|처럼|마다|밖에|뿐|조차|마저"
    r"|먹|복용|드시|드셔|추천|처방|끊|늘려|줄여|바꿔"
)
_DRUG = rf"(?<![요예계치공협서규])(?:(?<![조절])|(?<=보조|조절))약(?={_AFTER_DRUG})"

# 증상 바로 뒤의 부정("아프진 않은데", "통증은 없어", "아픈 건 아닌데")과 바로 앞의 "안"
_NEGATED_AFTER = re.compile(
    r"(하)?(진|지는|지도|지)\s?(않|안)"
    r"|(은|는|이|가|도)?\s?(없|아니|아냐|아닌)"
    r"|\s?(건|것은|거는|게|데는|데가|곳은|곳이)\s?(없|아니|아냐|아닌)"
)
_NEGATED_BEFORE = re.compile(r"(안|않)\s?$")


def _negated(text: str, symptom: re.Match[str]) -> bool:
    """그 증상만 없다고 말했는지. 같은 문장의 다른 증상은 따로 검사한다."""
    return bool(_NEGATED_AFTER.match(text, symptom.end()) or _NEGATED_BEFORE.search(text, 0, symptom.start()))


_CHEST = re.compile(r"가슴|심장|흉부")
# "가슴 운동", "가슴 근육"은 운동·근육 이야기다
_CHEST_COMPOUND = re.compile(r"\s?(운동|근육|근력|스트레칭|벤치|푸시업|웨이트|둘레|사이즈|펴기)")
# "가슴이랑 팔이 아파"처럼 부위를 이어 말하면 뒤의 증상이 가슴에도 해당한다
_CHEST_AND = re.compile(r"(이랑|랑|하고|과|와)\s")
_CHEST_SYMPTOM = re.compile(
    r"아프|아파|아픈|아픔|아팠|아플|통증|답답|조이|조여|조임|조였|쥐어\s?짜|짓눌|눌리|찌르|찌릿|쑤시|쑤셔|콕콕|쿡쿡"
)
# 부위와 증상 사이에 다른 부위가 오면 그 부위의 증상이다 ("가슴 운동하고 팔이 아파")
_OTHER_PART = re.compile(r"(팔|다리|무릎|허리|어깨|등|발|손|목|머리|배)(이|가|에|은|는|도|\s)")
# 부위 뒤에서 증상을 찾는 거리. "심장이 너무 빨리 뛰고 조여와"의 "조여"까지 닿는다
_CHEST_REACH = 16
_SENTENCE_END = re.compile(r"[.?!\n]")

_BREATH_HARD = re.compile(
    r"숨\s?(을|이|도)?\s?((잘|좀|제대로|너무|도저히)\s?)?"
    r"(못\s?쉬|안\s?쉬어|쉬어지지\s?않|쉬기\s?(가\s?)?((너무|좀|많이)\s?)?(힘들|힘든|어렵|어려|곤란)"
    r"|쉴\s?수(가)?\s?없|넘어갈)"
    r"|호흡\s?곤란|호흡(이|하기)?\s?(가\s?)?((너무|좀|많이|잘)\s?)?(힘들|힘든|어렵|어려|안\s?돼|곤란)"
)
# "숨이 막히다"만 날씨·분위기와 함께 쓰는 관용 표현과 겹친다. 숨을 쉴 수 없다는 말은 날씨가 있어도 차단한다
_BREATH_BLOCKED = re.compile(r"숨\s?(이|도)?\s?((좀|너무|턱)\s?)?(막혀|막히|막힐|막혔)")
_STIFLING = re.compile(r"더워|더운|더위|덥|습해|습한|습도|후덥|찜통|폭염|미세먼지|매연|공기|분위기|긴장|경기|바빠|바쁘")
_STIFLING_BEFORE = 12
_STIFLING_AFTER = 10

_FAINT = re.compile(r"기절|실신|졸도|정신을\s?잃|의식(을|이)?\s?(잃|없|흐려|희미)|쓰러(졌|질|져|지|짐|진)")
# 잠이 바로 이어지면 깊이 잤다는 비유다("기절하듯 잤어", "쓰러져 잤어"). "쓰러질 듯 어지러워"는 쓰러지기 직전 증상이다
_INTO_SLEEP = re.compile(
    r"(하듯이?|듯이?|해서|하고|하자마자|자마자|서|한?\s?(것|거)\s?처럼|처럼)?\s?"
    r"(잠들|잠이\s?들|잠에\s?들|잤|자고|자서|자\s?버|잠)"
)

_SEIZURE = re.compile(r"경련")
# 부위를 말한 경련과 쥐는 운동 중에도 흔한 근육 증상이다
_MUSCLE_PART = re.compile(r"눈|입술|입가|얼굴|볼|다리|종아리|허벅지|발(?!작)|손|팔|근육|쥐")
_WHOLE_BODY = re.compile(r"온몸|전신|몸\s?전체")
_MUSCLE_PART_BEFORE = 10

_OTHER_EMERGENCY = re.compile(
    r"마비|흉통|피를\s?토|토혈|각혈|(말|발음)이\s?어눌"
    r"|(한쪽|한\s쪽|반쪽)\s?(팔|다리|팔다리|얼굴|몸)(에|이|가|은|는)?\s?"
    r"(힘이\s?(빠|없)|감각이\s?없|저리|저려|마비|처지|처져|안\s?움직)"
)


def _chest_pain(text: str) -> bool:
    for part in _CHEST.finditer(text):
        if _CHEST_COMPOUND.match(text, part.end()):
            continue
        end = part.end() + _CHEST_REACH
        stop = _SENTENCE_END.search(text, part.end(), end)
        if stop:
            end = stop.start()
        joined = _CHEST_AND.match(text, part.end()) is not None
        for symptom in _CHEST_SYMPTOM.finditer(text, part.end(), end):
            if not joined and _OTHER_PART.search(text, part.end(), symptom.start()):
                continue
            if not _negated(text, symptom):
                return True
    return False


def _breathing_difficulty(text: str) -> bool:
    if any(not _negated(text, m) for m in _BREATH_HARD.finditer(text)):
        return True
    for m in _BREATH_BLOCKED.finditer(text):
        near = text[max(0, m.start() - _STIFLING_BEFORE) : m.end() + _STIFLING_AFTER]
        if not _negated(text, m) and not _STIFLING.search(near):
            return True
    return False


def _fainting(text: str) -> bool:
    return any(not _INTO_SLEEP.match(text, m.end()) and not _negated(text, m) for m in _FAINT.finditer(text))


def _seizure(text: str) -> bool:
    for m in _SEIZURE.finditer(text):
        before = text[max(0, m.start() - _MUSCLE_PART_BEFORE) : m.start()]
        if _MUSCLE_PART.search(before) and not _WHOLE_BODY.search(before):
            continue
        if not _negated(text, m):
            return True
    return False


def _other_emergency(text: str) -> bool:
    return any(not _negated(text, m) for m in _OTHER_EMERGENCY.finditer(text))


_EMERGENCY_KINDS: dict[str, Callable[[str], bool]] = {
    "chest": _chest_pain,
    "breath": _breathing_difficulty,
    "faint": _fainting,
    "seizure": _seizure,
    "other": _other_emergency,
}
# 같은 증상이 이어진다는 말. 이어지는 질문은 부위를 빼고 말하는 일이 많아 증상 낱말만 찾는다
_STILL = re.compile(r"지금도|아직도|아직|여전히|계속")
_SAME_SYMPTOM: dict[str, re.Pattern[str]] = {
    "chest": _CHEST_SYMPTOM,
    "breath": re.compile(r"숨\s?(이|을)?\s?(차|가빠|가쁘|막|못\s?쉬|안\s?쉬|쉬기|쉴\s?수)|호흡"),
    "faint": re.compile(r"쓰러|기절|실신|졸도|정신|의식|어지러|어지럽"),
    "seizure": re.compile(r"경련|떨려|떨리"),
    "other": re.compile(r"마비|저리|저려|힘이\s?(빠|없)|감각이\s?없|어눌|흉통|피를\s?토|토혈|각혈"),
}


def _emergency(text: str) -> bool:
    return any(detect(text) for detect in _EMERGENCY_KINDS.values())


def _same_symptom_continues(question: str, previous: str) -> bool:
    """앞 질문의 응급 증상이 지금도 이어진다는 질문인지. 다른 부위의 증상은 같은 증상이 아니다."""
    if not _STILL.search(question):
        return False
    for kind, detect in _EMERGENCY_KINDS.items():
        if not detect(previous):
            continue
        for symptom in _SAME_SYMPTOM[kind].finditer(question):
            if kind == "chest" and _OTHER_PART.search(question, 0, symptom.start()):
                continue
            if not _negated(question, symptom):
                return True
    return False


# 위기 표현에는 부정문 예외를 두지 않는다. "죽고 싶진 않은데"도 상담 안내가 필요하다
_CRISIS = _p(
    r"죽고\s?싶",
    r"죽어\s?버리고\s?싶",
    r"죽어\s?버릴까",
    r"죽는\s?게\s?(낫|나을|편할)",
    r"자살",
    r"극단적\s?(선택|인\s?생각)",
    r"목숨을\s?끊",
    r"자해",
    r"살고\s?싶지\s?않",
    r"살기\s?(가\s?)?싫",
    r"사는\s?게\s?(싫|의미\s?(가\s?)?없|무의미|지겹|지겨|괴로)",
    r"살\s?이유가\s?없",
    r"살아\s?(갈|야\s?할)\s?이유(가|를)?\s?(없|모르)",
    r"살아서\s?뭐\s?(해|하)",
    r"살아\s?있는\s?게\s?(싫|괴롭|괴로|의미\s?없)",
    r"사라지고\s?싶",
    r"없어지고\s?싶",
    r"(인생|삶|모든\s?(걸|것을?))(을|를)?\s?끝내",
    r"뛰어\s?내리고\s?싶",
    r"손목을\s?(긋|그었|그을)",
    r"목을\s?매",
    # 잠에서 깨지 않기를 바라는 말. "주말엔 영원히 자고 싶다"처럼 늦잠 이야기도 함께 차단된다
    r"영원히\s?(잠들|자)고\s?싶",
    r"눈을?\s?(뜨지\s?않았으면|안\s?떴으면)",
    r"(깨어나지\s?않았으면|안\s?깨어났으면)",
)


def _mental_crisis(text: str) -> bool:
    return _CRISIS.search(text) is not None


# "증" 하나로 끝나는 패턴은 증상·증가까지 차단하므로 병명을 나열한다
_DISEASE = (
    r"(불면증|기면증|(수면|공황|불안)\s?장애|무호흡|우울증|조울증|ADHD|치매"
    r"|부정맥|서맥|빈맥|심방세동|협심증|심부전|고혈압|저혈압|당뇨|고지혈증|빈혈|천식"
    r"|관절염|족저근막염|건초염|(허리|목)\s?디스크|오십견|골다공증|증후군|질환|병(?!원))"
)
# 질환을 밝힌 문장("고혈압 있는 거 알고 있는데")은 판정 요청이 아니다. 뒤에 판정을 묻는 말이 와야 한다
_JUDGEMENT = r"(아니|아냐|아닐|같|인가|일까|맞|야\?|이야)"
_DIAGNOSIS = _p(
    _DISEASE + r"\s?(이야|이냐|인가|일까|인지|인\s?(것|거)\s?같|인\s?듯|같(아|애|은데|다)|맞(아|나|는지|지)"
    r"|아니야|아냐|아닐까|아닌가|아닌지)",
    _DISEASE + r"\s?(이|가)?\s?있는\s?(거|건|것)\s?" + _JUDGEMENT,
    _DISEASE + r"\s?(에|이)?\s?걸린\s?(거|건|것)\s?" + _JUDGEMENT,
    _DISEASE + r"\s?(에|이)?\s?걸렸(나|을까|는지|는\s?(것|거)\s?같)",
    r"(무슨|어떤)\s?병\s?(이야|이냐|인가|일까|인지|때문|걸린|같|이에요|이지|일\s?수|있는|아니|에\s?걸린)",
)
_DIAGNOSE_REQUEST = re.compile(r"진단\s?(을\s?)?(좀\s?)?(해|하|내려)\s?(줘|줄래|주세요|주실|줄\s?수|봐|볼래|주라)")
# "수면 상태 진단해 줘"처럼 기록을 가리키면 질병 판정 요청이 아니다
_DIAGNOSE_TARGET = re.compile(_DISEASE + r"|증상|통증|아프|아파|아픈", re.IGNORECASE)


def _diagnosis(text: str) -> bool:
    if _DIAGNOSIS.search(text):
        return True
    return bool(_DIAGNOSE_REQUEST.search(text) and _DIAGNOSE_TARGET.search(text))


# 이름만으로 약이 분명한 낱말
_DRUG_NAME = re.compile(
    r"수면제|수면\s?(유도|보조)\s?(제|약)|보조\s?약(?![속간관])|진통제|항우울제|안정제|진정제|각성제|해열제|소화제|소염제"
    r"|항생제|이뇨제|식욕\s?억제제|스테로이드|약물|처방약|처방전"
)
# "수면제 없이 잘 자는 방법"은 약을 쓰지 않는 방법을 묻는다. 뒤에 복용·중단·용량을 묻는 말이 오면 약 질문이다
_WITHOUT_DRUG = re.compile(r"\s?(없이|안\s?먹고|안\s?쓰고|말고|대신|빼고|에\s?의존하지\s?않고)")
_DRUG_USE = re.compile(r"먹|복용|끊|줄여|늘려|바꿔|용량|mg|밀리그램|처방", re.IGNORECASE)
# 몸에서도 만들어지는 성분이라 먹는 약 맥락이 있을 때만 약으로 판정한다 ("멜라토닌 많은 음식")
_HORMONE = re.compile(r"멜라토닌")
_HORMONE_INTAKE = re.compile(
    r"[^.?!\n]{0,10}?(먹|복용|mg|밀리그램|㎎|몇\s?(알|정|캡슐)|영양제|보충제|젤리|처방|직구|구매|구입|사서|섭취량|용량)",
    re.IGNORECASE,
)
_FOOD = re.compile(r"음식|식품|식단|과일|먹거리")
_DRUG_REQUEST = _p(
    rf"{_DRUG}(을|은|이|도)?\s?(먹|복용|추천|처방|끊|늘려|줄여|바꿔|있|뭐)",
    rf"먹(는|을|은|던)(\s?만한)?\s?{_DRUG}",
    r"(?<!공)복용",
)
# mg 만으로는 약으로 판정하지 않는다 ("카페인 하루 몇 mg까지 괜찮아?")
_DOSE = re.compile(
    r"mg|밀리그램|㎎|몇\s?(알|정|캡슐|포)|복용량|섭취량|용량|(하루|한\s?번)\s?(에\s?)?몇\s?(번|알|정)", re.IGNORECASE
)
_ANY_DRUG = re.compile("|".join([_DRUG_NAME.pattern, r"멜라토닌|영양제|보충제", _DRUG]))
_INTAKE_FOLLOW_UP = re.compile(
    _DOSE.pattern + r"|먹어도|먹으면|먹어야|먹는\s?게|얼마나\s?먹|언제\s?먹|복용", re.IGNORECASE
)

# 약 질문은 의도로 나눈다. 복용 판단(먹어도 되는지, 용량, 끊기, 함께 먹기, 약 고르기, 다른 사람 약)은 차단하고,
# 일반 지식(분류, 차이, 무엇인지, 처방이 필요한지)은 통과시킨 뒤 답을 다 받아 검사한다 (drug_answer.py).
# 둘 다 아니면 위 낱말 목록으로 판정한다 (_listed_medication)
#
# 약을 가리키는 일반 낱말. 제품 이름 목록 대신 약의 종류를 뜻하는 ~제와 제형으로 찾아, 처음 보는 약 이름도
# "OO 수면제", "OO라는 약", "OO캡슐"처럼 말하면 같은 규칙이 적용된다
_DRUG_KIND = (
    r"(?:수면\s?(?:유도|보조)|수면|유도|보조|진통|해열|소화|소염|항생|영양|보충|안정|진정|각성|이뇨|지사|제산|변비"
    r"|항히스타민|항우울|항불안|억제|피로\s?회복)제"
)
_DRUG_WORD = re.compile(rf"{_DRUG_KIND}|{_DRUG}|약물|처방전|스테로이드|[가-힣A-Za-z]+(?:캡슐|시럽)")
# "OO정"은 일정·걱정 같은 낱말과 겹쳐, 먹는 말이나 알 수가 바로 뒤에 올 때만 약으로 본다
_TABLET = re.compile(
    r"[가-힣A-Za-z]{2,}정(?=\s?(?:을|를|은|는|도)?\s?(?:먹|복용|드시|드셔|몇\s?알|한\s?알|반\s?알|하루))"
)
# 위 종류에 없는 "OO제"도 문제·숙제·엊그제 같은 낱말과 겹쳐, 먹는 말이나 알 수가 바로 뒤에 올 때만 약으로 본다
_UNLISTED_KIND = re.compile(
    r"[가-힣A-Za-z]{2,}(?<!그)제(?=\s?(?:을|를|은|는|도)?\s?(?:먹|복용|드시|드셔|몇\s?알|한\s?알|반\s?알))"
)
# 약 낱말 뒤 같은 문장에 오면 복용 판단을 묻는 말
_DECIDE_AFTER = re.compile(
    r"먹|드시|드셔|드세요|복용|섭취|투약|끊|중단|줄여|줄이|늘려|늘리|바꿔|바꾸|추천|골라|고르"
    r"|처방\s?(?:받|해)|(?<![가-힣])사(?:도|면|야|려|서)|살까|구매|구입|직구"
    r"|어때|어떨까|괜찮|효과|도움|(?:잘|안)\s?(?:들|듣)|같이|함께|섞|병용|부작용|중독|의존|위험|안전"
    r"|나한테|나에게|내게|저한테|저에게|맞(?:아|을까|는지|나|을지)"
    r"|용량|mg|밀리그램|㎎|몇\s?(?:알|정|캡슐|포|번|개)|(?:반|한|두)\s?알|얼마나",
    re.IGNORECASE,
)
# 약 낱말 바로 뒤에 오면 약을 달라는 말 ("잠 오는 약 있어?")
_ASK_FOR_DRUG = re.compile(r"\s?(?:을|를|은|는|이|가|도)?\s?있(?:어|나|을까|니|는지|어요|나요|습니까)")
# 약 낱말 바로 앞에 오면 먹을 약이나 고를 약을 가리키는 말 ("두통에 먹는 약", "엄마가 먹는 수면제", "잘 듣는 수면제")
_DECIDE_BEFORE = re.compile(
    r"(?:(?:먹|드시|복용하|처방받|추천하|고를|바꿀|끊을)(?:는|을|은|던|을\s?만한|기\s?좋은|면\s?좋은)"
    r"|좋은|잘\s?듣는|효과\s?(?:좋은|있는)|센|순한|괜찮은)\s?$"
)
_DECIDE_BEFORE_REACH = 12
# 약을 말한 문장 어디에 있든 약 고르기를 묻는 말 ("어떤 약이 좋아?", "뭐가 더 나아?")
_CHOOSE = re.compile(
    r"(?:어떤|무슨|뭔)\s?(?:약|거|걸|게|것)(?:이|가|을|를)?\s?(?:좋|나아|낫|효과|잘\s?들|먹|추천)"
    r"|뭐(?:가|를)?\s?(?:더\s?)?(?:좋|나아|낫|먹)"
)
_GENERAL_ASK = re.compile(
    r"차이|다른\s?점|(?:뭐가|무엇이|어디가|어떤\s?점이|어떻게|뭐)\s?(?:달라|다르|다른)"
    r"|분류|종류|계열"
    r"|뭐(?:야|예요|에요|지|니|냐|임|일까|인데|인가)|무엇(?:이야|인가|이에요|인지|일까)|뭔데|뭔지"
    r"|(?:어떤|무슨)\s?(?:약|거|것)(?:이야|인가|이에요|이지|일까|인지)"
    r"|의약품(?:이야|인가|이에요|이지|일까|인지)|(?:전문|일반)\s?의약품"
    r"|처방(?:이|전이|전)?\s?(?:필요|있어야)|처방\s?없이\s?(?:살|사|구)"
    r"|(?:약국|편의점)에서\s?(?:그냥\s?)?(?:살|사|구할)\s?수"
    r"|에\s?대해\s?(?:알려|설명)|설명해"
)
_CLAUSE_END = re.compile(r"[.?!\n]")
# 잠과 관련된 질문. 수면 약이거나 수면·잠 낱말이 있으면 차단할 때 수면 기록을 함께 보자고 제안한다
_SLEEP_WORD = re.compile(
    r"수면|잠(?![깐시금재복수그가꼬기])|숙면|불면|자기\s?전|잘\s?자|못\s?자|졸음|졸려|졸린|졸리|멜라토닌"
)

MEDICATION_NOTICE = "약이나 복용에 대해서는 안내해 드릴 수 없어요. 의사나 약사와 상담해 주세요."
# 차단하거나 되돌린 답 끝의 다음 질문 제안. LLM 으로 만들지 않는 고정 문장이다
SLEEP_NEXT_QUESTION = "대신 최근 수면 기록으로 잠드는 시각과 습관을 같이 볼까요?"
RECORD_NEXT_QUESTION = "대신 생활 기록(수면·걸음·운동)으로 볼 수 있는 건 함께 찾아 드릴 수 있어요."


def medication_answer(sleep: bool) -> str:
    """약 질문을 차단하거나 답을 되돌릴 때의 고정 답. 잠과 관련된 질문이면 수면 기록을 함께 보자고 제안한다."""
    return f"{MEDICATION_NOTICE}\n\n{SLEEP_NEXT_QUESTION if sleep else RECORD_NEXT_QUESTION}"


def _sleep_related(*texts: str | None) -> bool:
    return any(text is not None and _SLEEP_WORD.search(text) for text in texts)


def _drug_mentions(text: str) -> list[re.Match[str]]:
    """약을 가리키는 낱말. "수면제 없이"처럼 약을 쓰지 않는 방법의 낱말은 빼고, 그 뒤에 복용·중단을 묻는 말이
    오면 넣는다."""
    found = []
    for m in [*_DRUG_WORD.finditer(text), *_TABLET.finditer(text), *_UNLISTED_KIND.finditer(text)]:
        without = _WITHOUT_DRUG.match(text, m.end())
        if without is None or _DRUG_USE.search(text, without.end()):
            found.append(m)
    return found


def _hormone_intake(text: str) -> bool:
    for hormone in _HORMONE.finditer(text):
        intake = _HORMONE_INTAKE.match(text, hormone.end())
        if intake is not None and not _FOOD.search(intake.group()):
            return True
    return False


def _decides(text: str, m: re.Match[str]) -> bool:
    end = _CLAUSE_END.search(text, m.end())
    after = _DECIDE_AFTER.search(text, m.end(), end.start() if end else len(text))
    before = text[max(0, m.start() - _DECIDE_BEFORE_REACH) : m.start()]
    return bool(after or _ASK_FOR_DRUG.match(text, m.end()) or _DECIDE_BEFORE.search(before))


DrugIntent = Literal["decision", "general"]


def _drug_intent(text: str) -> DrugIntent | None:
    """약 질문의 의도. 약 낱말이 없거나 두 의도 어디에도 맞지 않으면 None 이다.

    멜라토닌은 몸에서도 만들어지는 성분이라 먹는 말이 바로 뒤에 올 때만 복용 판단으로 본다 ("멜라토닌 분비 늘리는 법").
    """
    mentions = _drug_mentions(text)
    hormone = _HORMONE.search(text) is not None
    if not mentions and not hormone:
        return None
    if _hormone_intake(text) or (hormone and _DOSE.search(text)):
        return "decision"
    if mentions and (any(_decides(text, m) for m in mentions) or _CHOOSE.search(text) or _DOSE.search(text)):
        return "decision"
    return "general" if _GENERAL_ASK.search(text) else None


def _listed_medication(text: str) -> bool:
    """약 이름 낱말, 약 요청, 용량으로 판정한다. 두 의도 어디에도 맞지 않는 약 문장에 쓴다."""
    for name in _DRUG_NAME.finditer(text):
        without = _WITHOUT_DRUG.match(text, name.end())
        if without is None or _DRUG_USE.search(text, without.end()):
            return True
    if _hormone_intake(text) or _DRUG_REQUEST.search(text):
        return True
    return bool(_DOSE.search(text) and _ANY_DRUG.search(text))


def _medication(text: str) -> bool:
    intent = _drug_intent(text)
    if intent is not None:
        return intent == "decision"
    return _listed_medication(text)


def _drug_context(text: str) -> bool:
    """약을 묻는 질문이었는지. 통과한 일반 지식 질문도 넣는다."""
    return _drug_intent(text) is not None or _listed_medication(text)


# 앞 질문의 약을 가리키는 말 ("그거 매일 먹어도 돼?", "그건 처방이 필요해?")
_REFERENCE = re.compile(r"(?<![가-힣])[그이저](?:거|것|걸|건|게)")
# 먹는 말 앞에 을·를로 말한 대상 ("약과를 먹으면", "우유를 자기 전에 마시면"). 은·는·이·가로 말한 낱말은 먹는 사람
# ("아이는 먹어도 돼?")일 수 있어 대상으로 보지 않는다
_OBJECT = re.compile(r"([가-힣A-Za-z]+)(?:을|를)(?=[^.?!,\n]{0,12}?(?:먹|복용|드시|드셔|마시|섭취))")
# 수와 단위로 말한 양 ("몇 알을", "두 개를")은 대상이 아니다
_AMOUNT_BEFORE = re.compile(r"(?:\d|몇|한|두|세|네|반)\s?$")


def _names_new_object(question: str) -> bool:
    """먹는 말의 대상을 을·를로 새로 말했는지. 앞 질문을 가리키는 말, 양, 약을 가리키는 낱말은 새 대상이 아니다."""
    drugs = [m.span() for m in _drug_mentions(question)]
    for m in _OBJECT.finditer(question):
        if _REFERENCE.match(m.group(1)) or _AMOUNT_BEFORE.search(question, 0, m.start(1)):
            continue
        if any(start < m.end(1) and m.start(1) < end for start, end in drugs):
            continue
        return True
    return False


def _continues(question: str) -> bool:
    """앞 질문의 약을 이어 묻는 질문인지. 앞 질문을 가리키는 말이 있거나 먹는 말의 대상을 새로 말하지 않았으면 그렇다.

    대상을 새로 말한 질문("약과를 먹으면 잠에 도움이 될까?")은 앞 질문과 관계없이 이번 질문만으로 판정해, 같은 질문이
    앞 대화에 따라 다르게 판정되지 않게 한다.
    """
    return _REFERENCE.search(question) is not None or not _names_new_object(question)


def _dose_follow_up(question: str, previous: str) -> bool:
    """약 질문 바로 뒤에 그 약의 용량·복용을 묻는 질문인지 ("멜라토닌 먹어도 돼?" 다음 "그럼 하루 몇 mg이 적당해?")."""
    return _INTAKE_FOLLOW_UP.search(question) is not None and _drug_context(previous) and _continues(question)


def _medication_answer(question: str, previous: str | None) -> str:
    # 앞 질문에 이어 차단했으면 앞 질문까지 확인해 잠 관련인지 정한다
    return medication_answer(_sleep_related(question, previous))


def general_drug_fallback(question: str, previous: str | None = None) -> str | None:
    """가드를 통과한 약 일반 지식 질문이면 답이 검사에 걸렸을 때 대신 보낼 고정 답을, 아니면 None 을 반환한다.

    약 이름 없이 바로 앞 약 질문의 약을 가리키는 일반 지식 질문("그건 처방이 필요해?")도 넣는다.
    """
    text = question.strip()
    before = previous.strip() if previous else None
    intent = _drug_intent(text)
    if intent == "general":
        return medication_answer(_sleep_related(text))
    if intent is None and before and _GENERAL_ASK.search(text) and _drug_context(before) and _continues(text):
        return medication_answer(_sleep_related(text, before))
    return None


# 주제 낱말 뒤에 그 주제를 묻거나 요청하는 말이 올 때만 범위 밖 질문이다. "주식 때문에 못 잤어"는 수면 이야기다
_OFF_TOPIC = re.compile(
    r"(주식|코인|비트코인|로또|정치|선거)[^.?!,\n]{0,10}?"
    r"(추천|사도|사면\s?(돼|될까|좋|어때)|사야|살까|살지|사려고|팔까|팔아야|팔아도|팔면\s?(돼|될까|좋)|매수|매도"
    r"|오를까|떨어질까|오를지|떨어질지|전망\s?(어때|어떨까|좋을까)|어떻게\s?생각|의견|누구\s?(뽑|찍)"
    r"|누가\s?(이길|될|돼)|이길까|뭐야|뭐예요|투자\s?(해도|할까|하면|해야)"
    r"|(알려|가르쳐|설명해|분석해|예측해)\s?(줘|줄래|주세요|줄\s?수)"
    r"|(얘기|이야기)\s?(좀\s?)?(하자|할래|해\s?줘|해\s?볼까|나누자))"
)
_OTHER_TASK = _p(
    r"번역해",
    # 요청 어미만 받는다. "코드 짜느라 늦게 잤어", "코딩하다가 잤어"처럼 일상 문장에도 나온다.
    # "코드"는 인증·초대 코드에도 쓰여 짜·작성만 받는다
    rf"코드{_GAP}(짜|작성(\s?좀)?\s?해){_REQUEST}",
    rf"(프로그래밍|코딩){_GAP}(알려|가르쳐|짜|해){_REQUEST}",
)
# 건강 기록을 함께 물으면 기록 부분에 답할 수 있게 LLM 에 넘긴다
_RECORD_REQUEST = re.compile(
    r"(수면|잠|잤|잔\s?날|걸음|걸었|심박|산소포화도|운동|기록|데이터)[^.?!\n]{0,12}?"
    r"(보여|알려|정리|요약|몇|얼마|어때|어땠|비교|확인)"
)


def _out_of_scope(text: str) -> bool:
    if not (_OFF_TOPIC.search(text) or _OTHER_TASK.search(text)):
        return False
    return _RECORD_REQUEST.search(text) is None


_RELATION = r"(타인|남의|엄마|아빠|친구|아내|남편|와이프|동생|누나|언니|오빠)"
_METRIC = r"(수면|걸음|심박|건강|산소포화도|운동)"
_ADVERSARIAL = _p(
    r"(이전|위의|앞의|앞서|모든)\s?(지시|명령|규칙|지침|설정)(을|를|은|는)?\s?(무시|잊)",
    r"ignore\s+(all\s+|the\s+)?(previous|above|prior)?\s*instructions",
    r"시스템\s?프롬프트|system\s?prompt",
    r"(프롬프트|지침|규칙)(을|를)?\s?(보여|알려|출력)",
    # 관계 낱말 뒤 "이·가"는 그 사람이 말했다는 뜻이다("엄마가 건강 챙기래"). 조사가 없거나 "의"일 때만 기록의 주인이다
    _RELATION + r"(의)?\s?(" + _METRIC + r"(\s?수)?\s?)?(데이터|기록|정보|수치)",
    _RELATION + r"(의)?\s?" + _METRIC + r"(\s?수)?[^.?!,\n]{0,5}?(보여|알려|조회|확인|비교|봐\s?줘|볼\s?수)",
    # 지표 낱말만 있으면 일반 지식 질문과 구분되지 않아 기록·데이터·정보가 있어야 판정한다
    r"다른\s?사람(의)?\s?((수면|걸음|심박|건강)(\s?수)?\s?)?(데이터|기록|정보)",
    # 복수형과 중간 낱말은 서비스 사용자에게만 허용한다. "다른 사람들"은 일반 지식 질문에도 쓰인다
    rf"다른\s?(사용자|회원|유저)들?{_GAP}(데이터|기록|수면|걸음|심박|건강|정보)",
    r"user_?id|사용자\s?id|회원\s?번호",
)


def _adversarial(text: str) -> bool:
    return _ADVERSARIAL.search(text) is not None


# 순서가 우선순위다. 다른 유형과 겹쳐도 응급 안내가 먼저다
RULES: tuple[GuardRule, ...] = (
    GuardRule(
        "emergency",
        _emergency,
        "말씀하신 증상은 응급 상황일 수 있어요. 지금도 증상이 있거나 다시 생기면 바로 119에 연락하거나 "
        "가까운 응급실로 가세요. 이 서비스는 응급 상황을 판단하거나 도와드릴 수 없어요.",
        _same_symptom_continues,
    ),
    GuardRule(
        "mental_crisis",
        _mental_crisis,
        "많이 힘드시군요. 혼자 견디지 마시고 지금 도움을 받으세요. "
        "자살예방상담전화 109(24시간)나 정신건강위기상담전화 1577-0199에 연락할 수 있어요. 위급하면 119에 연락하세요.",
    ),
    GuardRule(
        "diagnosis",
        _diagnosis,
        "질병이 있는지는 판단해 드릴 수 없어요. 걱정되는 증상이 있으면 의료진과 상담해 주세요. "
        "수면·걸음 수 같은 기록을 정리해 드리는 건 도와드릴 수 있어요.",
    ),
    GuardRule("medication", _medication, medication_answer(sleep=False), _dose_follow_up, _medication_answer),
    GuardRule(
        "out_of_scope",
        _out_of_scope,
        "저는 건강 기록을 바탕으로 생활 습관을 돕는 코치라 그 질문에는 답하기 어려워요.",
    ),
    GuardRule(
        "adversarial",
        _adversarial,
        "그 요청은 도와드릴 수 없어요. 본인의 건강 기록에 대해 물어봐 주세요.",
    ),
)


def check(question: str, previous: str | None = None) -> GuardResult | None:
    """걸리는 첫 규칙의 결과를 반환한다. 통과하면 None.

    previous 는 바로 앞 사용자 질문이다. 약 질문 뒤의 용량·복용 질문과, 응급 증상 뒤에 같은 증상이 이어진다는
    질문(지금도·아직도·계속)만 앞 질문을 참조한다.
    """
    text = question.strip()
    before = previous.strip() if previous else None
    for rule in RULES:
        if rule.detect(text):
            return GuardResult(rule.category, _answer(rule, text, None))
        if before and rule.follow_up is not None and rule.follow_up(text, before):
            return GuardResult(rule.category, _answer(rule, text, before))
    return None


def _answer(rule: GuardRule, question: str, previous: str | None) -> str:
    return rule.answer if rule.choose is None else rule.choose(question, previous)
