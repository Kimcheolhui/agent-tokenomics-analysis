#!/usr/bin/env python3
"""REPORT 객체가 템플릿이 기대하는 모양인지 검사한다. 스키마는 docs/report-schema.md와 같다.

예:
  python3 scripts/validate_report.py out/report.json
  python3 scripts/validate_report.py report/token-diagnosis-report.html
종료 코드: 0 = 통과, 1 = 오류 있음. 경고는 종료 코드에 영향을 주지 않는다.
"""
import json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from report_io import extract_report  # noqa: E402

VERDICTS = ["해당", "후보", "비해당", "계측 부재", "판정 보류", "미점검"]
STATUSES = ["abnormal", "normal", "nodata", "hold"]
FMTS = ["int", "pct", "ratio", "tok", "usd", "x"]
CHART_TYPES = ["hbar", "stackbar", "segbar", "columns", "line", "box", "diff", "listdiff", "pairs"]
CATEGORIES = list("ABCDEF")
CAUSE_IDS = ["A1", "A2", "A3", "A4", "A5", "A6", "B1", "B2", "B3", "B4", "C1", "C2", "C3", "D1", "D2", "D3", "D4",
             "E1", "E2", "E3", "E4", "F1", "F2", "F3", "F4"]
SYMPTOM_IDS = [f"S{i}" for i in range(1, 13)]
NUM = (int, float)


class V:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, path, msg):
        self.errors.append(f"{path}: {msg}")

    def warn(self, path, msg):
        self.warnings.append(f"{path}: {msg}")

    # --- 원시 검사 ---------------------------------------------------------
    def req(self, o, key, path, kind=None, enum=None):
        if not isinstance(o, dict) or key not in o or o[key] is None:
            self.err(path, f"필수 필드 '{key}'가 없다")
            return None
        v = o[key]
        self.kind(v, f"{path}.{key}", kind)
        if enum is not None and v not in enum:
            self.err(f"{path}.{key}", f"값 '{v}'은 {enum} 중 하나여야 한다")
        return v

    def opt(self, o, key, path, kind=None, enum=None):
        if isinstance(o, dict) and o.get(key) is not None:
            v = o[key]
            self.kind(v, f"{path}.{key}", kind)
            if enum is not None and v not in enum:
                self.err(f"{path}.{key}", f"값 '{v}'은 {enum} 중 하나여야 한다")
            return v
        return None

    def kind(self, v, path, kind):
        if kind is None:
            return
        ok = {"str": isinstance(v, str), "num": isinstance(v, NUM) and not isinstance(v, bool),
              "int": isinstance(v, int) and not isinstance(v, bool), "bool": isinstance(v, bool),
              "list": isinstance(v, list), "dict": isinstance(v, dict)}[kind]
        if not ok:
            self.err(path, f"{kind} 이어야 하는데 {type(v).__name__}이다")


def check_meta(v, m):
    p = "meta"
    if not isinstance(m, dict):
        return v.err(p, "객체여야 한다")
    for k in ("title", "customer", "service", "project", "appInsights", "models", "sources", "author", "date"):
        v.req(m, k, p, "str")
    v.req(m, "sample", p, "bool")
    per = v.req(m, "period", p, "dict")
    if per:
        for k in ("from", "to"):
            x = v.req(per, k, p + ".period", "str")
            if x and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
                v.err(f"{p}.period.{k}", "YYYY-MM-DD 형식이어야 한다")
        v.req(per, "days", p + ".period", "int")
    ag = v.req(m, "agents", p, "list")
    if ag is not None and not ag:
        v.err(p + ".agents", "비어 있다")
    for k in ("totalCost", "fixedInput", "peff"):
        v.req(m, k, p, "num")
    for k in ("turns", "calls"):
        v.req(m, k, p, "int")
    cr = v.req(m, "cacheRate", p, "num")
    if isinstance(cr, NUM) and not 0 <= cr <= 1:
        v.err(p + ".cacheRate", "0–1 사이여야 한다")
    pr = v.req(m, "prices", p, "list")
    for i, x in enumerate(pr or []):
        v.req(x, "model", f"{p}.prices[{i}]", "str")
        for k in ("pin", "pcache", "pout"):
            v.req(x, k, f"{p}.prices[{i}]", "num")
    sm = v.req(m, "summary", p, "list")
    if sm is not None and not sm:
        v.err(p + ".summary", "요약 문장이 최소 1개 필요하다")
    for i, s in enumerate(sm or []):
        v.kind(s, f"{p}.summary[{i}]", "str")


def check_categories(v, c):
    if not isinstance(c, dict):
        return v.err("categories", "객체여야 한다")
    for L in CATEGORIES:
        if L not in c:
            v.err("categories", f"범주 '{L}'가 없다")
        else:
            v.req(c[L], "name", f"categories.{L}", "str")
            v.req(c[L], "term", f"categories.{L}", "str")


def check_symptoms(v, ss):
    if not isinstance(ss, list):
        return v.err("symptoms", "배열이어야 한다")
    ids = [s.get("id") for s in ss if isinstance(s, dict)]
    if ids != SYMPTOM_IDS:
        v.err("symptoms", f"id가 {SYMPTOM_IDS} 순서여야 한다 (현재 {ids})")
    for i, s in enumerate(ss):
        p = f"symptoms[{i}]({s.get('id')})"
        v.req(s, "name", p, "str")
        v.req(s, "metric", p, "str")
        st = v.req(s, "status", p, "str", STATUSES)
        cs = v.req(s, "causes", p, "list")
        for c in cs or []:
            if c not in CAUSE_IDS:
                v.err(p + ".causes", f"모르는 원인 '{c}'")
        has_num = s.get("value") is not None and s.get("threshold") is not None
        has_text = bool(s.get("display")) and bool(s.get("thresholdText"))
        if st in ("abnormal", "normal"):
            if not (has_num or has_text):
                v.err(p, "abnormal/normal은 (value, threshold, dir, fmt) 또는 (display, thresholdText)가 필요하다")
        if has_num:
            v.req(s, "dir", p, "str", ["higher", "lower"])
            v.req(s, "fmt", p, "str", FMTS)
            v.kind(s["value"], p + ".value", "num")
            v.kind(s["threshold"], p + ".threshold", "num")
        if st == "nodata" and not s.get("note"):
            v.warn(p, "nodata인데 note(이유)가 없다")
        if st == "hold" and not (s.get("thresholdText") or s.get("display")):
            v.warn(p, "hold인데 thresholdText가 없다")


def check_chart(v, ch, p):
    t = v.req(ch, "type", p, "str", CHART_TYPES)
    v.req(ch, "title", p, "str")
    v.opt(ch, "sub", p, "str")
    v.opt(ch, "wide", p, "bool")
    v.opt(ch, "fmt", p, "str", FMTS)
    if t in ("hbar", "stackbar", "segbar", "columns", "line", "box") and not ch.get("fmt"):
        v.err(p, f"{t}은 fmt가 필요하다")

    def rows(key, fn):
        lst = v.req(ch, key, p, "list")
        for i, x in enumerate(lst or []):
            fn(x, f"{p}.{key}[{i}]")
        if lst is not None and not lst:
            v.err(f"{p}.{key}", "비어 있다")
        return lst

    if t == "hbar":
        def hb(x, q):
            v.req(x, "label", q, "str"); v.req(x, "value", q, "num")
        rows("rows", hb)
        th = ch.get("threshold")
        if th is not None:
            v.req(th, "value", p + ".threshold", "num")
            v.req(th, "dir", p + ".threshold", "str", ["above", "below"])
            v.req(th, "label", p + ".threshold", "str")
        v.opt(ch, "max", p, "num")
    elif t == "stackbar":
        se = v.req(ch, "series", p, "list")
        n = len(se or [])
        def sb(x, q):
            v.req(x, "label", q, "str")
            sg = v.req(x, "segs", q, "list")
            if sg is not None and len(sg) != n:
                v.err(q + ".segs", f"series 수({n})와 길이가 다르다")
        rows("rows", sb)
    elif t == "segbar":
        def sgb(x, q):
            v.req(x, "name", q, "str"); v.req(x, "value", q, "num")
        rows("parts", sgb)
    elif t == "columns":
        # 두 형태: 단일 시리즈(bins[].value) 또는 누적(series + bins[].segs)
        bins = ch.get("bins")
        stacked = isinstance(bins, list) and bins and isinstance(bins[0], dict) and "segs" in bins[0]
        n = 0
        if stacked:
            se = v.req(ch, "series", p, "list")
            n = len(se or [])
        def cb(x, q):
            v.req(x, "label", q, "str")
            if stacked:
                sg = v.req(x, "segs", q, "list")
                if sg is not None and len(sg) != n:
                    v.err(q + ".segs", f"series 수({n})와 길이가 다르다")
            else:
                v.req(x, "value", q, "num")
        rows("bins", cb)
        for i, mk in enumerate(ch.get("markers") or []):
            v.req(mk, "at", f"{p}.markers[{i}]", "num"); v.req(mk, "label", f"{p}.markers[{i}]", "str")
    elif t == "line":
        def pt(x, q):
            v.req(x, "x", q, "str"); v.req(x, "y", q, "num")
        rows("points", pt)
        th = ch.get("threshold")
        if th is not None:
            v.req(th, "value", p + ".threshold", "num"); v.req(th, "label", p + ".threshold", "str")
    elif t == "box":
        def bx(x, q):
            v.req(x, "label", q, "str")
            a = v.req(x, "v", q, "list")
            if a is not None:
                if len(a) != 5 or not all(isinstance(z, NUM) for z in a):
                    v.err(q + ".v", "숫자 5개 [최소, Q1, 중앙값, Q3, 최대]여야 한다")
                elif a != sorted(a):
                    v.err(q + ".v", "오름차순이어야 한다")
        rows("rows", bx)
    elif t == "diff":
        v.req(ch, "aTag", p, "str"); v.req(ch, "bTag", p, "str")
        def df(x, q):
            if not isinstance(x, dict):
                return v.err(q, "객체여야 한다")
            ok = "same" in x or "more" in x or ("a" in x and "b" in x)
            if not ok:
                v.err(q, "same, more, 또는 (a, b) 중 하나가 필요하다")
        rows("lines", df)
    elif t == "listdiff":
        v.req(ch, "aTag", p, "str"); v.req(ch, "bTag", p, "str")
        v.req(ch, "a", p, "list"); v.req(ch, "b", p, "list")
    elif t == "pairs":
        def pr(x, q):
            v.req(x, "a", q, "str"); v.req(x, "b", q, "str")
        rows("pairs", pr)


def check_causes(v, cs, symptom_ids):
    if not isinstance(cs, list):
        return v.err("causes", "배열이어야 한다")
    ids = [c.get("id") for c in cs if isinstance(c, dict)]
    if ids != CAUSE_IDS:
        v.err("causes", f"id가 A1–F4 25개 순서여야 한다 (현재 {ids})")
    for i, c in enumerate(cs):
        p = f"causes[{i}]({c.get('id')})"
        v.req(c, "name", p, "str")
        vd = v.req(c, "verdict", p, "str", VERDICTS)
        v.req(c, "rule", p, "str")
        fr = v.req(c, "from", p, "list")
        for s in fr or []:
            if s not in symptom_ids:
                v.err(p + ".from", f"모르는 증상 '{s}'")
        w = c.get("waste")
        card = vd in ("해당", "후보") and w
        if w is not None:
            v.kind(w, p + ".waste", "dict")
            if isinstance(w, dict):
                v.req(w, "usd", p + ".waste", "num")
                v.req(w, "formula", p + ".waste", "str")
                v.req(w, "inputs", p + ".waste", "str")
                v.opt(w, "bound", p + ".waste", "bool")
                if isinstance(w.get("usd"), NUM) and w["usd"] < 0:
                    v.err(p + ".waste.usd", "음수다")
        if vd in ("해당", "후보"):
            v.req(c, "finding", p, "str")
            if not w:
                v.warn(p, "해당/후보인데 낭비 추정(waste)이 없어 3장 카드가 만들어지지 않는다. basis가 있어야 4장 표에 나온다")
        if not card:
            if not c.get("basis"):
                v.err(p, "카드가 아닌 원인은 basis(판정 근거)가 필요하다 — 4장 표에 나온다")
        for j, d in enumerate(c.get("differential") or []):
            v.kind(d, f"{p}.differential[{j}]", "str")
        for j, ch in enumerate(c.get("charts") or []):
            check_chart(v, ch, f"{p}.charts[{j}]")
        if card and not c.get("charts"):
            v.warn(p, "카드인데 charts가 없다")


def validate(R):
    v = V()
    for k in ("meta", "categories", "symptoms", "causes"):
        if k not in R:
            v.err("(root)", f"'{k}'가 없다")
    if v.errors:
        return v
    check_meta(v, R["meta"])
    check_categories(v, R["categories"])
    check_symptoms(v, R["symptoms"])
    check_causes(v, R["causes"], [s.get("id") for s in R["symptoms"]])
    return v


def load(path):
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".html", ".htm"):
        return extract_report(text)
    return json.loads(text)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    v = validate(load(sys.argv[1]))
    for w in v.warnings:
        print(f"[warn]  {w}")
    for e in v.errors:
        print(f"[error] {e}")
    print(f"{len(v.errors)} errors, {len(v.warnings)} warnings")
    return 1 if v.errors else 0


if __name__ == "__main__":
    sys.exit(main())
