// queries/export/*.kql을 Microsoft Kusto 파서(@kusto/language-service-next)로 오프라인 검사한다.
// 1) 문법·의미 오류(없는 컬럼, 함수 인자 형식 등)  2) 결과 컬럼과 //! columns 지시자의 일치.
// 테이블 스키마는 schema.json(공식 테이블 레퍼런스)을 쓴다. 실행 결과는 보장하지 않는다.
// skill 폴더에서도 같은 상대 경로로 동작한다(scripts/kql/ 기준 두 단계 위에 queries/export가 있다).
// 사용: (cd scripts/kql && npm install) && node scripts/kql/check_kql.js
const fs = require('fs');
const path = require('path');
require('@kusto/language-service-next/bridge.js');
require('@kusto/language-service-next/Kusto.Language.Bridge.js');
const K = Kusto.Language;

const ROOT = path.resolve(__dirname, '..', '..');
const schema = JSON.parse(fs.readFileSync(path.join(__dirname, 'schema.json'), 'utf8'));
const tables = Object.keys(schema).filter(k => !k.startsWith('_'))
  .map(name => new K.Symbols.TableSymbol.$ctor7(name, '(' + schema[name] + ')', null));
const db = new K.Symbols.DatabaseSymbol.$ctor3('workspace', null, Bridge.toList(tables), false);
const globals = K.GlobalState.Default.WithDatabase(db);

// export.py resolve()와 같은 치환. SpanId 자리표시자는 예시 값으로 바꾼다.
function resolve(text) {
  return text
    .replace(/let T0 = datetime\([^)]*\);/, 'let T0 = datetime(2026-11-01);')
    .replace(/let T1 = datetime\([^)]*\);/, 'let T1 = datetime(2026-11-02);')
    .replace('"SPAN_IDS"', '"a1b2c3d4e5f60718", "0011223344556677"');
}

function lineCol(text, pos) {
  const pre = text.slice(0, pos).split('\n');
  return `${pre.length}:${pre[pre.length - 1].length + 1}`;
}

const dir = path.join(ROOT, 'queries', 'export');
let failed = 0;
for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.kql')).sort()) {
  const raw = fs.readFileSync(path.join(dir, f), 'utf8');
  const text = resolve(raw);
  const m = raw.match(/^\/\/!\s*columns\s*:\s*(.*)$/m);
  const declared = m ? m[1].split(',').map(s => s.trim()).filter(Boolean) : [];
  const code = K.KustoCode.ParseAndAnalyze(text, globals);
  const diags = code.GetDiagnostics();
  const problems = [];
  for (let i = 0; i < diags.Count; i++) {
    const d = diags.getItem(i);
    const sev = String(d.Severity);
    if (sev !== 'Error' && sev !== 'Warning') continue;
    problems.push(`${sev} ${d.Code} @${lineCol(text, d.Start)} ${d.Message}`);
  }
  const rt = code.ResultType;
  let actual = [];
  if (rt && rt.Columns) {
    for (let i = 0; i < rt.Columns.Count; i++) {
      const c = rt.Columns.getItem(i);
      actual.push(`${c.Name}:${c.Type.Name}`);
    }
  }
  const names = actual.map(s => s.split(':')[0]);
  if (declared.length && names.join(',') !== declared.join(',')) {
    problems.push(`Columns 불일치\n      선언: ${declared.join(',')}\n      결과: ${names.join(',')}`);
  }
  const errors = problems.filter(p => !p.startsWith('Warning'));
  if (errors.length) failed++;
  console.log(`${errors.length ? 'FAIL' : 'ok  '} ${f}`);
  for (const p of problems) console.log('    ' + p);
  if (process.argv.includes('--types')) console.log('    → ' + actual.join(', '));
}
console.log(failed ? `\n${failed}개 파일에 오류가 있다` : '\n모든 파일 통과');
process.exit(failed ? 1 : 0);
