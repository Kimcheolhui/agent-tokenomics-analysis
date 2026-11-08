#!/usr/bin/env python3
"""skill/agent-tokenomics-diagnosis/를 이 저장소의 원본에서 만든다.

직접 편집하는 파일은 skill/agent-tokenomics-diagnosis/SKILL.md 하나다. 나머지는 모두 여기서 생성한다.
  scripts/ queries/ config/ report/   저장소의 같은 경로를 그대로 복사 (런타임 레이아웃이 같아 경로 수정이 필요 없다)
  references/                          docs/ 문서를 복사하고, 진단 가이드를 범주별로 쪼갠다

사용:
  python3 scripts/build_skill.py          생성
  python3 scripts/build_skill.py --check  생성 결과가 현재 skill 폴더와 같은지 확인 (다르면 종료 코드 1)
"""
import argparse, filecmp, pathlib, re, shutil, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skill" / "agent-tokenomics-diagnosis"

RUNTIME_SCRIPTS = ["export.py", "run_metrics.py", "build_report.py", "validate_report.py", "report_io.py", "lint_text.py"]
COPY_DIRS = ["queries", "config"]
DOCS = {"data-contract.md": "data-contract.md", "report-schema.md": "report-schema.md",
        "build-mapping.md": "build-mapping.md", "metrics-notes.md": "metrics-notes.md",
        "writing-style.md": "writing-style.md"}
PATH_FIX = {"docs/data-contract.md": "references/data-contract.md",
            "docs/report-schema.md": "references/report-schema.md",
            "docs/build-mapping.md": "references/build-mapping.md",
            "docs/metrics-notes.md": "references/metrics-notes.md",
            "docs/writing-style.md": "references/writing-style.md",
            "docs/diagnosis-guide.md": "references/guide-common.md 와 references/catalog-*.md",
            "docs/backlog.md": "(저장소의 docs/backlog.md)"}


def fix_paths(text):
    for a, b in PATH_FIX.items():
        text = text.replace(a, b)
    return text


def split_guide(text):
    """진단 가이드를 최상위(##) 절로 나눈다. 앞머리(제목·작성 정보)는 공통 파일에 붙인다."""
    parts = re.split(r"(?m)^(?=## )", text)
    head, secs = parts[0], parts[1:]
    out = {}
    common = [head]
    for s in secs:
        title = s.splitlines()[0]
        m = re.match(r"## ([A-F])\. ", title)
        if m:
            out[f"catalog-{m.group(1)}.md"] = f"# 문제 카탈로그 {m.group(1)}\n\n" + s
        elif title.startswith("## 1. ") or title.startswith("## 2. ") or title.startswith("## 3. "):
            out.setdefault("guide-overview.md", "# 문제 범주 개요와 증상 → 후보 원인\n\n")
            out["guide-overview.md"] += s
        elif title.startswith("## 출처"):
            out["sources.md"] = "# 출처\n\n" + s.split("\n", 1)[1].lstrip("\n")
        else:
            common.append(s)
    out["guide-common.md"] = "".join(common)
    return out


def generate(dst):
    dst.mkdir(parents=True, exist_ok=True)
    # 런타임
    (dst / "scripts").mkdir(exist_ok=True)
    for f in RUNTIME_SCRIPTS:
        shutil.copy2(REPO / "scripts" / f, dst / "scripts" / f)
    for d in COPY_DIRS:
        shutil.copytree(REPO / d, dst / d, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    (dst / "report").mkdir(exist_ok=True)
    shutil.copy2(REPO / "report" / "token-diagnosis-report.html", dst / "report" / "token-diagnosis-report.html")
    # 참조 문서
    ref = dst / "references"
    ref.mkdir(exist_ok=True)
    for src, name in DOCS.items():
        (ref / name).write_text(fix_paths((REPO / "docs" / src).read_text(encoding="utf-8")), encoding="utf-8")
    for name, body in split_guide((REPO / "docs" / "diagnosis-guide.md").read_text(encoding="utf-8")).items():
        (ref / name).write_text(fix_paths(body), encoding="utf-8")


def same(a, b):
    """두 디렉터리가 파일 단위로 같은가 (SKILL.md는 직접 편집하므로 비교에서 뺀다)."""
    diffs = []
    fa = {p.relative_to(a) for p in a.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"}
    fb = {p.relative_to(b) for p in b.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"}
    for r in sorted(fa | fb):
        if r.name == "SKILL.md":
            continue
        if r not in fa:
            diffs.append(f"없음(생성 결과에만): {r}")
        elif r not in fb:
            diffs.append(f"남음(skill에만): {r}")
        elif not filecmp.cmp(a / r, b / r, shallow=False):
            diffs.append(f"다름: {r}")
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        with tempfile.TemporaryDirectory() as t:
            tmp = pathlib.Path(t) / "skill"
            generate(tmp)
            d = same(tmp, SKILL)
        for x in d:
            print(x)
        print("skill이 원본과 같다" if not d else f"{len(d)}건 다르다. python3 scripts/build_skill.py 로 다시 생성한다")
        return 1 if d else 0
    # 직접 편집 파일(SKILL.md)만 남기고 지운 뒤 생성한다
    keep = SKILL / "SKILL.md"
    text = keep.read_text(encoding="utf-8") if keep.exists() else None
    for child in list(SKILL.iterdir()) if SKILL.exists() else []:
        if child.name != "SKILL.md":
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    generate(SKILL)
    if text is not None:
        keep.write_text(text, encoding="utf-8")
    files = sorted(p.relative_to(SKILL) for p in SKILL.rglob("*") if p.is_file())
    print(f"{len(files)}개 파일 → {SKILL.relative_to(REPO)}")
    for r in sorted({str(f.parent) for f in files}):
        print(f"  {r}/ ({sum(1 for f in files if str(f.parent) == r)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
