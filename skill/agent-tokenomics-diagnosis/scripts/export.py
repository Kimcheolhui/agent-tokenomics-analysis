#!/usr/bin/env python3
"""Log Analytics에서 진단용 CSV를 내린다.

queries/export/*.kql을 그대로 읽어 실행한다. 포털에 붙여 넣는 쿼리와 같은 파일이므로,
스크립트 경로와 수동 경로의 결과가 논리적으로 같다.

기간은 하루 단위로 쪼개 파일마다 저장한다. 기간을 늘려도 이미 뽑은 날은 다시 뽑지 않는다.

예:
  python3 scripts/export.py --workspace <GUID> --from 2026-10-20 --to 2026-11-03
  python3 scripts/export.py --workspace <GUID> --from 2026-10-20 --to 2026-11-03 --include-sensitive
  python3 scripts/export.py --workspace <GUID> --from 2026-11-01 --to 2026-11-02 --only calls --dry-run
  python3 scripts/export.py --workspace <GUID> --from 2026-10-20 --to 2026-11-03 \
      --only content_by_span --spans spans.txt --include-sensitive
"""
import argparse
import csv
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUERY_DIR = ROOT / "queries" / "export"
ROW_WARN = 450_000          # Log Analytics API 상한 50만 행에 대한 경고선
PORTAL_WARN = 100_000       # 포털 UI 상한


def parse_directives(text):
    """.kql 머리의 //! 지시자를 읽는다."""
    d = {}
    for line in text.splitlines():
        m = re.match(r"^//!\s*([a-z_]+)\s*:\s*(.*)$", line.strip())
        if m:
            d[m.group(1)] = m.group(2).strip()
    return d


def load_queries():
    out = {}
    for path in sorted(QUERY_DIR.glob("*.kql")):
        text = path.read_text(encoding="utf-8")
        d = parse_directives(text)
        name = d.get("name") or path.stem
        out[name] = {
            "path": path,
            "text": text,
            "columns": [c.strip() for c in d.get("columns", "").split(",") if c.strip()],
            "sensitive": d.get("sensitive", "false").lower() == "true",
            "needs_spans": "spans" in d,
        }
    return out


def resolve(text, day_from, day_to, spans=None):
    """T0/T1 줄과 SpanId 목록을 치환한다. 치환 결과는 포털에 그대로 붙여 넣을 수 있다."""
    text = re.sub(r"let T0 = datetime\([^)]*\);",
                  f"let T0 = datetime({day_from.isoformat()});", text, count=1)
    text = re.sub(r"let T1 = datetime\([^)]*\);",
                  f"let T1 = datetime({day_to.isoformat()});", text, count=1)
    if spans is not None:
        text = text.replace('"SPAN_IDS"', ", ".join(json.dumps(s) for s in spans))
    return text


def run_query(workspace, query, timespan=None):
    cmd = ["az", "monitor", "log-analytics", "query",
           "--workspace", workspace, "--analytics-query", query, "--output", "json"]
    # Basic 요금제 테이블(AppGenAIContent)이 들어간 쿼리는 쿼리 안의 시간 조건과 별개로 timespan을 명시해야 한다
    if timespan:
        cmd += ["--timespan", timespan]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "az 호출이 실패했다")
    payload = json.loads(p.stdout or "[]")
    # az는 보통 행 객체 배열을 돌려준다. tables 형태도 처리한다.
    if isinstance(payload, dict) and "tables" in payload:
        rows = []
        for t in payload["tables"]:
            cols = [c["name"] for c in t.get("columns", [])]
            rows.extend(dict(zip(cols, r)) for r in t.get("rows", []))
        return rows
    return payload if isinstance(payload, list) else []


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = columns or (list(rows[0].keys()) if rows else [])
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    return header


def day_range(start, end):
    d = start
    while d < end:
        yield d, d + dt.timedelta(days=1)
        d += dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", help="Log Analytics workspace customer ID (GUID)")
    ap.add_argument("--from", dest="date_from", required=True, help="시작 날짜 (YYYY-MM-DD, 포함)")
    ap.add_argument("--to", dest="date_to", required=True, help="끝 날짜 (YYYY-MM-DD, 제외)")
    ap.add_argument("--out", default="data", help="출력 디렉터리 (기본 data)")
    ap.add_argument("--only", help="쉼표로 구분한 쿼리 이름. 생략하면 민감하지 않은 전부")
    ap.add_argument("--include-sensitive", action="store_true", help="원문이 들어 있는 추출을 포함한다")
    ap.add_argument("--spans", help="SpanId 목록 파일 (한 줄에 하나). content_by_span에 쓴다")
    ap.add_argument("--overwrite", action="store_true", help="이미 있는 날짜 파일도 다시 뽑는다")
    ap.add_argument("--dry-run", action="store_true", help="실행하지 않고 치환된 쿼리를 출력한다")
    a = ap.parse_args()

    if not a.dry_run and not a.workspace:
        ap.error("--workspace가 필요하다 (--dry-run일 때는 생략할 수 있다)")

    start = dt.date.fromisoformat(a.date_from)
    end = dt.date.fromisoformat(a.date_to)
    if start >= end:
        ap.error("--from이 --to보다 앞이어야 한다")

    queries = load_queries()
    if a.only:
        wanted = [n.strip() for n in a.only.split(",")]
        unknown = [n for n in wanted if n not in queries]
        if unknown:
            ap.error(f"모르는 쿼리 이름: {', '.join(unknown)}. 있는 것: {', '.join(queries)}")
    else:
        wanted = [n for n, q in queries.items() if not q["sensitive"] and not n.startswith("precheck")]

    spans = None
    if a.spans:
        spans = [l.strip() for l in pathlib.Path(a.spans).read_text().splitlines() if l.strip()]

    out_root = ROOT / a.out
    failures = []

    for name in wanted:
        q = queries[name]
        if q["sensitive"] and not a.include_sensitive:
            print(f"[skip] {name}: 원문 추출이다. --include-sensitive가 필요하다")
            continue
        if q["needs_spans"] and not spans:
            print(f"[skip] {name}: --spans 파일이 필요하다")
            continue

        for d0, d1 in day_range(start, end):
            path = out_root / name / f"{d0.isoformat()}.csv"
            if path.exists() and not a.overwrite:
                print(f"[have] {path.relative_to(ROOT)}")
                continue

            query = resolve(q["text"], d0, d1, spans)
            if a.dry_run:
                print(f"\n----- {name} {d0} -----\n{query}")
                continue

            # Basic 테이블이 들어간 쿼리는 최근 30일까지만 조회된다
            if "AppGenAIContent" in query and (dt.date.today() - d0).days > 30:
                print(f"[warn] {name} {d0}: AppGenAIContent는 Basic 테이블이라 30일보다 오래된 날은 조회되지 않는다. "
                      "검색 작업(search job)으로 따로 내려야 한다", file=sys.stderr)
            try:
                rows = run_query(a.workspace, query, f"{d0.isoformat()}T00:00:00Z/{d1.isoformat()}T00:00:00Z")
            except Exception as e:                      # noqa: BLE001
                print(f"[fail] {name} {d0}: {e}", file=sys.stderr)
                failures.append((name, d0.isoformat()))
                continue

            write_csv(path, q["columns"], rows)
            n = len(rows)
            flag = ""
            if n >= ROW_WARN:
                flag = "  ← API 상한 50만 행에 가깝다. 이 날은 시간 단위로 더 쪼개야 한다"
            elif n >= PORTAL_WARN:
                flag = "  ← 포털 UI 상한 10만 행을 넘는다. 수동 추출로는 이 날을 받을 수 없다"
            print(f"[ok]   {path.relative_to(ROOT)}  {n} rows{flag}")

    if failures:
        print(f"\n실패 {len(failures)}건. 같은 명령을 다시 돌리면 실패한 날만 다시 뽑는다:", file=sys.stderr)
        for name, day in failures:
            print(f"  {name} {day}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
