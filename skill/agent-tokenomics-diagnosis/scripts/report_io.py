"""보고서 HTML에서 REPORT 객체를 꺼내고 넣는 함수. 검증기와 빌더가 함께 쓴다.

템플릿(report/token-diagnosis-report.html)의 REPORT는 JS 객체 리터럴이라 JSON이 아니다(따옴표 없는 키,
작은따옴표 문자열). 빌더가 넣은 REPORT는 JSON이다. 꺼낼 때는 JSON을 먼저 시도하고, 실패하면 node로 평가한다.
"""
import json, pathlib, re, shutil, subprocess, tempfile

START = "const REPORT = {"
END = "\n};\n"          # 템플릿에서 REPORT 객체는 첫 번째 "\n};\n"에서 끝난다


def _bounds(html):
    a = html.index(START)
    b = html.index(END, a) + len(END)
    return a, b


def extract_report(html):
    a, b = _bounds(html)
    body = html[a + len("const REPORT = "): b].rstrip()
    body = body[:-1] if body.endswith(";") else body
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    node = shutil.which("node")
    if not node:
        raise ValueError("REPORT가 JSON이 아니고 node도 없어 읽을 수 없다")
    js = "var REPORT = " + body + ";\nprocess.stdout.write(JSON.stringify(REPORT));"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    try:
        out = subprocess.run([node, f.name], capture_output=True, text=True, check=True).stdout
    finally:
        pathlib.Path(f.name).unlink(missing_ok=True)
    return json.loads(out)


def inject_report(html, report):
    """REPORT 객체를 JSON으로 바꿔 넣는다. 나머지 HTML은 그대로 둔다."""
    a, b = _bounds(html)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    # </script>가 문자열 안에 있으면 HTML 파서가 스크립트를 끝낸다
    text = text.replace("</", "<\\/")
    return html[:a] + "const REPORT = " + text + ";\n" + html[b:]
