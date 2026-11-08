#!/usr/bin/env python3
"""out/metrics.json에서 REPORT 객체를 만들어 보고서 HTML에 넣는다.

판정은 두 층이다.
  1) 이 스크립트가 텔레메트리 지표와 config/thresholds.csv로 판정한다. [C]·[S] 확인이 필요한 원인은 `후보`까지만 간다.
  2) 사람(또는 Agent)이 원문·코드를 리뷰한 뒤 review.json으로 판정을 올리거나 내린다.

예:
  python3 scripts/build_report.py --metrics out/metrics.json --root . --meta config/report-meta.json
  python3 scripts/build_report.py --metrics out/metrics.json --review out/review.json --out out/report.html

review.json 형식은 docs/build-mapping.md에 있다.
"""
import argparse, csv, json, math, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from report_io import extract_report, inject_report   # noqa: E402
from validate_report import validate                    # noqa: E402

REPO = HERE.parent


# ───────────────────────── 표기 ─────────────────────────
def n0(x):
    return f"{x:,.0f}"


def usd(x):
    return f"${x:,.2f}" if abs(x) < 10 else f"${x:,.0f}"


def pc(x, d=0):
    return f"{x * 100:.{d}f}%"


def f2(x):
    return f"{x:.2f}"


# ───────────────────────── 컨텍스트 ─────────────────────────
class Ctx:
    def __init__(self, metrics, root, tmpl, review):
        self.m = metrics
        self.tmpl = tmpl
        self.review = review or {}
        self.th, self.dir = {}, {}
        for base in (root, REPO):
            f = base / "config" / "thresholds.csv"
            if f.exists():
                for r in csv.DictReader(f.open(encoding="utf-8")):
                    self.th[r["Key"]] = (r["Value"] or "").strip()
                    self.dir[r["Key"]] = r["Dir"]
                break
        self.params = {}
        for base in (root, REPO):
            f = base / "config" / "model-params.csv"
            if f.exists():
                self.params = {r["Model"]: r for r in csv.DictReader(f.open(encoding="utf-8"))}
                break
        c = metrics.get("_checks", {})
        self.instr = (c.get("instrumentation") or [{}])[0]
        self.pres = (c.get("data_presence") or [{}])[0]
        self.over = (c.get("overview") or [{}])[0]
        self.unpriced = c.get("unpriced_calls") or []
        self.missing_sa = c.get("missing_sa") or []
        self.errors = []
        self.have_cache = (self.instr.get("WithCacheRead") or 0) > 0
        self.have_reason = (self.instr.get("WithReasoning") or 0) > 0
        self.have_conv = (self.instr.get("WithConvId") or 0) > 0
        self.have_content = (self.pres.get("ContentRows") or 0) > 0
        self.have_tools = (self.pres.get("ToolRows") or 0) > 0
        self.have_schemas = (self.pres.get("ToolSchemaRows") or 0) > 0
        self.have_retr = (self.pres.get("RetrievalRows") or 0) > 0
        self.have_sub = (self.pres.get("SubAgentRows") or 0) > 0
        ac = self.q("A-common", "agent_cache")
        self.sa_agents = [r["Agent"] for r in ac if r.get("HasSa")]
        self.window_min = min([float(p["WindowMin"]) for p in self.params.values()] or [5.0])
        self.rpm_limit = min([int(float(p["RpmLimit"])) for p in self.params.values()] or [15])
        self.cpt = self.t("chars_per_token") or 4
        self.min_n = self.t("min_sample") or 30

    # 지표 접근
    def q(self, fid, name):
        v = (self.m.get(fid) or {}).get(name)
        if isinstance(v, dict) and "error" in v:
            self.errors.append(f"{fid}.{name}: {v['error']}")
            return []
        return v or []

    def t(self, key):
        v = self.th.get(key, "")
        try:
            return float(v)
        except ValueError:
            return None

    def ts(self, key):
        return self.th.get(key, "") or None

    def bad(self, key, value):
        """value가 key의 판정 기준을 넘었는가. higher는 이상, lower는 이하."""
        thr = self.t(key)
        if thr is None or value is None:
            return None
        return value >= thr if self.dir.get(key) == "higher" else value < thr


# ───────────────────────── 판정 결과 ─────────────────────────
def R(verdict, finding=None, basis=None, waste=None, diff=None, charts=None):
    d = {"verdict": verdict}
    if finding:
        d["finding"] = finding
    if basis:
        d["basis"] = basis
    if waste and waste.get("usd", 0) > 0:
        d["waste"] = waste
    if diff:
        d["differential"] = diff
    if charts:
        d["charts"] = charts
    return d


def W(usd_, formula, inputs, bound=False):
    w = {"usd": round(usd_, 2), "formula": formula, "inputs": inputs}
    if bound:
        w["bound"] = True
    return w


def NODATA(why): return R("계측 부재", basis=why)
def SKIP(why): return R("미점검", basis=why)
def HOLD(why): return R("판정 보류", basis=why)
def NO(why): return R("비해당", basis=why)


def hbar(title, sub, rows, fmt, threshold=None, max_=None, wide=False, label_width=None):
    ch = {"type": "hbar", "title": title, "sub": sub, "fmt": fmt, "rows": rows}
    if threshold:
        ch["threshold"] = threshold
    if max_:
        ch["max"] = max_
    if wide:
        ch["wide"] = True
    if label_width:
        ch["labelWidth"] = label_width
    return ch


def nice_max(v):
    if not v or v <= 0:
        return None
    e = 10 ** math.floor(math.log10(v))
    for k in (1, 2, 2.5, 5, 10):
        if v <= k * e:
            return k * e
    return 10 * e


# ───────────────────────── 증상 ─────────────────────────
def build_symptoms(c):
    out = []
    ov = c.q("A-common", "overall")
    ov = ov[0] if ov else {}
    ac = [r for r in c.q("A-common", "agent_cache") if r.get("HasSa")]
    th_ = sum(r["Theoretical"] for r in ac)
    ach = round(sum(r["Actual"] for r in ac) / th_, 3) if th_ else None
    c.achievement = ach                                   # 원인 판정에서 A 범주 게이트로 쓴다

    def worst(rows, key, w="Turns", direction="higher"):
        """에이전트 중 가장 나쁜 값. 증상은 "어느 에이전트에서든 기준을 넘는가"이므로 가중 평균은 문제를 가린다.
        표본이 min_sample보다 작은 에이전트는 뺀다."""
        rows = [r for r in rows if r.get(key) is not None and r.get(w, c.min_n) >= c.min_n]
        if not rows:
            return None
        vals = [r[key] for r in rows]
        return round(max(vals) if direction == "higher" else min(vals), 3)

    warm = c.q("A1", "warm_first_miss")
    wf, wm = sum(r["WarmFirstCalls"] for r in warm), sum(r["Miss"] for r in warm)
    fixed = c.q("B-common", "overall")
    fixed = fixed[0] if fixed else {}
    tg = c.q("C2", "turn_growth")
    ct = c.q("C1", "cross_turn")
    tail = c.q("D1", "tail_turns")
    dl = c.q("D4", "delegation")
    ft = c.q("F4", "failed_turns")
    sv = c.q("E1", "savings")
    sv = sv[0] if sv else {}

    def sym(sid, value=None, key=None, fmt="ratio", note=None, display=None, thr_text=None, status=None):
        t = next(s for s in c.tmpl["symptoms"] if s["id"] == sid)
        d = {"id": sid, "name": t["name"], "metric": t["metric"], "causes": t["causes"]}
        if display is not None:
            d["display"], d["thresholdText"] = display, thr_text
            d["value"], d["threshold"] = None, None
            d["status"] = status or "normal"
        elif value is None:
            d["status"] = "nodata"
            d["value"], d["threshold"], d["fmt"], d["dir"] = None, c.t(key) if key else None, fmt, c.dir.get(key, "higher")
        else:
            d["value"], d["threshold"] = value, c.t(key)
            d["fmt"], d["dir"] = fmt, c.dir.get(key, "higher")
            d["status"] = "abnormal" if c.bad(key, value) else "normal"
        if status and display is None:
            d["status"] = status
        if note:
            d["note"] = note
        return d

    out.append(sym("S1", ach, "cache_achievement", note="cache_read 미수집" if not c.have_cache else "S_a 미측정" if not c.sa_agents else None))
    out[-1]["status"] = "nodata" if (not c.have_cache or not c.sa_agents) else out[-1]["status"]
    s2 = round(wm / wf, 3) if wf else None
    out.append(sym("S2", s2 if c.have_cache else None, "a1_warm_miss", note=None if c.have_cache else "cache_read 미수집"))
    s3 = ov.get("AchievementSecondPlus")
    out.append(sym("S3", s3 if (c.have_cache and c.sa_agents) else None, "cache_mid_turn", note=None if c.have_cache else "cache_read 미수집"))
    out.append(sym("S4", fixed.get("FixedCostShare") if c.sa_agents else None, "b_fixed_share", note=None if c.sa_agents else "S_a 미측정"))
    out.append(sym("S5", worst(tg, "P50Multiple"), "c2_multiple", fmt="x"))
    s6 = max([r["CrossTurnMultiple"] for r in ct if r.get("CrossTurnMultiple") is not None] or [None]) if c.have_conv else None
    out.append(sym("S6", s6, "c1_multiple", fmt="x", note=None if c.have_conv else "gen_ai.conversation.id 미기록"))
    out.append(sym("S7", worst(tail, "TailCostShare"), "d1_tail_share"))
    s8 = worst([dict(r, v=r.get("P50PassRatioVar") if r.get("P50PassRatioVar") is not None else r.get("P50PassRatio"), Turns=r["Delegations"]) for r in dl], "v")
    out.append(sym("S8", s8, "d4_pass_ratio", note=None if dl else "하위 에이전트 위임이 없다"))
    # S9: 단순 단계의 reasoning 수준과 분포를 최종 응답 단계와 비교한다
    if not c.have_reason:
        out.append(sym("S9", display="reasoning 토큰 미기록", thr_text="수치 기준 없음 · 단계 비교", status="hold"))
        out[-1]["status"] = "nodata"
        out[-1]["note"] = "gen_ai.usage.reasoning.output_tokens 미수집"
    else:
        s9 = reasoning_view(c)
        out.append(sym("S9", display=s9["display"], thr_text="수치 기준 없음 · 단계 비교", status="abnormal" if s9["suspect"] else "normal"))
    # S10: 목표 길이가 있어야 판정한다
    fr = c.q("E3", "final_response")
    p95 = max([r["P95Visible"] for r in fr] or [0])
    tgt = c.t("e3_target_tok")
    if tgt is None:
        out.append(sym("S10", display=f"최종 응답 p95 {n0(p95)} tok", thr_text="목표 길이 미정", status="hold"))
    else:
        out.append(sym("S10", display=f"최종 응답 p95 {n0(p95)} tok", thr_text=f"목표 {n0(tgt)} tok", status="abnormal" if p95 > tgt else "normal"))
    if sv.get("SmallModelFound"):
        out.append(sym("S11", sv.get("SavingShare"), "e1_saving_share"))
    else:
        out.append(sym("S11", display="소형 모델 미지정", thr_text="e1_small_model을 model-params.csv에 등록한다", status="hold"))
    s12 = worst([dict(r, v=r.get("LateFailIndex")) for r in ft if r.get("LateFailIndex") is not None], "v")
    s12_note = None
    if s12 is None and ft and all((r.get("FailTurnRate") or 0) == 0 for r in ft):
        s12, s12_note = 0.0, "실패한 턴이 없다"
    out.append(sym("S12", s12, "f4_late_fail", fmt="x", note=s12_note))
    return out


def reasoning_view(c):
    """단순 단계(유형 1–3)와 최종 응답 단계(유형 4)의 reasoning을 비교한다."""
    rows = c.q("E2", "reasoning_by_type")
    best = None
    for ag in sorted({r["Agent"] for r in rows}):
        simple = [r for r in rows if r["Agent"] == ag and r["CallType"][0] in "123" and r["Calls"]]
        final = [r for r in rows if r["Agent"] == ag and r["CallType"][0] == "4"]
        if not simple or not final:
            continue
        med_s = max(r["Median"] for r in simple)
        med_f = max(r["Median"] for r in final)
        levels_s = {r["Level"] for r in simple}
        levels_f = {r["Level"] for r in final}
        suspect = med_s > 0 and (levels_s == levels_f or med_s >= 0.5 * med_f)
        cand = {"agent": ag, "med_s": med_s, "med_f": med_f, "ls": levels_s, "lf": levels_f, "suspect": suspect}
        if best is None or med_s > best["med_s"]:
            best = cand
    if not best:
        return {"display": "비교할 유형이 없다", "suspect": False, "agent": None}
    best["display"] = (f"유형 1–3 {'/'.join(sorted(best['ls']))} · ReasonTok p50 {n0(best['med_s'])}"
                       f" (최종 응답 {n0(best['med_f'])})")
    return best


# ───────────────────────── 원인 ─────────────────────────
def build_causes(c):
    F = {}
    ach = c.achievement
    a_gate = (c.have_cache and ach is not None and ach >= (c.t("cache_achievement") or 0.7))
    a_gate_msg = f"캐시 달성률 {f2(ach)}가 기준 {c.ts('cache_achievement')} 이상이다" if ach is not None else ""
    fixed = (c.q("B-common", "overall") or [{}])[0]
    b_gate = (c.sa_agents and fixed.get("FixedCostShare") is not None
              and fixed["FixedCostShare"] < (c.t("b_fixed_share") or 0.3))
    b_gate_msg = f"고정 Input 비용 비중 {f2(fixed.get('FixedCostShare') or 0)}가 기준 {c.ts('b_fixed_share')} 미만이다"

    def need_cache():
        if not c.have_cache:
            return NODATA("gen_ai.usage.cache_read.input_tokens 미수집")
        if not c.sa_agents:
            return SKIP("S_a 미측정 (config/agents.csv)")
        return None

    # ── A1 ──
    def A1():
        g = need_cache()
        if g: return g
        if a_gate: return NO(a_gate_msg)
        warm, sysv, inturn = c.q("A1", "warm_first_miss"), c.q("A1", "sys_variants"), c.q("A1", "inturn_misses")
        if not warm: return SKIP("보존 시간 내 첫 호출이 없다 (조건을 만족하는 호출 0건)")
        thr = c.t("a1_warm_miss")
        flagged = [r for r in warm if r["WarmMissRate"] >= thr and r["WarmFirstCalls"] >= c.min_n]
        if not flagged:
            return NO(f"보존 시간 내 첫 호출 미스율 최대 {f2(max(r['WarmMissRate'] for r in warm))} < 기준 {thr}")
        vr = {r["AgentName"]: r for r in sysv}
        conf = [r for r in flagged if (vr.get(r["Agent"]) or {}).get("VariantRatio", 0) >= c.t("a1_variant_ratio")]
        use = conf or flagged
        it = {r["Agent"]: r for r in inturn}
        total, parts, lines = 0.0, [], []
        for r in use:
            total += r["WasteUsd"]
            v = vr.get(r["Agent"])
            s = (f"{r['Agent']}의 보존 시간 내 턴 첫 호출 {n0(r['WarmFirstCalls'])}건 중 {n0(r['Miss'])}건({pc(r['WarmMissRate'])})이 캐시를 쓰지 못했다.")
            if v:
                s += f" System prompt 변형 비율은 {f2(v['VariantRatio'])}이고 앞 4,000자 변형이 {n0(v['HeadVariants'])}종이다."
            x = it.get(r["Agent"])
            if x and x["InTurnMissRate"] >= thr:
                total += x["WasteUsd"]
                s += f" 턴 안 두 번째 이후 호출도 {pc(x['InTurnMissRate'])}가 미스 나서 호출마다 템플릿을 다시 렌더링하는 유형으로 본다."
                parts.append(f"{r['Agent']} 턴 안 미스 {n0(x['InTurnMiss'])}건")
            lines.append(s)
            parts.append(f"{r['Agent']} 첫 호출 미스 {n0(r['Miss'])}건")
        a2 = {r["AgentName"]: r for r in c.q("A2", "tooldef_variants")}
        diff = [f"A5 캐시 만료: 직전 첫 호출과 {c.window_min:g}분 안에 들어온 호출만 골랐으므로 만료로 설명되지 않는다.",
                f"A6 쏠림: 분당 {c.rpm_limit}건 이하 구간만 골랐다."]
        for r in use:
            if r["Agent"] in a2:
                diff.append(f"A2 Tool 정의 변동: {r['Agent']}의 ToolDefinitions 원문 변형이 {a2[r['Agent']]['RawVariants']}종이다.")
        if not conf:
            diff.append("System prompt 변형 비율이 기준에 못 미치거나 content가 없어 `후보`로 둔다. 변형 2건의 첫 차이를 확인하면 확정할 수 있다.")
        ch = hbar("에이전트별 보존 시간 내 첫 호출 미스율",
                  f"쿼리: 보존 시간 내 첫 호출 미스율 (A1) · WindowMin {c.window_min:g}분, RpmLimit {c.rpm_limit}",
                  [{"label": r["Agent"], "value": r["WarmMissRate"], "aside": f"첫 호출 {n0(r['WarmFirstCalls'])}회"} for r in warm],
                  "pct", {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(r["WarmMissRate"] for r in warm)))
        return R("해당" if conf else "후보", " ".join(lines), waste=W(total, "Σ(미스 호출) Cacheable(캐시 가능 토큰) × (P_in − P_cache)", " + ".join(parts) + "의 캐시 가능 토큰 × 단가 차이"),
                 diff=diff, charts=[ch])
    F["A1"] = A1

    # ── A2 ──
    def A2():
        if not c.have_content: return SKIP("AppGenAIContent 없음 (content recording 또는 권한)")
        if a_gate: return NO(a_gate_msg)
        tv = c.q("A2", "tooldef_variants")
        if not tv: return SKIP("ToolDefinitions가 기록되지 않았다")
        hits = [r for r in tv if r["RawVariants"] > r["NameSetVariants"] or r["NameSetVariants"] >= 2]
        shares = c.q("A2", "variant_share")
        charts = []
        pick = (hits or tv)[0]["AgentName"]
        vs = [r for r in shares if r["AgentName"] == pick]
        if len(vs) >= 2:
            charts.append({"type": "listdiff", "title": "Tool 정의 순서 변형 두 건", "sub": "AppGenAIContent.ToolDefinitions",
                           "aTag": f"변형 1 · {pc(vs[0]['Share'])} 턴", "bTag": f"변형 2 · {pc(vs[1]['Share'])} 턴",
                           "a": vs[0]["ToolNames"].split("|"), "b": vs[1]["ToolNames"].split("|"),
                           "note": "순서가 바뀐 tool부터 뒤가 모두 다른 prefix가 된다."})
        if not hits:
            return R("비해당", basis="ToolDefinitions 원문 변형 " + ", ".join(f"{r['AgentName']} {r['RawVariants']}종" for r in tv) + ", 이름 집합 변형 각 1종", charts=charts)
        wa = {r["Agent"]: r for r in c.q("A2", "waste")}
        total = sum(wa.get(r["AgentName"], {}).get("WasteUsd", 0) for r in hits)
        fnd = " ".join(f"{r['AgentName']}의 ToolDefinitions는 원문 변형 {r['RawVariants']}종, 이름 집합 변형 {r['NameSetVariants']}종이다." for r in hits)
        return R("해당", fnd, waste=W(total, "가장 흔한 정의와 다른 정의를 쓴 턴 첫 호출 수 × Cacheable(S_a) × (P_in − P_cache)",
                                      f"다른 정의를 쓴 턴 첫 호출 {n0(sum(wa.get(r['AgentName'], {}).get('OffModalFirstCalls', 0) for r in hits))}건"), charts=charts)
    F["A2"] = A2

    # ── A3 ──
    def A3():
        if not c.have_cache: return NODATA("gen_ai.usage.cache_read.input_tokens 미수집")
        if not c.have_conv: return NODATA("gen_ai.conversation.id 미기록")
        if a_gate: return NO(a_gate_msg)
        sh = c.q("A3", "shrink")
        drop = c.t("a3_hit_drop")
        flagged = [r for r in sh if r["ShrinkEvents"] >= c.min_n and r["HitNormal"] is not None and r["HitAfterShrink"] is not None
                   and r["HitNormal"] - r["HitAfterShrink"] >= drop]
        prof = c.q("A3", "conv_call_profile")
        charts = [{"type": "columns", "wide": True, "title": "대화 안 호출별 Input 구성", "sub": "대화 안 호출 순번별 평균 CacheTok과 비캐시 Input",
                   "fmt": "tok", "xLabel": "대화 안 LLM 호출 순번", "xName": "호출", "series": [{"name": "캐시 히트"}, {"name": "비캐시 Input"}],
                   "bins": [{"label": str(r["ConvCallIdx"]), "segs": [r["AvgCached"], r["AvgNonCached"]]} for r in prof]}] if prof else None
        if not flagged:
            ev = sum(r["ShrinkEvents"] for r in sh)
            return R("비해당", basis=f"축소 이벤트 {n0(ev)}건, 축소 직후 히트율이 평상시보다 {c.ts('a3_hit_drop')} 이상 낮은 에이전트 없음", charts=charts)
        total = sum(r["UpperBoundWasteUsd"] for r in flagged)
        fnd = " ".join(f"{r['Agent']}에서 같은 대화 안 Input이 직전보다 줄어든 호출이 {n0(r['ShrinkEvents'])}건이고, 축소 직후 히트율은 {f2(r['HitAfterShrink'])}로 평상시 {f2(r['HitNormal'])}보다 낮다." for r in flagged)
        return R("후보", fnd, waste=W(total, "Σ(축소 이벤트) [Cacheable(편집 지점 이전 공유 길이) − CacheTok] × (P_in − P_cache)",
                                     "공유 길이를 몰라 직전 prefix 전체가 히트했어야 한다고 본 상한. `[C]` InputMessages 비교로 줄인다", True),
                 diff=["턴 경계마다 축소가 생기면 이전 턴의 tool 결과를 이력에서 빼는 구조다. 이때 다음 턴 첫 호출은 고정부까지만 히트한다."], charts=charts)
    F["A3"] = A3

    # ── A4 ──
    def A4():
        if not c.have_cache: return NODATA("gen_ai.usage.cache_read.input_tokens 미수집 (첫 호출 히트를 판정할 수 없다)")
        if not c.sa_agents: return SKIP("S_a 미측정 (config/agents.csv)")
        rows = c.q("A4", "small_calls")
        hits = [r for r in rows if r.get("STok") and r["MinTok"] and r["STok"] < r["MinTok"]
                and (r.get("FirstCallZeroHit") or 0) >= 0.95]
        if not hits:
            return NO("S_a < MinTok인 에이전트가 없거나 첫 호출이 캐시되고 있다")
        total = sum(r["SmallCost"] for r in hits)
        bas = " · ".join(f"{r['Agent']} S_a {n0(r['STok'])} < MinTok {n0(r['MinTok'])}, 실행 첫 호출 히트 {pc(1 - r['FirstCallZeroHit'])}" for r in hits)
        ch = hbar("에이전트별 고정 Input 토큰 S_a", "config/agents.csv · 점선은 MinTok",
                  [{"label": r["Agent"], "value": r["STok"], "aside": f"호출 {n0(r['Calls'])}건"} for r in rows], "tok",
                  {"value": hits[0]["MinTok"], "dir": "below", "label": f"MinTok {n0(hits[0]['MinTok'])}"})
        if total > 0:
            return R("해당", f"{bas}. 고정 prefix가 캐시 가능한 길이보다 짧아 첫 호출이 구조적으로 캐시되지 않는다.",
                     waste=W(total, "Σ(InTok < MinTok) Cost (구조적 한계)", f"캐시 불가 호출 {n0(sum(r['SmallCalls'] for r in hits))}건의 비용", True), charts=[ch])
        return R("해당", basis=bas + ". Input 전체는 MinTok 이상이라 비용 영향은 작다", charts=[ch])
    F["A4"] = A4

    # ── A5 ──
    def A5():
        g = need_cache()
        if g: return g
        if a_gate: return NO(a_gate_msg)
        gh = c.q("A5", "gap_hit")
        if not gh: return SKIP("연속 턴 첫 호출 간격을 계산할 수 없다")
        drop = c.t("a5_hit_drop")
        flagged, total = [], 0.0
        for ag in sorted({r["Agent"] for r in gh}):
            rs = [r for r in gh if r["Agent"] == ag]
            a = next((r for r in rs if r["Bucket"].startswith("a.")), None)
            out = [r for r in rs if not r["Bucket"].startswith("a.")]
            out = [r for r in out if r["FirstCalls"] >= c.min_n]
            if not a or not out or a["FirstCalls"] < c.min_n: continue
            n_out = sum(r["FirstCalls"] for r in out)
            hit_out = sum(r["HitRate"] * r["FirstCalls"] for r in out) / n_out if n_out else 0
            if a["HitRate"] - hit_out >= drop:
                flagged.append((ag, a, out, hit_out))
                total += sum(r["WasteUsd"] for r in out)
        main = max({r["Agent"] for r in gh}, key=lambda ag: sum(r["FirstCalls"] for r in gh if r["Agent"] == ag))
        rs = [r for r in gh if r["Agent"] == main]
        ch = {"type": "columns", "title": "요청 간격 구간별 첫 호출 히트율", "sub": f"쿼리: 요청 간격별 첫 호출 히트율 (A5) · {main} · WindowMin {c.window_min:g}분",
              "fmt": "pct", "yMax": 1, "capLabels": True, "xName": "간격",
              "bins": [dict(label=r["Bucket"][3:], value=r["HitRate"], n=f"{n0(r['FirstCalls'])}회", **({"tone": "crit"} if not r["Bucket"].startswith("a.") else {})) for r in rs]}
        if not flagged:
            return R("비해당", basis=f"보존 시간 밖 구간에서 히트율 하락이 {c.ts('a5_hit_drop')} 미만이다", charts=[ch])
        fnd = " ".join(f"{ag}의 보존 시간 안 첫 호출 히트율은 {f2(a['HitRate'])}이고 그 밖 구간은 {f2(h)}이다." for ag, a, o, h in flagged)
        return R("해당", fnd, waste=W(total, "Σ(보존 시간 밖 첫 호출) Cacheable(S_a) × (P_in − P_cache) × (안쪽 히트율 − 구간 히트율)",
                                     "구간별 첫 호출 수 × 히트율 차이 × 캐시 가능 토큰 × 단가 차이"),
                 diff=["보존 시간 안 구간의 히트율이 낮으면 A1·A2·A6을 함께 확인한다. 보존 시간은 `prompt_cache_retention` 설정을 [S]로 확인한다."], charts=[ch])
    F["A5"] = A5

    # ── A6 ──
    def A6():
        g = need_cache()
        if g: return g
        if a_gate: return NO(a_gate_msg)
        rp, ep = c.q("A6", "rpm_hit"), c.q("A6", "endpoint_hit")
        drop = c.t("a6_hit_drop")
        hot = []
        for ag in sorted({r["Agent"] for r in rp}):
            rs = [r for r in rp if r["Agent"] == ag]
            base = next((r for r in rs if r["RpmBucket"].startswith("a.")), None)
            for r in rs:
                if base and not r["RpmBucket"].startswith("a.") and r["Calls"] >= c.min_n and base["HitRate"] - r["HitRate"] >= drop and r["WasteUsd"] > 0:
                    hot.append((ag, base, r))
        spread = []
        for ag in sorted({r["Agent"] for r in ep}):
            rs = [r for r in ep if r["Agent"] == ag and r["Calls"] >= 30]
            if len(rs) >= 2 and max(r["HitRate"] for r in rs) - min(r["HitRate"] for r in rs) >= drop:
                spread.append((ag, rs))
        main = max({r["Agent"] for r in rp} or {None}, key=lambda ag: sum(r["Calls"] for r in rp if r["Agent"] == ag)) if rp else None
        charts = []
        if main:
            charts.append({"type": "columns", "title": "분당 요청 수 구간별 히트율", "sub": f"쿼리: 분당 요청 수 구간별 히트율 (A6) · {main} · RpmLimit {c.rpm_limit}",
                           "fmt": "pct", "yMax": 1, "capLabels": True, "xName": "구간",
                           "bins": [dict(label=r["RpmBucket"][3:], value=r["HitRate"], n=f"{n0(r['Minutes'])}분", **({"tone": "crit"} if not r["RpmBucket"].startswith("a.") else {})) for r in rp if r["Agent"] == main]})
        if not hot and not spread:
            return R("비해당", basis=f"기준 이하 구간 대비 요청이 집중된 구간의 히트율 하락이 {c.ts('a6_hit_drop')} 미만이고 엔드포인트별 히트율 차이도 작다", charts=charts)
        total = sum(r["WasteUsd"] for _, _, r in hot)
        parts = [f"{ag}의 히트율은 분당 {c.rpm_limit}건 이하 구간에서 {f2(b['HitRate'])}이고 {r['RpmBucket'][3:]} 구간에서 {f2(r['HitRate'])}이다." for ag, b, r in hot]
        parts += [f"{ag}는 엔드포인트별 히트율이 {f2(min(r['HitRate'] for r in rs))}–{f2(max(r['HitRate'] for r in rs))}로 갈린다." for ag, rs in spread]
        return R("해당" if hot else "후보", " ".join(parts), waste=W(total, "Σ(해당 구간 호출) Cacheable(S_a) × (기준 구간 히트율 − 해당 구간 히트율) × (P_in − P_cache)", "요청이 집중된 구간의 호출 수 × 히트율 차이 × 캐시 가능 토큰 × 단가 차이") if hot else None,
                 basis=None if hot else "엔드포인트별 히트율 차이만 있다. 게이트웨이 라우팅을 [S]로 확인한다", charts=charts)
    F["A6"] = A6

    # ── B ──
    def b_pre():
        if not c.have_schemas: return SKIP("tool 정의 크기가 없다 (AppGenAIContent 또는 tool_schemas)")
        if not c.sa_agents: return SKIP("S_a 미측정 (config/agents.csv)")
        if b_gate: return NO(b_gate_msg)
        return None

    def B1():
        g = b_pre()
        if g: return g
        if not c.have_tools: return SKIP("execute_tool span이 없어 사용률을 계산할 수 없다")
        tu = c.q("B1", "tool_use")
        thr = c.t("b1_use_rate")
        un = [r for r in tu if r["UseRate"] is not None and (r["AgentRuns"] or 0) >= c.min_n and r["UseRate"] < thr]
        ch = hbar("tool 정의 크기 (사용률 표시)", "쿼리: Tool별 사용률 (B1) · 정의 문자 수 ÷ chars_per_token인 근사",
                  [dict(label=f"{r['Agent']} · {r['ToolName']}", value=r["ApproxTok"], aside=f"사용 {pc(r['UseRate'], 1)}", **({"tone": "crit"} if r["UseRate"] < thr else {}))
                   for r in sorted(tu, key=lambda r: -r["ApproxTok"])[:10]], "tok", wide=True, label_width="14rem")
        if not un:
            return R("비해당", basis=f"사용률 {pc(thr)} 미만인 tool이 없다", charts=[ch])
        total = sum(r["IfUnusedWasteUsd"] for r in un)
        names = ", ".join(f"{r['Agent']}의 {r['ToolName']}(약 {n0(r['ApproxTok'])} tok)" for r in un)
        return R("후보", f"사용률이 {pc(thr)} 미만인 tool이 매 호출에 포함된다: {names}.",
                 waste=W(total, "Σ(미사용 tool) tool 토큰 × 에이전트 LLM 호출 수 × P_eff", "tool 토큰은 정의 문자 수 ÷ chars_per_token인 근사. 측정법 1·2로 확정한다"),
                 diff=["[S]로 드물지만 반드시 필요한 tool(예외 처리, 에스컬레이션)인지 확인한다. 필요하면 낭비에서 뺀다.",
                       "하위 에이전트의 tool은 요청한 에이전트 실행에 귀속시켰다."], charts=[ch])
    F["B1"] = B1

    def B2():
        g = b_pre()
        if g: return g
        ts = c.q("B2", "tool_schema_size")
        thr = c.t("b2_ratio")
        big = [r for r in ts if r["Multiple"] is not None and r["Multiple"] >= thr and r["MedianChars"]]
        ch = hbar("tool 정의 크기", "쿼리: Tool별 정의 크기 (B2) · 같은 에이전트 중앙값 대비",
                  [dict(label=f"{r['AgentName']} · {r['ToolName']}", value=r["ApproxTok"], aside=f"중앙값의 {r['Multiple']:.1f}배", **({"tone": "crit"} if r["Multiple"] >= thr else {}))
                   for r in sorted(ts, key=lambda r: -r["Chars"])[:10]], "tok", wide=True, label_width="14rem")
        if not big:
            return R("비해당", basis=f"같은 에이전트 tool 정의 크기 중앙값의 {c.ts('b2_ratio')}배 이상인 tool이 없다", charts=[ch])
        return R("후보", basis=", ".join(f"{r['AgentName']}의 {r['ToolName']} 중앙값의 {r['Multiple']:.1f}배(약 {n0(r['ApproxTok'])} tok)" for r in big) + ". 축약 가능 토큰은 [S] 리뷰로 정한다", charts=[ch])
    F["B2"] = B2

    def B3():
        g = b_pre()
        if g: return g
        sp = c.q("B3", "system_prompt_size")
        fx = {r["Agent"]: r for r in c.q("B-common", "fixed_share")}
        big = [r for r in sp if fx.get(r["AgentName"], {}).get("FixedCostShare", 0) >= c.t("b_fixed_share")]
        ch = hbar("에이전트별 System prompt 크기", "쿼리: 에이전트별 최신 System prompt (B3) · 문자 수 ÷ chars_per_token인 근사",
                  [{"label": r["AgentName"], "value": r["ApproxTok"], "aside": f"고정 Input 비용 비중 {pc(fx.get(r['AgentName'], {}).get('FixedCostShare', 0))}"} for r in sp], "tok")
        if not big:
            return R("비해당", basis="고정 Input 비용 비중이 기준 미만인 에이전트의 System prompt만 있다", charts=[ch])
        return R("후보", basis=", ".join(f"{r['AgentName']} System prompt 약 {n0(r['ApproxTok'])} tok · 고정 Input 비용 비중 {pc(fx[r['AgentName']]['FixedCostShare'])}" for r in big) + ". 규칙·예시·참조 데이터 구성은 [S] 리뷰가 필요하다", charts=[ch])
    F["B3"] = B3

    def B4():
        g = b_pre()
        if g: return g
        if not c.have_sub: return NO("하위 에이전트 위임이 없다 (멀티 에이전트가 아니다)")
        fs = c.q("B4", "agent_fixed_size")
        return HOLD("공통 블록 비교는 [C] 원문 비교가 필요하다. " + " · ".join(f"{r['AgentName']} S_a {n0(r['STok'] or 0)}, 정의 tool {r['DefinedTools']}개 중 사용 {r['UsedTools']}개" for r in fs))
    F["B4"] = B4

    # ── C ──
    def C1():
        if not c.have_conv: return NODATA("gen_ai.conversation.id 미기록")
        ct = c.q("C1", "cross_turn")
        if not ct: return SKIP("대화 안 턴 순번을 계산할 수 없다")
        thr = c.t("c1_multiple")
        top_ = max(ct, key=lambda r: r["CrossTurnMultiple"] or 0)
        pts = [r for r in c.q("C1", "turn_idx_input") if r["Agent"] == top_["Agent"]]
        ch = {"type": "line", "wide": True, "title": "턴 순번별 첫 호출 Input", "sub": f"쿼리: 턴 순번별 첫 호출 Input (C1) · {top_['Agent']} · InTok p50", "fmt": "tok",
              "xLabel": "대화 안 턴 순번", "xName": "턴", "points": [{"x": str(r["TurnIdx"]), "y": r["P50In"]} for r in pts],
              "threshold": {"value": top_["In1"] * thr, "label": f"1턴의 {thr:g}배"}, "endLabel": True}
        idx = next((i for i, r in enumerate(pts) if r["TurnIdx"] == top_["P90Turn"]), None)
        if idx is not None:
            ch["vmarkers"] = [{"i": idx, "label": f"p90 대화 길이 {top_['P90Turn']}턴"}]
        if (top_["CrossTurnMultiple"] or 0) < thr:
            return R("비해당", basis=f"턴 간 Input 증가 배수 최대 {top_['CrossTurnMultiple']}배 < 기준 {thr:g}배", charts=[ch])
        wb = {r["Agent"]: r for r in c.q("C1", "waste_baseline")}.get(top_["Agent"], {})
        ch["note"] = f"p90 대화 길이({top_['P90Turn']}턴)에서 첫 호출 Input이 1턴의 {top_['CrossTurnMultiple']}배다. 꺾이는 지점 없이 오르면 이력에 상한이 없다."
        return R("해당", f"{top_['Agent']}의 p90 대화 길이({top_['P90Turn']}턴)에서 턴 첫 호출 Input p50이 {n0(top_['InP90'])} tok으로 1턴의 {n0(top_['In1'])} tok보다 {top_['CrossTurnMultiple']}배다. 턴 순번이 쌓일수록 매 호출이 전체 이력을 다시 전송한다.",
                 waste=W(wb.get("WasteUsd", 0), "Σ(턴 순번 t > k) (턴 t 첫 호출 InTok − 턴 k 첫 호출 InTok) × 턴 t의 LLM 호출 수 × P_eff",
                         f"k = {wb.get('KeepTurns')}턴 기준선. 실제 k는 리뷰로 정한다", True),
                 diff=["일정 턴 이후 평탄해지거나 톱니 모양이면 절단·요약이 있는 것이다. 이때는 A3과 함께 본다."], charts=[ch])
    F["C1"] = C1

    def C2():
        gr, sz, cr = c.q("C2", "growth_steps"), c.q("C2", "result_size"), c.q("C2", "result_carry")
        tg = c.q("C2", "turn_growth")
        if not gr: return SKIP("연속 호출 증가분을 계산할 수 없다")
        hot = [r for r in gr if r["P95GrowthRate"] is not None and r["P95GrowthRate"] >= c.t("c2_growth_rate")]
        mult = max([r["P50Multiple"] for r in tg] or [0])
        ch = None
        if sz:
            ch = hbar("tool 결과 크기 p95", "쿼리: Tool별 결과 크기 (C2) · 결과 문자 수 ÷ chars_per_token인 근사",
                      [dict(label=r["ToolName"], value=round(r["P95Chars"] / c.cpt), aside=f"{n0(r['Calls'])}회", **({"tone": "crit"} if i < 2 and r["P95Chars"] / c.cpt >= 1000 else {})) for i, r in enumerate(sz[:8])], "tok")
        if not hot and mult < c.t("c2_multiple"):
            return R("비해당", basis=f"호출당 증가율 p95 최대 {max(r['P95GrowthRate'] or 0 for r in gr)} < {c.ts('c2_growth_rate')}, 턴 내 증가 배수 p50 {mult} < {c.ts('c2_multiple')}", charts=[ch] if ch else None)
        keep = c.t("c2_needed_fraction") or 0
        total = sum(r["CarriedUsd"] for r in cr) * (1 - keep)
        top_ = cr[0] if cr else None
        fnd = " ".join(f"{r['Agent']}에서 연속 호출 사이 증가율 p95가 {r['P95GrowthRate']}이고 한 번 들어온 증가분이 이후 평균 {r['CarryFactor']}번 다시 포함된다." for r in hot) or f"턴 내 Input 증가 배수 p50이 {mult}배다."
        if top_:
            fnd += f" 재전송 비용이 가장 큰 tool은 {top_['ToolName']}(결과 평균 약 {n0(top_['AvgResultTok'])} tok)이다."
        return R("후보", fnd, waste=W(total, "Σ(tool 결과) (결과 토큰 − 필요 토큰) × 이후 남은 호출 수 × P_eff",
                                     f"결과 토큰 × 남은 호출 수 × P_eff, 필요 비율 {keep:g}" + (" (tool 결과 전체를 불필요로 본 상한)" if keep == 0 else ""), keep == 0),
                 diff=["[C] 샘플에서 응답에 실제 쓰인 부분이 일부뿐이면 C2로 확정한다. 필요 비율은 c2_needed_fraction으로 반영한다.",
                       "증가율은 낮은데 배수가 크면 작은 증가가 쌓인 것이므로 호출 수가 많은 D1을 본다."], charts=[ch] if ch else None)
    F["C2"] = C2

    def C3():
        if not c.have_retr: return SKIP("retrieval span이 없다 (검색을 tool로만 쓰면 C2 방식으로 본다)")
        rq = (c.q("C3", "retrieval_quality") or [{}])[0]
        sh = c.q("C3", "score_hist")
        ch = {"type": "columns", "title": "반환 문서 점수 분포", "sub": f"쿼리: 검색 결과 품질 (C3) · 저점수 기준 {c.ts('low_score')}", "fmt": "int", "xName": "점수",
              "bins": [dict(label=f"{r['Bin'] / 10:.1f}", value=r["Docs"], **({"tone": "crit"} if r["Bin"] / 10 < (c.t("low_score") or 0.5) else {})) for r in sh]} if sh else None
        lod = rq.get("LowOrDupShare")
        if lod is None or lod < c.t("c3_low_or_dup"):
            return R("비해당", basis=f"저점수·중복 문서 비율 {pc(lod or 0)} < 기준 {pc(c.t('c3_low_or_dup'))}", charts=[ch] if ch else None)
        names = set((c.ts("search_tools") or "").split(","))
        carried = sum(r["CarriedUsd"] for r in c.q("C2", "result_carry") if r["ToolName"] in names)
        return R("후보", f"검색 {n0(rq['Searches'])}회가 문서 {n0(rq['ReturnedDocs'])}건(top_k 평균 {rq['AvgTopK']})을 돌려주고, 저점수 문서가 {pc(rq['LowScoreShare'])}, 같은 턴의 중복 문서가 {n0(rq['DupInTurn'])}건이다.",
                 waste=W(carried * lod, "(저점수 + 중복 문서 토큰) × 이후 남은 호출 수 × P_eff", f"검색 tool 결과 재전송 비용 {usd(carried)} × 저점수·중복 비율 {pc(lod)}", True),
                 diff=["점수 임계값은 검색 엔진의 점수 척도에 맞춰야 한다 (BM25, 벡터 유사도, semantic reranker는 척도가 다르다)."], charts=[ch] if ch else None)
    F["C3"] = C3

    # ── D ──
    def D1():
        tail = c.q("D1", "tail_turns")
        if not tail: return SKIP("턴 단위 호출 수를 계산할 수 없다")
        nh = c.q("D1", "n_hist")
        ch = {"type": "columns", "wide": True, "title": "턴당 LLM 호출 수 분포", "sub": "쿼리: 턴당 호출 수 분포와 꼬리 비용 (D1) · 15회 이상은 한 구간", "fmt": "int", "xLabel": "턴당 LLM 호출 수", "xName": "호출 수",
              "bins": [{"label": str(r["N"]) if r["N"] < 15 else "15+", "value": r["Turns"]} for r in nh]} if nh else None
        tot = sum(r["TailExcessUsd"] for r in tail)
        sh = max(r["TailCostShare"] or 0 for r in tail)
        dups = c.q("D1", "dup_tool_calls")
        dup_hi = [r for r in dups if r["DupRate"] >= c.t("d1_dup_rate") and r["DupCalls"] > 0]
        if sh < c.t("d1_tail_share"):
            return R("비해당", basis=f"꼬리 턴 비용 비중 {f2(sh)} < 기준 {c.ts('d1_tail_share')}", charts=[ch] if ch else None)
        t0 = max(tail, key=lambda r: r["TailCostShare"] or 0)
        fnd = f"호출 수 상위 5% 턴({n0(t0['TailTurns'])}개)이 {t0['Agent']} 비용의 {pc(t0['TailCostShare'])}를 쓴다. 턴당 호출 수는 p50 {t0['P50N']:g}, p95 {t0['P95N']:g}, 최대 {t0['MaxN']}이다."
        if dup_hi:
            fnd += " 같은 턴에서 같은 인자로 반복된 tool 호출이 있다: " + ", ".join(f"{r['ToolName']} {pc(r['DupRate'])}" for r in dup_hi[:3]) + "."
        elif not c.have_content:
            fnd += " tool 인자 해시(content)가 없어 중복 호출은 확인하지 못했다."
        return R("해당" if dup_hi else "후보", fnd, waste=W(tot, "꼬리 턴 비용 − 꼬리 턴 수 × 턴 비용 중앙값 (간이식)", "상위 5% 턴의 초과 비용 합계", True),
                 diff=["꼬리 턴에 tool 실패가 많으면 F1, LLM 오류가 많으면 F2를 본다.", "최대 반복 횟수와 같은 호출 수의 턴이 있으면 D1로 확정한다. 프레임워크 설정은 [S]로 확인한다."], charts=[ch] if ch else None)
    F["D1"] = D1

    def D2():
        if not c.have_tools: return SKIP("execute_tool span이 없다")
        rt = c.q("D2", "round_tools")
        if not rt: return SKIP("tool 라운드를 계산할 수 없다")
        thr = c.t("d2_serial_share")
        ch = hbar("순차 연쇄 턴 비율", "쿼리: 라운드당 Tool 수 (D2) · tool 라운드 3회 이상이면서 라운드마다 tool이 1개",
                  [{"label": r["Agent"], "value": r["SerialChainShare"], "aside": f"라운드당 tool {r['AvgToolsPerRound']}개"} for r in rt], "pct",
                  {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(r["SerialChainShare"] for r in rt)))
        hot = [r for r in rt if r["SerialChainShare"] >= thr]
        if not hot:
            return R("비해당", basis=f"순차 연쇄 턴 비율 최대 {pc(max(r['SerialChainShare'] for r in rt), 1)} < 기준 {pc(thr)}", charts=[ch])
        return R("후보", " ".join(f"{r['Agent']}의 턴 {pc(r['SerialChainShare'], 1)}가 tool을 한 번에 하나씩 3회 이상 부른다." for r in hot),
                 waste=W(sum(r["ChainWasteUsdUpper"] for r in hot), "연쇄 길이 k인 턴마다 (k − 1) × 해당 라운드들의 평균 InTok × P_eff", "tool 호출 라운드 비용의 (k−1)/k (상한)", True),
                 diff=["[C]로 연속 tool이 서로의 결과를 인자로 쓰는지 확인한다. 의존 관계가 있으면 정상이다."], charts=[ch])
    F["D2"] = D2

    def D3():
        if not c.have_content: return SKIP("output 해시(content)가 없어 출력 다양성을 계산할 수 없다")
        so = {(r["Agent"]): r for r in c.q("D3", "output_diversity")}
        sh = {}
        for r in c.q("D3", "short_output"):
            s = sh.setdefault(r["Agent"], {"cost": 0.0, "share": 0.0})
            s["cost"] += r["ShortCost"]
            s["share"] = max(s["share"], r["ShortCostShare"] or 0)
        rows = [dict(label=ag, value=sh[ag]["share"], aside=f"다양성 {so[ag]['OutputDiversity'] if ag in so else '—'}") for ag in sh]
        ch = hbar("에이전트별 짧은 출력 호출의 비용 비중", "쿼리: 짧은 출력 호출 비중 (D3) · 가시 출력 50 tok 미만, tool 호출 제외", rows, "pct", None, nice_max(max([r["value"] for r in rows] or [0])))
        hit = [ag for ag in so if so[ag]["OutputDiversity"] <= c.t("d3_diversity") and sh.get(ag, {}).get("share", 0) >= c.t("d3_short_cost")]
        if not hit:
            return R("비해당", basis="짧은 출력 호출의 비용 비중이 작거나 출력이 다양하다", charts=[ch])
        total = sum(so[ag]["ShortCost"] for ag in hit)
        return R("해당", " ".join(f"{ag}의 짧은 출력 호출 {n0(so[ag]['ShortCalls'])}건이 비용의 {pc(sh[ag]['share'])}를 쓰고, 출력의 종류는 {n0(so[ag]['DistinctOutputs'])}가지다(다양성 {so[ag]['OutputDiversity']})." for ag in hit),
                 waste=W(total, "해당 호출의 비용 전체 (규칙으로 대체할 때의 상한)", "짧은 출력 호출 비용 합계", True),
                 diff=["그 호출이 상위 모델이면 E1과 겹친다. 규칙으로 처리할 수 있는지는 [S]로 확인한다."], charts=[ch])
    F["D3"] = D3

    def D4():
        if not c.have_sub: return NO("하위 에이전트 위임이 없다")
        dl = c.q("D4", "delegation")
        if not dl: return SKIP("위임 직전 상위 호출을 짝지을 수 없다")
        thr = c.t("d4_pass_ratio")
        val = lambda r: r["P50PassRatioVar"] if r.get("P50PassRatioVar") is not None else r["P50PassRatio"]
        ch = hbar("하위 에이전트별 위임 시 Input 전달 비율", "쿼리: 위임 시 Input 전달량 (D4) · (하위 첫 Input − S_sub) ÷ (상위 직전 Input − S_parent)",
                  [{"label": r["SubAgent"], "value": val(r), "aside": f"위임 {n0(r['Delegations'])}회"} for r in dl], "ratio", {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(val(r) for r in dl)))
        hot = [r for r in dl if val(r) >= thr and r["Delegations"] >= c.min_n]
        if not hot:
            return R("비해당", basis=f"전달 비율 최대 {f2(max(val(r) for r in dl))} < 기준 {thr:g}", charts=[ch])
        keep = c.t("d4_needed_fraction") or 0
        total = sum(r["PassedUsd"] for r in hot) * (1 - keep)
        return R("후보", " ".join(f"{r['SubAgent']}는 위임마다 상위 가변 컨텍스트의 {pc(val(r))}를 그대로 받는다(첫 Input p50 {n0(r['P50SubFirstIn'])} tok)." for r in hot),
                 waste=W(total, "Σ(위임) (전달 토큰 − 필요 토큰) × 하위 에이전트 호출 수 × P_eff", f"전달 토큰 × 하위 호출 수 × P_eff, 필요 비율 {keep:g}" + (" (전달분 전체를 불필요로 본 상한)" if keep == 0 else ""), keep == 0),
                 diff=["[C]로 전달된 메시지가 하위 에이전트 역할에 필요한 부분인지 확인한다. 하위 응답 반입이 크면 상위에서 C2와 같은 재전송 문제가 생긴다."], charts=[ch])
    F["D4"] = D4

    # ── E ──
    def E1():
        sv = (c.q("E1", "savings") or [{}])[0]
        if not sv.get("SmallModelFound"):
            return HOLD(f"e1_small_model({c.ts('e1_small_model')})이 model-params.csv에 없어 소형 모델 단가로 다시 계산할 수 없다")
        bt = {}
        for r in c.q("E1", "savings_by_type"):
            bt[r["CallType"]] = bt.get(r["CallType"], 0) + r["SavingUsd"]
        ch = hbar("호출 유형별 잠재 절감", f"쿼리: 호출 유형별·모델별 비용 (E1) · {sv['SmallModel']} 단가로 다시 계산", [{"label": k, "value": round(v, 2)} for k, v in sorted(bt.items())], "usd", wide=True, label_width="16rem")
        if sv["SavingShare"] < c.t("e1_saving_share"):
            return R("비해당", basis=f"잠재 절감 비중 {pc(sv['SavingShare'], 1)} < 기준 {pc(c.t('e1_saving_share'))}", charts=[ch])
        return R("후보", f"유형 1–3(계획·tool 처리·짧은 출력) 호출 {n0(sv['CandidateCalls'])}건을 {sv['SmallModel']}로 처리하면 전체 비용의 {pc(sv['SavingShare'])}를 줄일 수 있다.",
                 waste=W(sv["SavingUsd"], "Σ(유형 1–3 상위 모델 호출) [Cost − 소형 모델 단가로 다시 계산한 Cost]", f"{usd(sv['CandidateCost'])} − 소형 모델 재계산 {usd(sv['CandidateCost'] - sv['SavingUsd'])}", True),
                 diff=["품질 영향은 진단 범위 밖이다. 소형 모델로 품질이 유지되는지는 별도 평가 대상이다."], charts=[ch])
    F["E1"] = E1

    def E2():
        if not c.have_reason: return NODATA("gen_ai.usage.reasoning.output_tokens 미수집")
        rv = reasoning_view(c)
        if not rv.get("agent"): return SKIP("유형 1–3과 유형 4를 비교할 호출이 없다")
        rows = [r for r in c.q("E2", "reasoning_by_type") if r["Agent"] == rv["agent"]]
        ch = {"type": "box", "wide": True, "title": "호출 유형별 ReasonTok 분포", "sub": f"gen_ai.usage.reasoning.output_tokens · {rv['agent']} · reasoning 수준 {'/'.join(sorted(rv['ls'] | rv['lf']))}", "fmt": "tok",
              "rows": [{"label": r["CallType"], "v": [r["Min"], r["Q1"], r["Median"], r["Q3"], r["Max"]]} for r in rows],
              "note": "상자는 Q1–Q3, 상자 안 틈은 중앙값, 수염은 최소–최대다."}
        if not rv["suspect"]:
            return R("비해당", basis=f"단순 단계 reasoning p50 {n0(rv['med_s'])}이 최종 응답 단계 {n0(rv['med_f'])}보다 충분히 낮다", charts=[ch])
        wa = c.q("E2", "waste")
        w = sum(r["WasteUsd"] for r in wa)
        return R("후보", f"{rv['agent']}의 단순 단계가 최종 응답 단계와 같은 reasoning 수준({'/'.join(sorted(rv['ls']))})으로 실행되고 ReasonTok p50이 {n0(rv['med_s'])}으로 최종 응답 단계({n0(rv['med_f'])})에 가깝다.",
                 waste=W(w, "Σ(후보 호출) (ReasonTok − 같은 유형 저수준 호출의 ReasonTok p50) × P_out", "저수준 호출 기준선 대비 초과 reasoning") if w > 0 else None,
                 basis=None if w > 0 else "같은 모델·유형의 저수준 호출이 없어 낭비 기준선이 없다. 소수 샘플을 낮은 수준으로 다시 실행해 비교한다", charts=[ch])
    F["E2"] = E2

    def E3():
        fr = c.q("E3", "final_response")
        if not fr: return SKIP("최종 응답 호출을 찾을 수 없다")
        p95 = max(r["P95Visible"] for r in fr)
        ch = hbar("에이전트별 최종 응답 길이", "쿼리: 최종 응답 길이 (E3) · 가시 출력 p50 / p95", [{"label": r["Agent"], "value": r["P95Visible"], "aside": f"p50 {n0(r['P50Visible'])} · max_tokens 미설정 {pc(r['NoMaxTokens'])}"} for r in fr], "tok")
        tgt = c.t("e3_target_tok")
        if tgt is None:
            return R("판정 보류", basis=f"고객이 정한 목표 길이가 없다 · 최종 응답 p95 {n0(p95)} tok. 목표 길이를 받으면 e3_target_tok에 넣는다", charts=[ch])
        wt = c.q("E3", "waste_vs_target")
        w = sum(r["WasteUsd"] for r in wt)
        if p95 <= tgt:
            return R("비해당", basis=f"최종 응답 p95 {n0(p95)} tok ≤ 목표 {n0(tgt)} tok", charts=[ch])
        return R("후보", f"최종 응답 p95가 {n0(p95)} tok으로 목표 {n0(tgt)} tok을 넘는다. [C] 샘플에서 반복이나 불필요한 구간이 확인되면 E3로 확정한다.",
                 waste=W(w, "Σ(최종 응답) (가시 출력 − 목표 길이) × P_out", f"목표 {n0(tgt)} tok 초과분 × P_out. 이력이 유지되면 이후 턴의 재전송분이 더해진다", True), charts=[ch])
    F["E3"] = E3

    def E4():
        tr = c.q("E4", "truncation")
        if not tr: return SKIP("호출이 없다")
        thr = c.t("e4_trunc_rate")
        ch = hbar("에이전트·모델별 잘림 비율", "쿼리: 출력 잘림 (E4) · finish_reasons length 또는 status incomplete", [{"label": f"{r['Agent']} · {r['Model']}", "value": r["TruncRate"], "aside": f"잘림 {n0(r['TruncCalls'])}건"} for r in tr], "pct",
                  {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(r["TruncRate"] for r in tr)))
        hot = [r for r in tr if r["TruncRate"] >= thr and r["FollowUpAfterTrunc"] > 0 and r["Calls"] >= c.min_n]
        if not hot:
            return R("비해당", basis=f"잘림 비율 최대 {pc(max(r['TruncRate'] for r in tr), 2)} < 기준 {pc(thr)} 이거나 후속 호출이 없다", charts=[ch])
        rs = c.t("e4_reason_share")
        return R("해당", " ".join(f"{r['Agent']}({r['Model']})의 호출 {pc(r['TruncRate'], 1)}가 출력 상한에 걸려 끊기고, 그 뒤 후속 호출 {n0(r['FollowUpAfterTrunc'])}건이 이어졌다." + (f" 잘린 호출의 reasoning 비중이 {pc(r['TruncReasonShare'])}라 reasoning이 상한을 다 쓴 유형이다." if (r['TruncReasonShare'] or 0) >= rs else "") for r in hot),
                 waste=W(sum(r["TruncCost"] + r["FollowUpCost"] for r in hot), "Σ 잘린 호출 Cost + 잘림 때문에 생긴 후속 호출 Cost", "잘린 호출 비용 + 후속 호출 비용"), charts=[ch])
    F["E4"] = E4

    # ── F ──
    def F1():
        if not c.have_tools: return SKIP("execute_tool span이 없다")
        tf = c.q("F1", "tool_failures")
        thr = c.t("f1_fail_rate")
        ch = hbar("tool별 실패율", "쿼리: Tool별 실패율 (F1)", [{"label": r["ToolName"], "value": r["FailRate"], "aside": f"{n0(r['Calls'])}회" + (f" · {r['SampleErr']}" if r["SampleErr"] else "")} for r in tf], "pct",
                  {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(r["FailRate"] for r in tf)))
        hot = [r for r in tf if r["FailRate"] >= thr and r["Calls"] >= c.min_n]
        fv = c.q("F1", "fail_vs_ok_turns")
        costlier = [a for a in {r["Agent"] for r in fv} if (lambda f, o: f and o and f["P50TurnCost"] > o["P50TurnCost"])(
            next((r for r in fv if r["Agent"] == a and r["HasFail"]), None), next((r for r in fv if r["Agent"] == a and not r["HasFail"]), None))]
        if not hot:
            return R("비해당", basis=f"tool 실패율 최대 {pc(max(r['FailRate'] for r in tf), 1)} < 기준 {pc(thr)}", charts=[ch])
        if not costlier:
            return R("후보", basis=f"{hot[0]['ToolName']} 실패율 {pc(hot[0]['FailRate'])}이지만 실패 턴이 정상 턴보다 비싸지 않다", charts=[ch])
        w = sum(r["WasteUsd"] for r in c.q("F1", "waste"))
        return R("해당", " ".join(f"{r['ToolName']}의 실패율이 {pc(r['FailRate'])}({r['SampleErr'] or '오류 유형 미기록'})이고" for r in hot[:2]) + " tool 실패가 있는 턴은 정상 턴보다 비용 중앙값이 크다.",
                 waste=W(w, "Σ(실패 턴) (턴 비용 − 정상 턴 비용 중앙값)", "tool 실패가 있는 턴의 초과 비용 합계"),
                 diff=["error.type으로 인자 오류와 외부 장애를 나눈다. 인자 오류가 많으면 tool 설명이 불명확한 경우가 많으니 B2 리뷰와 함께 본다."], charts=[ch])
    F["F1"] = F1

    def F2():
        lf = c.q("F2", "llm_failures")
        if not lf: return SKIP("호출이 없다")
        thr = c.t("f2_fail_rate")
        ch = hbar("에이전트별 실패·재요청 추정 비율", "쿼리: LLM 호출 실패와 재요청 (F2)", [{"label": r["Agent"], "value": r["FailOrResendRate"], "aside": f"실패 {n0(r['FailedCalls'])} · 재요청 추정 {n0(r['Resends'])}"} for r in lf], "pct",
                  {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(r["FailOrResendRate"] for r in lf)))
        hot = [r for r in lf if r["FailOrResendRate"] >= thr and r["Calls"] >= c.min_n]
        if not hot:
            return R("비해당", basis=f"실패·재요청 추정 비율 최대 {pc(max(r['FailOrResendRate'] for r in lf), 2)} < 기준 {pc(thr)}", charts=[ch])
        return R("후보", " ".join(f"{r['Agent']}의 호출 {pc(r['FailOrResendRate'], 1)}가 실패했거나 직전과 거의 같은 Input의 재요청으로 보인다." for r in hot),
                 waste=W(sum(r["FailedCost"] + r["ResendCost"] for r in hot), "토큰이 기록된 실패 호출 Cost + 재요청 호출 Cost", "실패 호출 비용 + 재요청 추정 호출 비용"),
                 diff=["재요청은 Input 차이 기반의 추정이다. 출력 파싱 실패 뒤에 몰리면 [S]로 structured output 사용 여부를 확인한다.", "토큰이 기록되지 않은 실패(예: 429)는 비용보다 지연 문제다."], charts=[ch])
    F["F2"] = F2

    def F3():
        if not c.have_conv: return NODATA("gen_ai.conversation.id 미기록")
        qr = c.q("F3", "quick_reinput")
        if not qr: return SKIP("대화 안 턴 간격을 계산할 수 없다")
        thr = c.t("f3_quick_rate")
        ch = hbar("에이전트별 빠른 재입력 비율", f"쿼리: 빠른 재입력 비율 (F3) · 직전 턴 종료 후 {c.ts('quick_retry_sec')}초 안", [{"label": r["Agent"], "value": r["QuickRetryRate"], "aside": f"{n0(r['QuickTurns'])}턴"} for r in qr], "pct",
                  {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max(r["QuickRetryRate"] for r in qr)))
        hot = [r for r in qr if r["QuickRetryRate"] >= thr and r["Turns"] >= c.min_n]
        if not hot:
            return R("비해당", basis=f"빠른 재입력 비율 최대 {pc(max(r['QuickRetryRate'] for r in qr), 1)} < 기준 {pc(thr)}", charts=[ch])
        w = sum(r["WasteUsd"] for r in c.q("F3", "waste"))
        return R("후보", " ".join(f"{r['Agent']}의 턴 {pc(r['QuickRetryRate'], 1)}가 직전 턴 종료 후 {c.ts('quick_retry_sec')}초 안에 시작된다." for r in hot),
                 waste=W(w, "Σ(재질문 직전 턴) 턴 비용", "빠른 재입력 직전 턴의 비용 합계 ([T] 근사)", True),
                 diff=["빠른 재입력이 꼭 재질문은 아니다. `quick_pairs`의 SpanId로 content_by_span을 받아 연속 사용자 메시지의 임베딩 유사도(≥ 0.85)를 계산해 확정한다."], charts=[ch])
    F["F3"] = F3

    def F4():
        ft = c.q("F4", "failed_turns")
        if not ft: return SKIP("턴이 없다")
        thr = c.t("f4_late_fail")
        ch = hbar("에이전트별 늦은 실패 계수", "쿼리: 실패 턴 비용 (F4) · 실패 턴 비용 비중 ÷ 실패 턴 비율", [{"label": r["Agent"], "value": r["LateFailIndex"] or 0, "aside": f"실패 턴 {pc(r['FailTurnRate'], 1)}"} for r in ft], "x",
                  {"value": thr, "dir": "above", "label": f"기준 {thr:g}"}, nice_max(max([r["LateFailIndex"] or 0 for r in ft] + [thr])))
        hot = [r for r in ft if (r["LateFailIndex"] or 0) >= thr]
        if not hot:
            return R("비해당", basis=f"늦은 실패 계수 최대 {f2(max((r['LateFailIndex'] or 0) for r in ft))} < 기준 {thr:g}. 실패 턴 비용 {usd(sum(r['FailCost'] for r in ft))}", charts=[ch])
        return R("해당", " ".join(f"{r['Agent']}의 실패 턴은 비율 {pc(r['FailTurnRate'], 1)}인데 비용 비중이 {pc(r['FailCostShare'], 1)}라 많이 쓰고 나서 실패한다(평균 호출 {r['AvgCallsFail']}회 vs {r['AvgCallsOk']}회)." for r in hot),
                 waste=W(sum(r["FailCost"] for r in hot), "Σ 실패 턴 비용", "루트 invoke_agent가 오류로 끝난 턴의 비용 합계"),
                 diff=["앱이 예외를 잡아 정상 응답으로 감싸면 Success가 true로 남는다. [S]로 오류 처리 방식을 확인한다."], charts=[ch])
    F["F4"] = F4
    return F


# ───────────────────────── 조립 ─────────────────────────
def assemble(c, meta_in):
    syms = build_symptoms(c)
    funcs = build_causes(c)
    causes = []
    for t in c.tmpl["causes"]:
        try:
            res = funcs[t["id"]]()
        except Exception as e:                          # noqa: BLE001
            res = SKIP(f"지표를 해석하지 못했다: {type(e).__name__}: {e}")
            c.errors.append(f"{t['id']}: {type(e).__name__}: {e}")
        d = {"id": t["id"], "name": t["name"], "verdict": res["verdict"], "from": t["from"], "rule": t["rule"]}
        for k in ("finding", "basis", "waste", "differential", "charts"):
            if res.get(k):
                d[k] = res[k]
        # 리뷰 반영
        rv = (c.review.get("causes") or {}).get(t["id"])
        if rv:
            for k in ("verdict", "finding", "basis", "differential", "rule"):
                if k in rv:
                    d[k] = rv[k]
            if "waste" in rv:
                d["waste"] = rv["waste"] if rv["waste"] else d.pop("waste", None)
            if rv.get("charts_add"):
                d["charts"] = (d.get("charts") or []) + rv["charts_add"]
            if rv.get("charts"):
                d["charts"] = rv["charts"]
            d["reviewed"] = True
        if d["verdict"] in ("해당", "후보") and not d.get("waste") and not d.get("basis"):
            d["basis"] = d.get("finding") or "낭비 추정 없음"
        if d["verdict"] not in ("해당", "후보") and not d.get("basis"):
            d["basis"] = d.get("finding") or d["verdict"]
        d.pop("reviewed", None)
        causes.append(d)
    meta = make_meta(c, causes, meta_in)
    return {"meta": meta, "categories": c.tmpl["categories"], "symptoms": syms, "causes": causes}


def make_meta(c, causes, meta_in):
    ov = c.over
    fs = c.q("B-common", "fixed_share")
    main = max(fs, key=lambda r: r["Cost"]) if fs else {}
    ac = c.q("A-common", "overall")
    per_from, per_to = ov.get("PeriodFrom"), ov.get("PeriodTo")
    days = 0
    try:
        import datetime as dt
        days = (dt.date.fromisoformat(per_to) - dt.date.fromisoformat(per_from)).days + 1
    except Exception:                                   # noqa: BLE001
        pass
    agents = sorted({r["Agent"] for r in c.q("A-common", "agent_cache")})
    cards = sorted([x for x in causes if x["verdict"] in ("해당", "후보") and x.get("waste")], key=lambda x: -x["waste"]["usd"])
    total = ov.get("TotalCost") or 0
    summ = []
    s1 = f"분석 기간 {days}일의 LLM 비용은 {usd(total)}이다."
    if cards:
        s1 += " 낭비 추정이 가장 큰 원인은 " + ", ".join(f"{x['name']}({x['id']}, {usd(x['waste']['usd'])})" for x in cards[:3]) + "이다."
    summ.append(s1)
    for x in cards[:3]:
        first = x.get("finding", "").split(". ")[0].rstrip(".")
        if first:
            summ.append(first + ("." if not first.endswith(".") else "") + (" (상한 추정)" if x["waste"].get("bound") else ""))
    nod = [x["id"] for x in causes if x["verdict"] == "계측 부재"]
    skp = [x["id"] for x in causes if x["verdict"] == "미점검"]
    hold = [x["id"] for x in causes if x["verdict"] == "판정 보류"]
    notes = []
    if nod: notes.append(f"{', '.join(nod)}는 계측이 없어 진단하지 못했다")
    if skp: notes.append(f"{', '.join(skp)}는 필요한 데이터나 입력이 없어 점검하지 못했다")
    if hold: notes.append(f"{', '.join(hold)}는 판정을 보류했다")
    if notes: summ.append(". ".join(notes) + ".")
    summ.append("낭비 추정은 원인끼리 같은 토큰을 겹쳐 셀 수 있어 합산하지 않는다.")
    prices = [{"model": m, "pin": float(p["PIn"]), "pcache": float(p["PCache"]), "pout": float(p["POut"])} for m, p in c.params.items()]
    sa = {r["Agent"]: r.get("STok") for r in []}
    sa_main = 0
    for base in (pathlib.Path(c.root_dir),):
        f = base / "config" / "agents.csv"
        if f.exists():
            rows = {r["Agent"]: r for r in csv.DictReader(f.open(encoding="utf-8"))}
            sa_main = int(float(rows.get(main.get("Agent"), {}).get("STok", 0) or 0))
    meta = {
        "sample": False, "title": "토큰 비용 진단 보고서", "customer": "(고객명)", "service": "(서비스)",
        "period": {"from": per_from or "", "to": per_to or "", "days": days}, "agents": agents or ["(없음)"],
        "project": "(Foundry 프로젝트)", "appInsights": "(Application Insights)", "models": ", ".join(c.params) or "(모델)",
        "sources": "AppDependencies, AppRequests" + (", AppGenAIContent(요약 해시)" if c.have_content else ""),
        "author": "", "date": "", "totalCost": total, "turns": ov.get("Turns") or 0, "calls": ov.get("Calls") or 0,
        "cacheRate": ov.get("CacheRate") or 0, "fixedInput": sa_main, "peff": main.get("PEff") or 0,
        "prices": prices, "summary": summ,
    }
    meta.update({k: v for k, v in (meta_in or {}).items() if k != "summary" or v})
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", default="out/metrics.json")
    ap.add_argument("--root", default=".", help="config/가 있는 디렉터리")
    ap.add_argument("--meta", help="머리말 JSON (customer, service, project, appInsights, models, sources, author, date, title ...)")
    ap.add_argument("--review", help="리뷰 반영 JSON (docs/build-mapping.md)")
    ap.add_argument("--template", default=str(REPO / "report" / "token-diagnosis-report.html"))
    ap.add_argument("--out", default="out/report.html")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve()
    metrics = json.loads(pathlib.Path(a.metrics).read_text(encoding="utf-8"))
    html = pathlib.Path(a.template).read_text(encoding="utf-8")
    tmpl = extract_report(html)
    review = json.loads(pathlib.Path(a.review).read_text(encoding="utf-8")) if a.review else None
    meta_in = json.loads(pathlib.Path(a.meta).read_text(encoding="utf-8")) if a.meta else {}
    if review and review.get("meta"):
        meta_in = {**meta_in, **review["meta"]}
    c = Ctx(metrics, root, tmpl, review)
    c.root_dir = root
    rep = assemble(c, meta_in)
    if review and review.get("summary"):
        rep["meta"]["summary"] = review["summary"]

    v = validate(rep)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out.write_text(inject_report(html, rep), encoding="utf-8")

    cnt = {}
    for x in rep["causes"]:
        cnt[x["verdict"]] = cnt.get(x["verdict"], 0) + 1
    print("판정:", ", ".join(f"{k} {n}" for k, n in sorted(cnt.items(), key=lambda kv: -kv[1])))
    cards = sorted([x for x in rep["causes"] if x["verdict"] in ("해당", "후보") and x.get("waste")], key=lambda x: -x["waste"]["usd"])
    for x in cards:
        print(f"  {x['id']:3} {x['verdict']:3} {usd(x['waste']['usd']):>9}{' 상한' if x['waste'].get('bound') else '    '}  {x['name']}")
    for e in c.errors:
        print(f"[metric-error] {e}", file=sys.stderr)
    for w in v.warnings:
        print(f"[warn]  {w}")
    for e in v.errors:
        print(f"[error] {e}")
    print(f"→ {out} · {out.with_suffix('.json')}")
    return 1 if v.errors else 0


if __name__ == "__main__":
    sys.exit(main())
