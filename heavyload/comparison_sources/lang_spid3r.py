"""Spid3r (.matrix) workload sources - the subject language of this tool.

Spid3r exposes only ``now_ms()`` (1 ms resolution), so the in-process TIME_US
value is derived as ``ms * 1000``. The variants (tree-walk, VM, VM --no-opt,
bytecode exec, native --release) are built and added by build()/variants().
Sources are copied verbatim from the originally verified implementations.
"""

KEY = "matrix"
LABEL = "Spid3r"
EXT = ".matrix"
GROUP = "spid3r"
TOOL_KEYS = ("spid3r",)
NOTE = ("Spid3r exposes only now_ms() (1 ms resolution); workloads are sized "
        "to stay clear of the timer floor and near-floor medians are flagged.")

PRIME = r"""fn is_prime(n: int) -> bool {
    if n < 2 { return false; }
    let d = 2;
    while d * d <= n { if n % d == 0 { return false; } d += 1; }
    return true;
}
fn main() {
    let t0 = now_ms();
    let i = 2;
    let count = 0;
    let checksum = 0;
    while i < 50000 {
        if is_prime(i) { count += 1; checksum += i; }
        i += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(count) + " " + string(checksum));
    println("TIME_US " + string(us));
    return 0;
}
"""

JSON = r"""fn build_rows(n: int) {
    let rows = list();
    let i = 0;
    while i < n {
        rows.push(map("id", i, "sku", "SKU-" + string(i), "qty", (i % 7) + 1, "price", (i * 37) % 1000));
        i += 1;
    }
    return rows;
}
fn main() {
    let rows = build_rows(200);
    let t0 = now_ms();
    let total = 0;
    let last_len = 0;
    let r = 0;
    while r < 600 {
        let s = json_write(rows);
        let back = json_parse(s);
        last_len = s.len();
        let i = 0;
        while i < back.len() {
            let row = back[i];
            total = total + row["price"] * row["qty"] + row["id"];
            i += 1;
        }
        r += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(total) + " " + string(last_len));
    println("TIME_US " + string(us));
    return 0;
}
"""

MAP = r"""fn build_table(n: int) {
    let t = map();
    let i = 0;
    while i < n {
        t["k" + string(i)] = (i * 37 + 11) % 997;
        i += 1;
    }
    return t;
}
fn main() {
    let t = build_table(64);
    let t0 = now_ms();
    let acc = 0;
    let r = 0;
    while r < 100000 {
        let key = "k" + string(r % 64);
        acc = acc + t[key];
        r += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(acc) + " 100000");
    println("TIME_US " + string(us));
    return 0;
}
"""

TEXT = r"""fn main() {
    let t0 = now_ms();
    let total_chars = 0;
    let acc = 0;
    let i = 0;
    while i < 50000 {
        let s = "user:" + string(i) + "|score:" + string((i * 37) % 100)
              + "|tag:t" + string(i % 13);
        total_chars = total_chars + s.len();
        acc = (acc * 31 + s.len()) % 1000000007;
        i += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(total_chars) + " " + string(acc));
    println("TIME_US " + string(us));
    return 0;
}
"""

MATMUL = r"""fn build_a(n: int) {
    let a = list();
    let i = 0;
    while i < n {
        let row = list();
        let j = 0;
        while j < n {
            row.push((i * 7 + j * 13) % 19 - 9);
            j += 1;
        }
        a.push(row);
        i += 1;
    }
    return a;
}
fn build_bt(n: int) {
    let bt = list();
    let k = 0;
    while k < n {
        let row = list();
        let j = 0;
        while j < n {
            row.push((k * 11 + j * 3) % 17 - 8);
            j += 1;
        }
        bt.push(row);
        k += 1;
    }
    return bt;
}
fn main() {
    let n = 128;
    let a = build_a(n);
    let bt = build_bt(n);
    let t0 = now_ms();
    let total = 0;
    let diag = 0;
    let i = 0;
    while i < n {
        let ai = a[i];
        let k = 0;
        while k < n {
            let btk = bt[k];
            let s = 0;
            let j = 0;
            while j < n {
                s = s + ai[j] * btk[j];
                j += 1;
            }
            total = total + s;
            if i == k { diag = diag + s; }
            k += 1;
        }
        i += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(total) + " " + string(diag));
    println("TIME_US " + string(us));
    return 0;
}
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("spid3r", {}).get("available"))


def filename(stem):
    return "%s.matrix" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    """Compile bytecode + native artifacts (same as the original build)."""
    built = {}
    for label, cmd, dest in [
        ("bytecode", ["spid3r", "compile", stem + ".matrix",
                      "-o", stem + ".matrixc"], stem + ".matrixc"),
        ("native", ["spid3r", "build", stem + ".matrix", "-o",
                    stem + "_native" + exe_suffix, "--release"],
         stem + "_native" + exe_suffix),
    ]:
        rc, out, err, _ = call(cmd, cwd=work, timeout=build_timeout)
        import os
        if rc == 0 and os.path.isfile(os.path.join(work, dest)):
            built[label] = os.path.join(work, dest)
        else:
            warnings.append("%s: %s build failed (%s)"
                            % (stem, label, (err or out).strip().splitlines()[0]
                               if (err or out).strip() else "rc=%s" % rc))
    return built


def variants(stem, work, tools, built):
    out = []
    ver = tools.get("spid3r", {}).get("version", "").replace(
        "spid3r ", "").split(" ")[0] or "?"
    out.append({"key": "spid3r_tw", "label": "Spid3r %s tree-walk" % ver,
                "group": GROUP, "cmd": ["spid3r", "run", stem + ".matrix"]})
    out.append({"key": "spid3r_vm", "label": "Spid3r %s VM" % ver,
                "group": GROUP,
                "cmd": ["spid3r", "run", "--engine", "vm",
                        stem + ".matrix"]})
    out.append({"key": "spid3r_vm_noopt",
                "label": "Spid3r %s VM --no-opt" % ver, "group": GROUP,
                "cmd": ["spid3r", "run", "--engine", "vm", "--no-opt",
                        stem + ".matrix"]})
    if built.get("bytecode"):
        out.append({"key": "spid3r_bytecode",
                    "label": "Spid3r %s bytecode exec" % ver, "group": GROUP,
                    "cmd": ["spid3r", "exec", stem + ".matrixc"]})
    if built.get("native"):
        out.append({"key": "spid3r_native",
                    "label": "Spid3r %s native --release" % ver,
                    "group": GROUP, "cmd": [built["native"]]})
    return out
