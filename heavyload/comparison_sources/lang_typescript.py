"""TypeScript workload sources (tsc -> node run; semantics identical to JS)."""

KEY = "typescript"
LABEL = "TypeScript"
EXT = ".ts"
GROUP = "baseline"
TOOL_KEYS = ("tsc", "node")
NOTE = ("Compiled with tsc (untimed) and run on Node; runtime semantics are "
        "the ECMAScript ones, measured separately from the plain JS row.")

PRIME = r"""function isPrime(n: number): boolean {
  if (n < 2) return false;
  let d = 2;
  while (d * d <= n) { if (n % d === 0) return false; d += 1; }
  return true;
}
const t0 = process.hrtime.bigint();
let i = 2, count = 0, checksum = 0;
while (i < 50000) { if (isPrime(i)) { count += 1; checksum += i; } i += 1; }
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log('RESULT ' + count + ' ' + checksum);
console.log('TIME_US ' + us);
"""

JSON = r"""interface Row { id: number; sku: string; qty: number; price: number; }
function buildRows(n: number): Row[] {
  const rows: Row[] = [];
  for (let i = 0; i < n; i++) {
    rows.push({id: i, sku: "SKU-" + i, qty: (i % 7) + 1, price: (i * 37) % 1000});
  }
  return rows;
}
const rows = buildRows(200);
const t0 = process.hrtime.bigint();
let total = 0, lastLen = 0;
for (let r = 0; r < 600; r++) {
  const s = JSON.stringify(rows);
  const back = JSON.parse(s) as Row[];
  lastLen = s.length;
  for (const row of back) total += row.price * row.qty + row.id;
}
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + total + " " + lastLen);
console.log("TIME_US " + us);
"""

MAP = r"""function buildTable(n: number): Record<string, number> {
  const t: Record<string, number> = {};
  for (let i = 0; i < n; i++) t["k" + i] = (i * 37 + 11) % 997;
  return t;
}
const t = buildTable(64);
const t0 = process.hrtime.bigint();
let acc = 0;
for (let r = 0; r < 100000; r++) acc += t["k" + (r % 64)];
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + acc + " 100000");
console.log("TIME_US " + us);
"""

TEXT = r"""const ROWS = 50000;
const t0 = process.hrtime.bigint();
let totalChars = 0, acc = 0;
for (let i = 0; i < ROWS; i++) {
  const s = "user:" + i + "|score:" + ((i * 37) % 100) + "|tag:t" + (i % 13);
  totalChars += s.length;
  acc = (acc * 31 + s.length) % 1000000007;
}
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + totalChars + " " + acc);
console.log("TIME_US " + us);
"""

MATMUL = r"""const N = 128;
const A: number[][] = [], Bt: number[][] = [];
for (let i = 0; i < N; i++) {
  A.push([]);
  for (let j = 0; j < N; j++) A[i].push((i * 7 + j * 13) % 19 - 9);
}
for (let k = 0; k < N; k++) {
  Bt.push([]);
  for (let j = 0; j < N; j++) Bt[k].push((k * 11 + j * 3) % 17 - 8);
}
const t0 = process.hrtime.bigint();
let total = 0, diag = 0;
for (let i = 0; i < N; i++) {
  const Ai = A[i];
  for (let k = 0; k < N; k++) {
    const Btk = Bt[k];
    let s = 0;
    for (let j = 0; j < N; j++) s += Ai[j] * Btk[j];
    total += s;
    if (i === k) diag += s;
  }
}
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + total + " " + diag);
console.log("TIME_US " + us);
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return all(tools.get(k, {}).get("available") for k in TOOL_KEYS)


def filename(stem):
    return "hlc_%s.ts" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    """tsc <file> --outDir ts_out --target es2020 --module commonjs."""
    import os
    outdir = os.path.join(work, "ts_out")
    rc, out, err, _ = call(
        ["tsc", "hlc_%s.ts" % stem, "--outDir", outdir, "--target", "es2020",
         "--module", "commonjs", "--skipLibCheck"],
        cwd=work, timeout=build_timeout)
    js = os.path.join(outdir, "hlc_%s.js" % stem)
    if rc == 0 and os.path.isfile(js):
        return {"js": js}
    warnings.append("%s: tsc build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("tsc", {}).get("version", "") or "?"
    return [{"key": "typescript", "label": "TypeScript %s" % ver,
             "group": GROUP, "cmd": ["node", built["js"]]}]
