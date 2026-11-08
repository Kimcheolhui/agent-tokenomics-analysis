#!/usr/bin/env python3
"""REPORT 문장의 문체 검사. 비유적 표현과 AI체를 찾아 대체 표현을 제안한다.

기준은 docs/writing-style.md다. 이 목록은 작성자를 판정하는 기준이 아니라 교정 체크리스트다.
기술 용어로 굳은 말(꼬리, 쏠림 등)은 패턴에 넣지 않았다. 그 판단은 docs/writing-style.md의 "남겨 둔 용어"에 있다.

예:
  python3 scripts/lint_text.py out/report.json            REPORT의 문장
  python3 scripts/lint_text.py report/token-diagnosis-report.html   REPORT와 템플릿 고정 문구
  python3 scripts/lint_text.py docs/foo.md                 일반 텍스트 파일
종료 코드: 0 = 지적 없음, 1 = 지적 있음.
"""
import json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# (분류, 정규식, 지적 이유, 대체 방향)
PATTERNS = [
    # 대비·대칭 구문
    ("대비", r"(이|가) 아니(라|고)\b", "'A가 아니라 B' 대비 구문. 바로 서술한다", "B를 직접 쓴다"),
    ("대비", r"뿐(만)? 아니라", "'뿐만 아니라' 대비 구문", "두 문장으로 나누거나 한 가지만 쓴다"),
    ("대비", r"(이|가)기도 하(고|며)", "'A이기도 하고 B이기도 하다' 대칭 구문", "사실 하나씩 쓴다"),
    ("대비", r"단순(한|히)\s*\S+(이|가)?\s*아니", "'단순한 A가 아니라' 구문", "A를 빼고 B만 쓴다"),
    # 접속 부사와 맺음말 습관
    ("접속", r"(^|[.!?]\s)(결론적으로|종합하면|요약하면|정리하면|다시 말해|이처럼|한편)", "문두 접속 부사·맺음 표지", "삭제하고 내용만 쓴다"),
    ("접속", r"(^|[.!?]\s)(또한|따라서|그러나|하지만),?\s", "문두 접속 부사. 앞 문장과의 관계가 문장 안에서 드러나면 뺀다", "관계가 필요하면 근거를 쓴다"),
    ("접속", r"이러한", "'이러한' 지시어", "'이', 또는 지시 대상을 직접 쓴다"),
    ("맺음", r"(중요한 것은|핵심은|이유는 단순하다|관건은|주목할 (만한|필요))", "결론을 선언하는 틀", "사실을 바로 쓴다"),
    ("맺음", r"(하는 것이 중요하|가 중요하다|이 중요하다|할 필요가 있다|해야 할 시점|고민해야|성찰)", "무난한 맺음말", "해야 할 일을 구체적으로 쓴다"),
    ("맺음", r"(라고 (볼|할) 수 있다|라고 할 수 있|할 수 있을 것이다|것으로 보인다|것으로 판단된다)", "완곡한 단정 회피", "근거가 충분하면 단정하고, 아니면 `후보`로 표시한다"),
    ("맺음", r"(셈이다|하는 셈)", "'~한 셈이다'", "사실을 바로 쓴다"),
    # 과장·마케팅 어휘
    ("과장", r"(혁신적|획기적|강력한|탁월|심오|본질적|패러다임|시너지|핵심적|핵심 동력|결정적(인|으로) (역할|영향|차이))", "과장·마케팅 어휘", "수치나 사실로 바꾼다"),
    ("과장", r"(매우|굉장히|엄청|극도로|상당히|크게 우려|심각한)", "강조 부사·평가어", "수치로 바꾼다"),
    ("과장", r"(다양한|효과적(인|으로)?|중요한 역할)", "내용 없는 상투 어휘", "무엇이 몇 가지인지, 어떻게 효과가 있는지 쓴다"),
    # 모호한 수량
    ("모호", r"수[천백십만]\s*(토큰|건|회|개|달러)", "범위가 모호한 수량 표현", "측정한 수치를 쓴다"),
    ("모호", r"(다수의|대다수|상당수|사실상|거의 대부분)", "범위가 모호한 표현", "비율이나 건수를 쓴다"),
    # 번역투
    ("번역투", r"(기능합니다|기능한다|역할을 한다|역할을 수행)", "'serves as' 번역투", "'~이다'로 쓴다"),
    ("번역투", r"(에 의해|에 의하여)\s", "피동 번역투", "능동으로 쓴다"),
    ("번역투", r"(이 요구된다|이 강조된다|이 논의되|이 조성|이 필요시)", "주어가 없는 피동", "누가 무엇을 해야 하는지 쓴다"),
    ("번역투", r"(재검증|교차 확인)했", "'re-verified' 번역투", "무엇을 어떻게 확인했는지 쓴다"),
    # 은유·비유
    ("은유", r"(떠받|삼켜|숨은|숨겨진|발목|열쇠|돌파|지형|여정|뿌리|병목|파고들|꿰뚫|짚어|조용히|살얼음|뇌관|덫|함정)", "은유 표현", "직접 표현으로 바꾼다"),
    ("은유", r"(가파르|불어난|불어나|부풀|맴돌|밀려난|밀려나|새어|새는|쏟아|치솟|곤두박질|폭증|급락)", "움직임 은유", "'크게 늘어난다', '줄어든다'와 같이 수치로 쓴다"),
    ("은유", r"(싣는다|싣고|실린다|실려|다시 싣)", "'싣다' 은유", "'포함한다', '전송한다'로 쓴다"),
    ("은유", r"(몰린|몰림|몰려)", "'몰리다' 은유", "'집중된'으로 쓴다"),
    ("은유", r"(가린다|가려)", "'가리다' 은유", "'확인하기 어렵게 한다'와 같이 쓴다"),
    ("은유", r"(몫|자리별|첫 자리|어느 자리)", "'몫', '자리' 은유", "'비중', '위치'로 쓴다"),
    ("은유", r"(한눈에|큰 그림|밑그림|들여다|들춰|살펴보자|짚고)", "시각·탐색 은유", "'확인한다', '비교한다'로 쓴다"),
    # 서술 습관
    ("서술", r"(보여 ?준다|말해 ?준다|시사한다|암시한다|드러낸다|방증)", "'~를 보여 준다' 해석 서술", "수치를 쓰고 해석은 판정 규칙에 맡긴다"),
    ("서술", r"(일까|인가\?|할까)", "수사 의문", "서술문으로 쓴다"),
    # 서식
    ("서식", r"\*\*[^*]+\*\*", "굵은 글씨 서식", "서식 없이 쓴다"),
    ("서식", "[\U0001F300-\U0001FAFF\u2705\u274C\u2728\u2B50\u26A0]", "이모지", "삭제한다"),
    ("서식", r"\S\s—\s\S", "줄표(—)로 이은 문장", "마침표로 나누거나 쉼표를 쓴다"),
]
COMPILED = [(c, re.compile(p), why, fix) for c, p, why, fix in PATTERNS]


REPORT_ONLY = {r"\*\*[^*]+\*\*", r"\S\s—\s\S"}   # Markdown 문서의 굵은 글씨와 표의 줄표는 구조라서 REPORT에서만 지적한다


def lint(text, report=True):
    """text에서 지적을 [(분류, 일치한 문구, 이유, 대체 방향)]로 돌려준다. report=False면 Markdown 문서용 예외를 둔다."""
    hits = []
    for cat, rx, why, fix in COMPILED:
        if not report and rx.pattern in REPORT_ONLY:
            continue
        for m in rx.finditer(text):
            hits.append((cat, m.group(0).strip(), why, fix))
    return hits


# ── REPORT에서 문장 필드를 모은다 ────────────────────────────────────────
PROSE_KEYS = {"title", "sub", "note", "meta", "name", "metric", "display", "thresholdText", "rule", "finding",
              "basis", "formula", "inputs", "aTag", "bTag", "label", "aside", "tag", "xLabel", "xName", "caption"}


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, str) and (k in PROSE_KEYS or k in ("note",)):
                yield p, v
            else:
                yield from walk(v, p)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            if isinstance(v, str) and path.split(".")[-1] in ("summary", "differential"):
                yield f"{path}[{i}]", v
            else:
                yield from walk(v, f"{path}[{i}]")


def report_texts(rep):
    for p, t in walk(rep):
        if p.startswith("categories"):
            continue
        yield p, t


def style_warnings(rep):
    """REPORT 객체의 문장을 검사해 경고 문자열 목록으로 돌려준다."""
    out = []
    for path, t in report_texts(rep):
        for cat, frag, why, fix in lint(t):
            out.append(f"[style:{cat}] {path}: '{frag}' ({why}) → {fix}")
    return out


def template_static_texts(html):
    """REPORT 객체 밖의 스크립트 문자열 중 한글이 있는 것. 템플릿이 고정으로 그리는 문구다."""
    from report_io import START, END
    a = html.index(START)
    b = html.index(END, a) + len(END)
    rest = html[:a] + html[b:]
    out = []
    for m in re.finditer(r"""(['`"])((?:\\.|(?!\1).)*?)\1""", rest, flags=re.S):
        s = m.group(2)
        if re.search(r"[가-힣]", s) and len(s) > 6 and "{" not in s[:1]:
            out.append((f"template:{m.start()}", s))
    # <body>의 정적 HTML 텍스트
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    for t in re.findall(r">([^<>]*[가-힣][^<>]*)<", body):
        if len(t.strip()) > 6:
            out.append(("html", t.strip()))
    return out


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    p = pathlib.Path(sys.argv[1])
    text = p.read_text(encoding="utf-8")
    items = []
    if p.suffix == ".json":
        items = list(report_texts(json.loads(text)))
    elif p.suffix in (".html", ".htm"):
        from report_io import extract_report
        items = list(report_texts(extract_report(text))) + template_static_texts(text)
    else:
        items = [(f"line {i + 1}", ln) for i, ln in enumerate(text.splitlines())]
    is_report = p.suffix in (".json", ".html", ".htm")
    n = 0
    for path, t in items:
        for cat, frag, why, fix in lint(t, report=is_report):
            n += 1
            print(f"[{cat}] {path}: '{frag}' — {why}. → {fix}")
    print(f"{n}건")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
