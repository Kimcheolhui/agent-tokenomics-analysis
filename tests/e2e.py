#!/usr/bin/env python3
"""종단 검증. skill 폴더의 스크립트만으로 합성 데이터 → 지표 → 보고서를 만들고, 심어 둔 문제가 잡히는지 확인한다.

duckdb가 있는 인터프리터로 실행한다:   python3 tests/e2e.py
시나리오:
  full         모든 데이터. 심어 둔 문제가 기대한 판정 이상으로 잡혀야 한다
  no-optional  retrieval, content_digest, tool_schemas가 없는 경우. 오류 없이 끝나야 한다
  no-instr     cache_read, reasoning, conversation.id가 없는 경우. 해당 원인이 `계측 부재`여야 한다
추출 KQL 검사는 node와 scripts/kql/node_modules가 있을 때만 돈다(설치: cd scripts/kql && npm install).
"""
import csv, json, pathlib, shutil, subprocess, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skill" / "agent-tokenomics-diagnosis"
PY = sys.executable
RANK = {"해당": 3, "후보": 2}

# 심어 둔 문제 → 최소 판정. "해당"이 기대되는 것은 "해당" 이상이어야 하고, "후보"는 후보 이상이면 된다.
EXPECT_AT_LEAST = {
    "A1": "해당", "A4": "해당", "A5": "후보", "A6": "후보", "B1": "후보", "B2": "후보", "C1": "해당",
    "C2": "후보", "C3": "후보", "D1": "해당", "D4": "후보", "E1": "후보", "E4": "해당",
    "F1": "해당",
}
# 심어 둔 문제지만 가이드에 수치 기준이 없어 스크립트가 판정하지 않는 원인. 값과 함께 판정 보류로 남아야 한다
EXPECT_HOLD = {"D3": "support-router", "F3": "support-router"}
EXPECT_NOT_FLAGGED = ["A2", "D2", "F4"]       # 심지 않은 문제. 해당·후보가 나오면 오탐이다


def sh(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, p.stdout + p.stderr


def run_scenario(name, work):
    steps = []
    rc, out = sh([PY, str(SKILL / "scripts" / "run_metrics.py"), "--root", str(work), "--out", str(work / "out" / "metrics.json")])
    steps.append(("run_metrics", rc, out.strip().splitlines()[-1] if out.strip() else ""))
    if rc:
        return steps, None
    rc, out = sh([PY, str(SKILL / "scripts" / "build_report.py"), "--metrics", str(work / "out" / "metrics.json"),
                  "--root", str(work), "--out", str(work / "out" / "report.html")])
    steps.append(("build_report", rc, out.strip().splitlines()[0] if out.strip() else ""))
    rc2, out2 = sh([PY, str(SKILL / "scripts" / "validate_report.py"), str(work / "out" / "report.json")])
    steps.append(("validate_report", rc2, out2.strip().splitlines()[-1]))
    html = (work / "out" / "report.html").read_text(encoding="utf-8")
    steps.append(("html", 0 if "const REPORT = {" in html and "const R = REPORT;" in html else 1, f"{len(html):,} bytes"))
    return steps, json.loads((work / "out" / "report.json").read_text(encoding="utf-8"))


def degrade(work, kind):
    if kind == "no-optional":
        for d in ("retrieval", "content_digest", "tool_schemas"):
            shutil.rmtree(work / "data" / d, ignore_errors=True)
    elif kind == "no-instr":
        for sub, cols in (("calls", {"CacheTok": 0, "ReasonTok": 0, "ConvId": ""}), ("agent_spans", {"ConvId": ""})):
            for f in (work / "data" / sub).glob("*.csv"):
                rows = list(csv.DictReader(f.open(encoding="utf-8")))
                names = list(rows[0].keys())
                for r in rows:
                    r.update(cols)
                w = csv.DictWriter(f.open("w", encoding="utf-8", newline=""), fieldnames=names)
                w.writeheader()
                w.writerows(rows)


def main():
    failures = []
    ok = lambda b, msg: (print(("  PASS " if b else "  FAIL ") + msg), None if b else failures.append(msg))

    print("[템플릿 문체]")
    rc, out = sh([PY, str(REPO / "scripts" / "lint_text.py"), str(REPO / "report" / "token-diagnosis-report.html")])
    ok(rc == 0, "템플릿(REPORT 예시와 고정 문구) 문체 검사 0건" + ("" if rc == 0 else f"\n{out}"))

    print("\n[skill 동기화]")
    rc, out = sh([PY, str(REPO / "scripts" / "build_skill.py"), "--check"])
    ok(rc == 0, "skill 폴더가 원본과 같다" + ("" if rc == 0 else f"\n{out}"))

    print("\n[추출 KQL 문법·스키마]")
    if shutil.which("node") and (REPO / "scripts" / "kql" / "node_modules").exists():
        rc, out = sh(["node", str(REPO / "scripts" / "kql" / "check_kql.js")])
        ok(rc == 0, "queries/export/*.kql이 Kusto 파서 검사를 통과하고 결과 컬럼이 //! columns와 같다" + ("" if rc == 0 else f"\n{out}"))
    else:
        print("  SKIP node 또는 scripts/kql/node_modules가 없다")

    with tempfile.TemporaryDirectory(prefix="tokenomics_e2e_") as t:
        base = pathlib.Path(t) / "base"
        rc, out = sh([PY, str(REPO / "scripts" / "gen_synthetic.py"), "--out", str(base), "--days", "3", "--convs", "250", "--seed", "7"])
        ok(rc == 0, "합성 데이터 생성")
        shutil.copy2(REPO / "config" / "thresholds.csv", base / "config" / "thresholds.csv")

        for scen in ("full", "no-optional", "no-instr"):
            print(f"\n[{scen}]")
            work = pathlib.Path(t) / scen
            shutil.copytree(base, work)
            degrade(work, scen)
            steps, rep = run_scenario(scen, work)
            for s, rc, msg in steps:
                ok(rc == 0, f"{s}: {msg}")
            if rep is None:
                continue
            v = {c["id"]: c["verdict"] for c in rep["causes"]}
            ok(len(v) == 25, f"원인 25개 판정 ({len(v)})")
            rc, out = sh([PY, str(SKILL / "scripts" / "lint_text.py"), str(work / "out" / "report.json")])
            ok(rc == 0, "REPORT 문체 검사 0건" + ("" if rc == 0 else f"\n{out}"))
            if scen == "full":
                for cid, want in EXPECT_AT_LEAST.items():
                    got = v[cid]
                    ok(RANK.get(got, 0) >= RANK[want], f"{cid} 기대 {want} 이상 · 실제 {got}")
                for cid, agent in EXPECT_HOLD.items():
                    c_ = next(c for c in rep["causes"] if c["id"] == cid)
                    ok(c_["verdict"] == "판정 보류" and agent in (c_.get("basis") or ""), f"{cid} 판정 보류이고 근거에 {agent}의 값이 있다 · 실제 {c_['verdict']}")
                for cid in EXPECT_NOT_FLAGGED:
                    ok(v[cid] not in RANK, f"{cid} 심지 않은 문제가 잡히지 않아야 한다 · 실제 {v[cid]}")
                cards = sorted([c for c in rep["causes"] if c["verdict"] in RANK and c.get("waste")], key=lambda c: -c["waste"]["usd"])
                ok(len(cards) >= 8, f"카드 {len(cards)}개 (≥ 8)")
                top = [c["id"] for c in cards[:5]]
                ok("A1" in top, f"A1이 낭비 상위 5위 안에 든다 · 상위 {top}")
                ok(all(c["waste"]["usd"] >= 0 for c in cards), "낭비 추정이 모두 0 이상이다")
                esc = next((c for c in rep["causes"] if c["id"] == "B1"), {})
                ok("escalate_to_human" in (esc.get("finding") or ""), "B1이 미사용 tool escalate_to_human을 지목한다")
            elif scen == "no-optional":
                ok(v["A2"] == "미점검" and v["C3"] == "미점검", f"content·retrieval이 없으면 A2·C3 미점검 · {v['A2']}, {v['C3']}")
                ok(v["A1"] in ("후보", "해당"), f"content 없이도 A1은 후보 이상 · {v['A1']}")
            else:
                for cid in ("A1", "A3", "A4", "A5", "A6", "C1", "E2", "F3"):
                    ok(v[cid] == "계측 부재", f"{cid} 계측 부재 · 실제 {v[cid]}")
                ok(all(s["status"] == "nodata" for s in rep["symptoms"] if s["id"] in ("S1", "S2", "S3", "S6")), "S1·S2·S3·S6가 nodata다")

    print(f"\n{'모두 통과' if not failures else str(len(failures)) + '건 실패'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
