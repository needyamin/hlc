"""Swift workload sources (swiftc -O native builds).

Arithmetic uses Int (64-bit on all supported platforms). Workload B (JSON)
uses Foundation's JSONSerialization — the native serializer on every platform
Swift ships for — and is checksum-gated like every other row: if the platform
serializer produces a different byte length, the row is REJECTED and never
published. Requires Swift 5.9+ (ContinuousClock).
"""

KEY = "swift"
LABEL = "Swift"
EXT = ".swift"
GROUP = "reference"
TOOL_KEYS = ("swiftc",)
NOTE = ("Workload B uses Foundation JSONSerialization (the native serializer). "
        "If a platform's serializer changes the byte length, the checksum gate "
        "rejects the row instead of publishing it. Requires Swift 5.9+ "
        "(ContinuousClock).")

PRIME = r"""func isPrime(_ n: Int) -> Bool {
    if n < 2 { return false }
    var d: Int = 2
    while d * d <= n { if n % d == 0 { return false }; d += 1 }
    return true
}
let clock = ContinuousClock()
let t0 = clock.now
var count = 0
var checksum = 0
var i: Int = 2
while i < 50000 {
    if isPrime(i) { count += 1; checksum += i }
    i += 1
}
let delta = clock.now - t0
let comps = delta.components
let us = comps.seconds * 1_000_000 + comps.attoseconds / 1_000_000_000_000
print("RESULT \(count) \(checksum)")
print("TIME_US \(us)")
"""

JSON = r"""import Foundation
let rows: [[String: Any]] = (0..<200).map { i in
    ["id": i, "sku": "SKU-\(i)", "qty": (i % 7) + 1, "price": (i * 37) % 1000]
}
let clock = ContinuousClock()
let t0 = clock.now
var total = 0
var lastLen = 0
for _ in 0..<600 {
    let d = try JSONSerialization.data(withJSONObject: rows)
    let back = try JSONSerialization.jsonObject(with: d) as! [[String: Any]]
    lastLen = d.count
    for row in back {
        total += (row["price"] as! Int) * (row["qty"] as! Int) + (row["id"] as! Int)
    }
}
let delta = clock.now - t0
let comps = delta.components
let us = comps.seconds * 1_000_000 + comps.attoseconds / 1_000_000_000_000
print("RESULT \(total) \(lastLen)")
print("TIME_US \(us)")
"""

MAP = r"""var t: [String: Int] = [:]
for i in 0..<64 { t["k\(i)"] = (i * 37 + 11) % 997 }
let clock = ContinuousClock()
let t0 = clock.now
var acc = 0
for r in 0..<100000 { acc += t["k\(r % 64)"]! }
let delta = clock.now - t0
let comps = delta.components
let us = comps.seconds * 1_000_000 + comps.attoseconds / 1_000_000_000_000
print("RESULT \(acc) 100000")
print("TIME_US \(us)")
"""

TEXT = r"""let clock = ContinuousClock()
let t0 = clock.now
var totalChars = 0
var acc = 0
for i in 0..<50000 {
    let s = "user:\(i)|score:\((i * 37) % 100)|tag:t\(i % 13)"
    totalChars += s.count
    acc = (acc * 31 + s.count) % 1_000_000_007
}
let delta = clock.now - t0
let comps = delta.components
let us = comps.seconds * 1_000_000 + comps.attoseconds / 1_000_000_000_000
print("RESULT \(totalChars) \(acc)")
print("TIME_US \(us)")
"""

MATMUL = r"""let n = 128
var a = [[Int]](repeating: [Int](repeating: 0, count: n), count: n)
var bt = [[Int]](repeating: [Int](repeating: 0, count: n), count: n)
for i in 0..<n {
    for j in 0..<n { a[i][j] = (i * 7 + j * 13) % 19 - 9 }
}
for k in 0..<n {
    for j in 0..<n { bt[k][j] = (k * 11 + j * 3) % 17 - 8 }
}
let clock = ContinuousClock()
let t0 = clock.now
var total = 0
var diag = 0
for i in 0..<n {
    let ai = a[i]
    for k in 0..<n {
        let btk = bt[k]
        var s = 0
        for j in 0..<n { s += ai[j] * btk[j] }
        total += s
        if i == k { diag += s }
    }
}
let delta = clock.now - t0
let comps = delta.components
let us = comps.seconds * 1_000_000 + comps.attoseconds / 1_000_000_000_000
print("RESULT \(total) \(diag)")
print("TIME_US \(us)")
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("swiftc", {}).get("available"))


def filename(stem):
    return "hlc_%s.swift" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    import os
    dest = "hlc_%s_swift%s" % (stem, exe_suffix)
    rc, out, err, _ = call(["swiftc", "-O", "hlc_%s.swift" % stem, "-o", dest],
                           cwd=work, timeout=build_timeout)
    if rc == 0 and os.path.isfile(os.path.join(work, dest)):
        return {"exe": os.path.join(work, dest)}
    warnings.append("%s: swiftc build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("swiftc", {}).get("version", "") or "?"
    return [{"key": "swift", "label": "Swift %s -O" % ver, "group": GROUP,
             "cmd": [built["exe"]]}]
