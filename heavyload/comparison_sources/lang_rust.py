"""Rust workload sources (rustc -O reference builds).

Workload B (JSON) is intentionally absent: Rust's stdlib ships no JSON
serializer, and a hand-rolled parser would NOT be the native-serializer
comparison B measures (same rule as Java / Kotlin / C / C++).
"""

KEY = "rust"
LABEL = "Rust"
EXT = ".rs"
GROUP = "reference"
TOOL_KEYS = ("rustc",)
NOTE = ("Workload B (JSON) not applicable: Rust's stdlib has no JSON "
        "serializer - a hand-rolled parser would not be the native-serializer "
        "comparison workload B measures.")

PRIME = r"""use std::time::Instant;
fn is_prime(n: i64) -> bool {
    if n < 2 { return false; }
    let mut d: i64 = 2;
    while d * d <= n { if n % d == 0 { return false; } d += 1; }
    true
}
fn main() {
    let t0 = Instant::now();
    let mut i: i64 = 2;
    let mut count: i64 = 0;
    let mut checksum: i64 = 0;
    while i < 50000 { if is_prime(i) { count += 1; checksum += i; } i += 1; }
    let us = t0.elapsed().as_micros();
    println!("RESULT {} {}", count, checksum);
    println!("TIME_US {}", us);
}
"""

MAP = r"""use std::collections::HashMap;
use std::time::Instant;
fn main() {
    let mut t: HashMap<String, i64> = HashMap::new();
    for i in 0..64i64 {
        t.insert(format!("k{}", i), (i * 37 + 11) % 997);
    }
    let t0 = Instant::now();
    let mut acc: i64 = 0;
    for r in 0..100000i64 {
        acc += t[&format!("k{}", r % 64)];
    }
    let us = t0.elapsed().as_micros();
    println!("RESULT {} 100000", acc);
    println!("TIME_US {}", us);
}
"""

TEXT = r"""use std::time::Instant;
fn main() {
    let rows: i64 = 50000;
    let t0 = Instant::now();
    let mut total_chars: i64 = 0;
    let mut acc: i64 = 0;
    let mut i: i64 = 0;
    while i < rows {
        let s = format!("user:{}|score:{}|tag:t{}", i, (i * 37) % 100, i % 13);
        total_chars += s.len() as i64;
        acc = (acc * 31 + s.len() as i64) % 1000000007;
        i += 1;
    }
    let us = t0.elapsed().as_micros();
    println!("RESULT {} {}", total_chars, acc);
    println!("TIME_US {}", us);
}
"""

MATMUL = r"""use std::time::Instant;
fn main() {
    let n: usize = 128;
    let mut a: Vec<Vec<i64>> = vec![vec![0i64; n]; n];
    let mut bt: Vec<Vec<i64>> = vec![vec![0i64; n]; n];
    for i in 0..n {
        for j in 0..n { a[i][j] = (i as i64 * 7 + j as i64 * 13) % 19 - 9; }
    }
    for k in 0..n {
        for j in 0..n { bt[k][j] = (k as i64 * 11 + j as i64 * 3) % 17 - 8; }
    }
    let t0 = Instant::now();
    let mut total: i64 = 0;
    let mut diag: i64 = 0;
    for i in 0..n {
        let ai = &a[i];
        for k in 0..n {
            let btk = &bt[k];
            let mut s: i64 = 0;
            for j in 0..n { s += ai[j] * btk[j]; }
            total += s;
            if i == k { diag += s; }
        }
    }
    let us = t0.elapsed().as_micros();
    println!("RESULT {} {}", total, diag);
    println!("TIME_US {}", us);
}
"""

SOURCES = {"prime": PRIME, "map": MAP, "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("rustc", {}).get("available"))


def filename(stem):
    # "hlc_" prefix: keeps generated sources unambiguous in sources/ and
    # avoids any same-named module resolution.
    return "hlc_%s.rs" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    """rustc -O reference build for every workload that ships a .rs source."""
    import os
    dest = "hlc_%s_rs%s" % (stem, exe_suffix)
    rc, out, err, _ = call(["rustc", "-O", "hlc_%s.rs" % stem, "-o", dest],
                           cwd=work, timeout=build_timeout)
    if rc == 0 and os.path.isfile(os.path.join(work, dest)):
        return {"exe": os.path.join(work, dest)}
    warnings.append("%s: rustc build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built or not built.get("exe"):
        return []
    ver = tools.get("rustc", {}).get("version", "").split(" ")[1] \
        if " " in tools.get("rustc", {}).get("version", "") else ""
    return [{"key": "rust", "label": "Rust %s -O native" % (ver or ""),
             "group": GROUP, "cmd": [built["exe"]]}]
