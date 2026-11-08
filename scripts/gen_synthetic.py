#!/usr/bin/env python3
"""합성 텔레메트리 CSV를 만든다. 문제를 알려진 위치에 심어 두어 지표와 판정을 검증하는 데 쓴다.

심어 둔 문제 (PLANTED 참고):
  A1 support-router의 템플릿 변형(40% 턴)에서 System prompt 첫 줄에 현재 시각이 들어가 prefix가 바뀐다
  A4 refund-agent의 고정 Input(S_a)이 1,024 미만이다
  A5 트래픽이 적어 요청 간격이 보존 시간(5분)을 넘는 경우가 많다
  A6 하루 한 번 분당 15건을 넘는 몰림이 있다
  B1 support-router는 escalate_to_human tool을 정의하지만 쓰지 않는다
  C1 대화 이력에 상한이 없어 턴이 쌓일수록 첫 호출 Input이 늘어난다
  C2 lookup_order가 결과 원문(약 12,000 토큰)을 그대로 넘긴다
  C3 search_faq가 top_k 10 중 저점수·중복 문서를 많이 돌려준다
  D1 3%의 턴이 같은 tool을 같은 인자로 반복하는 루프에 빠진다
  D3 의도 분류 호출(출력 6 토큰)을 상위 모델로 처리한다
  D4 refund-agent에게 상위 컨텍스트를 거의 통째로 넘긴다
  E1 분류·계획 호출에 gpt-5.4를 쓴다
  E4 일부 최종 응답이 max_tokens에 걸려 잘린다
  F1 search_faq가 30% 실패하고 재시도한다
  F3 8%의 턴이 직전 턴 종료 30초 안에 다시 들어온다

사용:  python3 scripts/gen_synthetic.py --out /tmp/synth --days 3 --seed 7
"""
import json, argparse, csv, datetime as dt, hashlib, pathlib, random, re
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
QDIR = ROOT / "queries" / "export"

S_TOK = {"support-router": 9800, "order-agent": 4200, "refund-agent": 900}
SYS_CHARS = {"support-router": 24000, "order-agent": 9000, "refund-agent": 2200}
TOOLS = {  # (에이전트) -> [(tool, 정의 chars, description chars)]
    "support-router": [("lookup_order", 1800, 900), ("search_faq", 6200, 5100),
                       ("get_refund_policy", 900, 300), ("create_ticket", 700, 200),
                       ("escalate_to_human", 4100, 3600), ("delegate_order", 300, 100),
                       ("delegate_refund", 300, 100)],
    "order-agent": [("lookup_order", 1800, 900), ("cancel_order", 700, 200)],
    "refund-agent": [("get_refund_policy", 900, 300)],
}
MODEL_BY_AGENT = {"support-router": "gpt-5.4", "order-agent": "gpt-5.4", "refund-agent": "gpt-5-mini"}
WINDOW_S = 300
RESULT_TOK = 12000


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def cacheable(x, mn=1024):
    return 0 if x < mn else (x // 128) * 128


class Gen:
    def __init__(self, a):
        self.r = random.Random(a.seed)
        self.a = a
        self.n = 0
        self.rows = defaultdict(list)       # name -> [dict]
        self.last_seen = {}                 # agent -> t
        self.rpm = Counter()                # (agent, minute) -> n
        self.conv = {}                      # conv -> dict(hist, last_end, turns)
        self.last_tool_ms = 0

    def sid(self, p):
        self.n += 1
        return f"{p}{self.n:07d}"

    def iso(self, t):
        return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def add(self, name, **kw):
        self.rows[name].append(kw)

    # --- 호출 하나 ---------------------------------------------------------
    def chat(self, t, op, parent, agent, conv, in_tok, out_tok, reason, finish, first, prev_in, hist_cached=0,
             model=None, max_tok=None, sysvar=False, status=""):
        r = self.r
        model = model or MODEL_BY_AGENT[agent]
        S = S_TOK[agent]
        unstable = sysvar and agent == "support-router"
        if unstable:
            hit = 0                                       # A1: prefix가 매번 바뀐다
        elif first:
            warm = agent in self.last_seen and (t - self.last_seen[agent]).total_seconds() < WINDOW_S
            hit = cacheable(S + hist_cached) if warm else 0
            m = (agent, t.replace(second=0, microsecond=0))
            if self.rpm[m] > 15 and r.random() < 0.5:    # A6: 쏠림
                hit = 0
        else:
            hit = cacheable(prev_in)
        mm = (agent, t.replace(second=0, microsecond=0))
        if not unstable and not first and self.rpm[mm] > 15 and r.random() < 0.8:
            hit = 0                                       # A6: 몰린 분에는 턴 안 호출도 다른 머신으로 간다
        hit = min(hit, in_tok)
        self.rpm[(agent, t.replace(second=0, microsecond=0))] += 1
        if not unstable:
            self.last_seen[agent] = t                    # 안정된 prefix만 캐시에 남는다
        sp = self.sid("c")
        self.add("calls", TimeGenerated=self.iso(t), OperationId=op, SpanId=sp, ParentId=parent, Op="chat",
                 Agent="", ConvId=conv, Model=model, RequestModel="dep-" + model, InTok=in_tok, CacheTok=hit,
                 WriteTok=0, OutTok=out_tok, ReasonTok=reason, Finish=json.dumps([finish]), Status=status,
                 MaxTok=max_tok if max_tok else "", ReasonLevel="high" if model == "gpt-5.4" else "low",
                 Compacted="", Endpoint="ep1" if r.random() < 0.9 or agent != "order-agent" else "ep2",
                 ErrorType="", Success="true", ResultCode=200, DurationMs=int(300 + out_tok * 4))
        # content digest (chat span)
        stamp = self.iso(t) if unstable else ""
        names = [x[0] for x in TOOLS[agent]]
        head = h("head-" + agent + stamp)
        self.add("content_digest", TimeGenerated=self.iso(t), TraceId=op, SpanId=sp, AgentName=agent,
                 SysChars=SYS_CHARS[agent], SysHash=h("sys-" + agent + stamp), SysHeadHash=head,
                 ToolDefsChars=sum(x[1] for x in TOOLS[agent]), ToolDefsHash=h("td-" + agent),
                 ToolCount=len(names), ToolNames="|".join(names), InChars=in_tok * 4, InHash=h(sp),
                 OutChars=out_tok * 4, OutHash=h("out-" + (finish if out_tok < 50 else sp)),
                 ArgsChars="", ArgsHash="", ResultChars="", ResultHash="")
        return sp, hit

    def tool(self, t, op, parent, name, ok, args, result_tok):
        sp = self.sid("tl")
        self.last_tool_ms = self.r.randint(80, 900)
        self.add("tools", TimeGenerated=self.iso(t), OperationId=op, SpanId=sp, ParentId=parent, ToolName=name,
                 ToolCallId="call-" + sp, Success="true" if ok else "false", ResultCode=200 if ok else 500,
                 ErrorType="" if ok else "ArgumentError", DurationMs=self.last_tool_ms)
        self.add("content_digest", TimeGenerated=self.iso(t), TraceId=op, SpanId=sp, AgentName="support-router",
                 SysChars="", SysHash="", SysHeadHash="", ToolDefsChars="", ToolDefsHash="", ToolCount=0,
                 ToolNames="", InChars="", InHash="", OutChars="", OutHash="", ArgsChars=len(args),
                 ArgsHash=h(name + args), ResultChars=result_tok * 4 if ok else 80,
                 ResultHash=h("res" + sp))
        if name == "search_faq" and ok:
            rs = self.sid("rt")
            seen = []
            for i in range(10):
                if seen and self.r.random() < 0.25:
                    d = self.r.choice(seen)                     # C3: 같은 턴의 중복 문서
                else:
                    d = f"doc-{self.r.randint(1, 400)}"
                seen.append(d)
                score = self.r.random() * (0.5 if i >= 4 else 1.0)  # C3: 하위 문서는 저점수
                self.add("retrieval", TimeGenerated=self.iso(t), OperationId=op, SpanId=rs, ParentId=sp,
                         TopK=10, DocCount=10, DocId=d, Score=round(score, 3))
        return sp

    # --- 턴 하나 -----------------------------------------------------------
    def turn(self, t0, conv, route, sysvar):
        r = self.r
        op = self.sid("op")
        c = self.conv[conv]
        user = r.randint(40, 200)
        hist = c["hist"]
        root = self.sid("ag")
        t = t0
        total_ms = 0
        calls_in_turn = 0
        prev_in = prev_out = 0
        ctx = hist + user
        S = S_TOK["support-router"]
        loop = r.random() < 0.03
        failed_turn = r.random() < 0.03

        def step(agent, parent, finish, out, reason=0, first=False, hist_cached=0, max_tok=None, model=None,
                 status="", base=None):
            nonlocal t, prev_in, prev_out, calls_in_turn
            in_tok = (S_TOK[agent] + ctx) if base is None else base
            out_total = out if status == "incomplete" else out + reason   # OutTok은 reasoning을 포함한다
            sp, hit = self.chat(t, op, parent, agent, conv, in_tok, out_total, reason, finish, first, prev_in,
                                hist_cached, model, max_tok, sysvar, status)
            prev_in, prev_out = in_tok, out_total
            calls_in_turn += 1
            t += dt.timedelta(milliseconds=int(300 + out_total * 4) + 200)
            return sp, in_tok

        # 1) 의도 분류 (D3, E1)
        sp, _ = step("support-router", root, "stop", 6, 0, first=True, hist_cached=hist // 2)
        ctx += 120
        # 2) 계획 + tool
        sp, in_tok = step("support-router", root, "tool_calls", 90, 380)
        used = []
        if route == "order":
            tool_name = "lookup_order"
            sub = r.random() < 0.4
        elif route == "refund":
            tool_name = "get_refund_policy"
            sub = True
        else:
            tool_name = "search_faq"
            sub = False
        repeats = 12 if loop else 1
        res_tok = RESULT_TOK if tool_name == "lookup_order" else 3500 if tool_name == "search_faq" else 600
        for k in range(repeats):
            ok = True
            if tool_name == "search_faq" and r.random() < 0.30:
                ok = False
            args = '{"q": "same"}' if loop else f'{{"q": "{r.random()}"}}'
            self.tool(t, op, root, tool_name, ok, args, res_tok)
            t += dt.timedelta(milliseconds=self.last_tool_ms + 20)
            used.append(tool_name)
            if not ok:
                # F1: 실패 후 재시도
                ctx += 150
                sp, in_tok = step("support-router", root, "tool_calls", 90, 200)
                self.tool(t, op, root, tool_name, True, args, res_tok)
                t += dt.timedelta(milliseconds=self.last_tool_ms + 20)
            ctx += res_tok if ok or True else 0
            if k < repeats - 1:
                sp, in_tok = step("support-router", root, "tool_calls", 70, 150)
        # 3) 하위 에이전트 위임 (D4)
        if sub:
            sub_agent = "refund-agent" if route == "refund" else "order-agent"
            dsp = self.tool(t, op, root, "delegate_refund" if sub_agent == "refund-agent" else "delegate_order",
                            True, "{}", 50)
            t += dt.timedelta(milliseconds=20)
            ssp = self.sid("ag")
            # 에이전트를 tool로 부르는 구조: 하위 invoke_agent의 부모는 위임 tool span이다
            self.add("agent_spans", TimeGenerated=self.iso(t), OperationId=op, SpanId=ssp, ParentId=dsp,
                     Agent=sub_agent, ConvId="", Success="true", ResultCode="", ErrorType="", DurationMs=1500)
            share = 0.9 if sub_agent == "refund-agent" else 0.15
            base = S_TOK[sub_agent] + int(ctx * share)
            sp2, _ = step(sub_agent, ssp, "tool_calls", 60, 0, first=True, base=base)
            self.tool(t, op, ssp, "lookup_order" if sub_agent == "order-agent" else "get_refund_policy", True,
                      '{"id": 1}', 600)
            t += dt.timedelta(milliseconds=self.last_tool_ms + 20)
            step(sub_agent, ssp, "stop", 150, 0, base=base + 700)
            ctx += 200
        if r.random() < 0.10:
            self.tool(t, op, root, "create_ticket", True, '{"k": 1}', 80)
            t += dt.timedelta(milliseconds=self.last_tool_ms + 20)
        # 4) 최종 응답 (E3, E4)
        final_out = int(min(max(r.gauss(450, 250), 60), 1800))
        truncated = r.random() < 0.03
        finish, status, mt = "stop", "", None
        if truncated:
            finish, status, mt, final_out = "length", "incomplete", 256, 256
        if r.random() < 0.01:
            finish = "content_filter"
        fsp, _ = step("support-router", root, finish, final_out, 700 if not truncated else 256, max_tok=mt,
                      status=status)
        if truncated:                                         # 잘린 뒤 재요청
            step("support-router", root, "stop", 450, 300)
        if r.random() < 0.01:                                 # F2: 직전과 거의 같은 재요청
            step("support-router", root, "stop", 450, 300, base=prev_in + r.randint(-20, 20))
        total_ms = int((t - t0).total_seconds() * 1000)
        self.add("agent_spans", TimeGenerated=self.iso(t0), OperationId=op, SpanId=root, ParentId="",
                 Agent="support-router", ConvId=conv, Success="false" if failed_turn else "true",
                 ResultCode=500 if failed_turn else "", ErrorType="TimeoutError" if failed_turn else "",
                 DurationMs=total_ms)
        c["hist"] = hist + user + final_out + RESULT_TOK // 8       # C1: 이력 상한 없음
        c["last_end"] = t
        return t

    # --- 전체 --------------------------------------------------------------
    def run(self):
        a, r = self.a, self.r
        start = dt.datetime.fromisoformat(a.start).replace(tzinfo=None)
        plan = []
        for i in range(a.convs):
            conv = f"conv-{i}"
            self.conv[conv] = {"hist": 0, "last_end": None}
            t = start + dt.timedelta(seconds=r.uniform(0, a.days * 86400 - 3600))
            for _ in range(r.choice([1, 1, 2, 3, 4, 6, 9, 12])):
                route = r.choices(["order", "refund", "faq"], [0.45, 0.2, 0.35])[0]
                plan.append((t, conv, route, r.random() < 0.4))
                t += dt.timedelta(seconds=r.choice([15, 25, 90, 240, 420, 900, 1800]))
        # A6: 하루 한 번 몰림
        for d in range(a.days):
            burst = start + dt.timedelta(days=d, hours=10)
            for j in range(40):
                conv = f"burst-{d}-{j}"
                self.conv[conv] = {"hist": 0, "last_end": None}
                plan.append((burst + dt.timedelta(seconds=r.uniform(0, 90)), conv, "faq", False))
        plan.sort(key=lambda x: x[0])
        for t, conv, route, quick in plan:
            self.turn(t, conv, route, quick)            # A1: 일부 템플릿 변형에서 System prompt에 시각이 들어간다
        return self

    def write(self):
        out = pathlib.Path(self.a.out) / "data"
        # tool_schemas: 에이전트별 최신 정의
        day0 = dt.datetime.fromisoformat(self.a.start)
        for ag, tools in TOOLS.items():
            for name, ch, dch in tools:
                self.add("tool_schemas", AgentName=ag, CapturedAt=self.iso(day0), ToolName=name, Chars=ch,
                         DescChars=dch)
        decl = {}
        for f in QDIR.glob("*.kql"):
            t = f.read_text(encoding="utf-8")
            nm = re.search(r"//!\s*name:\s*(\S+)", t).group(1)
            decl[nm] = [c.strip() for c in re.search(r"//!\s*columns:\s*(.*)", t).group(1).split(",")]
        decl["calls"] = decl["calls"]
        counts = {}
        for name, rows in self.rows.items():
            by_day = defaultdict(list)
            for rw in rows:
                key = (rw.get("TimeGenerated") or rw.get("CapturedAt"))[:10]
                by_day[key].append(rw)
            for day, rs in by_day.items():
                p = out / name / f"{day}.csv"
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("w", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=decl[name], extrasaction="ignore")
                    w.writeheader()
                    for rw in rs:
                        w.writerow({k: rw.get(k, "") for k in decl[name]})
            counts[name] = len(rows)
        return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2026-10-20")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--convs", type=int, default=250)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    g = Gen(a).run()
    print(g.write())
    # 설정 템플릿도 함께 만든다
    cfg = pathlib.Path(a.out) / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "model-params.csv").write_text(
        "Model,PIn,PCache,PWrite,POut,MinTok,Round128,WindowMin,RpmLimit\n"
        "gpt-5.4,2.00,0.20,0.00,8.00,1024,true,5.0,15\n"
        "gpt-5-mini,0.40,0.04,0.00,1.60,1024,true,5.0,15\n")
    (cfg / "agents.csv").write_text(
        "Agent,STok,Method,Note\nsupport-router,9800,1,synthetic\norder-agent,4200,1,synthetic\n"
        "refund-agent,900,1,synthetic\n")


if __name__ == "__main__":
    main()
