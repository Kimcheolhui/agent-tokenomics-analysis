#!/usr/bin/env python3
"""queries/metrics/*.sql을 DuckDB로 실행해 원인별 지표를 JSON 하나로 모은다.

SQL 파일 하나는 `-- @query 이름` 표지로 여러 쿼리를 담을 수 있다.
결과 구조:  { "<파일 ID>": { "<쿼리 이름>": [ {컬럼: 값, ...}, ... ] | {"error": "..."} } }

데이터가 비어 있는 테이블(예: retrieval, content_digest)은 빈 표로 처리한다.
작업 디렉터리는 --root이고, 그 아래 data/와 config/를 읽는다.

예:  python3 scripts/run_metrics.py --root . --out out/metrics.json
"""
import argparse, datetime as dt, decimal, json, os, pathlib, re, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
EMPTY_HEADERS = {
    "calls": "TimeGenerated,OperationId,SpanId,ParentId,Op,Agent,ConvId,Model,RequestModel,InTok,CacheTok,WriteTok,OutTok,ReasonTok,Finish,Status,MaxTok,ReasonLevel,Compacted,Endpoint,ErrorType,Success,ResultCode,DurationMs",
}


def js(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def split_queries(text):
    parts = re.split(r"^--\s*@query\s+(\S+)\s*$", text, flags=re.M)
    # parts: [前文, name1, body1, name2, body2, ...]
    return [(parts[i], parts[i + 1].strip().rstrip(";")) for i in range(1, len(parts) - 1, 2)]


def load_params(root):
    """config/thresholds.csv를 읽는다. 고객 폴더에 없으면 저장소 기본값을 쓴다."""
    import csv
    for base in (root, REPO):
        f = base / "config" / "thresholds.csv"
        if f.exists():
            return {r["Key"]: (r["Value"] or "").strip() for r in csv.DictReader(f.open(encoding="utf-8"))}
    return {}


def inline_params(sql, params):
    """thrn('키') / thrs('키')를 값 리터럴로 치환한다.
    quantile_cont의 분위수 인자는 상수여야 해서 서브쿼리 매크로로는 쓸 수 없다."""
    def num(m):
        v = params.get(m.group(1), "")
        try:
            float(v)
            return f"({v}::DOUBLE)"
        except ValueError:
            return "NULL::DOUBLE"

    def txt(m):
        v = params.get(m.group(1), "")
        return "'" + v.replace("'", "''") + "'" if v else "NULL::VARCHAR"

    sql = re.sub(r"thrn\('([^']+)'\)", num, sql)
    return re.sub(r"thrs\('([^']+)'\)", txt, sql)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="data/와 config/가 있는 디렉터리")
    ap.add_argument("--out", default="out/metrics.json")
    ap.add_argument("--only", help="쉼표로 구분한 파일 ID (예: A1,C2)")
    a = ap.parse_args()

    import duckdb
    root = pathlib.Path(a.root).resolve()
    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = root / out
    os.chdir(root)

    con = duckdb.connect()
    con.execute((REPO / "queries" / "views.sql").read_text(encoding="utf-8"))
    sel = set(x.strip() for x in a.only.split(",")) if a.only else None
    params = load_params(root)

    result, errors = {}, 0
    for f in sorted((REPO / "queries" / "metrics").glob("*.sql")):
        fid = f.stem
        if sel and fid not in sel:
            continue
        result[fid] = {}
        for name, sql in split_queries(f.read_text(encoding="utf-8")):
            try:
                cur = con.execute(inline_params(sql, params))
                cols = [d[0] for d in cur.description]
                result[fid][name] = [{c: js(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
            except Exception as e:                       # noqa: BLE001
                result[fid][name] = {"error": str(e).splitlines()[0]}
                errors += 1
                print(f"[error] {fid}.{name}: {str(e).splitlines()[0]}", file=sys.stderr)

    # 점검 뷰도 함께 담는다. 빌더가 계측 부재와 모델 매칭을 판정할 때 쓴다.
    result["_checks"] = {}
    for v in ("overview", "instrumentation", "unpriced_calls", "missing_sa"):
        cur = con.execute(f"SELECT * FROM {v}")
        cols = [d[0] for d in cur.description]
        result["_checks"][v] = [{c: js(x) for c, x in zip(cols, row)} for row in cur.fetchall()]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for k, v in result.items() if k != "_checks")
    print(f"{n} queries, {errors} errors → {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
