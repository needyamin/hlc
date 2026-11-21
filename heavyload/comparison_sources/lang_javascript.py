"""JavaScript (Node.js) workload sources."""

KEY = "node"
LABEL = "Node"
EXT = ".js"
GROUP = "baseline"
TOOL_KEYS = ("node",)
NOTE = None

PRIME = r"""function isPrime(n) {
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

JSON = r"""function buildRows(n) {
  const rows = [];
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
  const back = JSON.parse(s);
  lastLen = s.length;
  for (let i = 0; i < back.length; i++) {
    const row = back[i];
    total += row.price * row.qty + row.id;
  }
}
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + total + " " + lastLen);
console.log("TIME_US " + us);
"""

MAP = r"""function buildTable(n) {
  const t = {};
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
const A = [], Bt = [];
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
    return bool(tools.get("node", {}).get("available"))


def filename(stem):
    # "hlc_" prefix: prevents accidental stdlib/module shadowing in any
    # toolchain that resolves imports from the script directory.
    return "hlc_%s.js" % stem


def variants(stem, work, tools, built):
    ver = tools.get("node", {}).get("version", "").lstrip("v").split(".")[0]
    return [{"key": "node", "label": "Node %s" % (ver or "?"),
             "group": GROUP, "cmd": ["node", "hlc_%s.js" % stem]}]
