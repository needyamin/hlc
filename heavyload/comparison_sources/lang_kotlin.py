"""Kotlin/JVM workload sources (kotlinc -include-runtime jar + java -jar).

Arithmetic uses Long (64-bit) to match the other compiled implementations.
Workload B (JSON) is not applicable: the Kotlin standard library has no JSON
serializer (documented, never simulated). kotlinc is slow (~1 min per file),
so the build timeout is deliberately generous.
"""

KEY = "kotlin"
LABEL = "Kotlin"
EXT = ".kt"
GROUP = "reference"
TOOL_KEYS = ("kotlinc", "java")
NOTE = ("Workload B (JSON) not applicable: the Kotlin standard library has no "
        "JSON serializer (same rule as Java). Startup excluded via in-process "
        "TIME_US; the kotlinc build is untimed.")

PRIME = r"""fun isPrime(n: Long): Boolean {
    if (n < 2) return false
    var d: Long = 2
    while (d * d <= n) { if (n % d == 0L) return false; d += 1 }
    return true
}
fun main() {
    val t0 = System.nanoTime()
    var count = 0L
    var checksum = 0L
    var i: Long = 2
    while (i < 50000) {
        if (isPrime(i)) { count += 1; checksum += i }
        i += 1
    }
    val us = (System.nanoTime() - t0) / 1000
    println("RESULT $count $checksum")
    println("TIME_US $us")
}
"""

MAP = r"""fun main() {
    val t = HashMap<String, Long>()
    for (i in 0 until 64L) t["k$i"] = (i * 37 + 11) % 997
    val t0 = System.nanoTime()
    var acc = 0L
    for (r in 0 until 100000L) acc += t["k" + (r % 64)]!!
    val us = (System.nanoTime() - t0) / 1000
    println("RESULT $acc 100000")
    println("TIME_US $us")
}
"""

TEXT = r"""fun main() {
    val t0 = System.nanoTime()
    var totalChars = 0L
    var acc = 0L
    var i = 0L
    while (i < 50000L) {
        val s = "user:$i|score:${(i * 37) % 100}|tag:t${i % 13}"
        totalChars += s.length
        acc = (acc * 31 + s.length) % 1000000007L
        i += 1
    }
    val us = (System.nanoTime() - t0) / 1000
    println("RESULT $totalChars $acc")
    println("TIME_US $us")
}
"""

MATMUL = r"""fun main() {
    val n = 128
    val a = Array(n) { i -> LongArray(n) { j -> ((i * 7 + j * 13) % 19 - 9).toLong() } }
    val bt = Array(n) { k -> LongArray(n) { j -> ((k * 11 + j * 3) % 17 - 8).toLong() } }
    val t0 = System.nanoTime()
    var total = 0L
    var diag = 0L
    for (i in 0 until n) {
        val ai = a[i]
        for (k in 0 until n) {
            val btk = bt[k]
            var s = 0L
            for (j in 0 until n) s += ai[j] * btk[j]
            total += s
            if (i == k) diag += s
        }
    }
    val us = (System.nanoTime() - t0) / 1000
    println("RESULT $total $diag")
    println("TIME_US $us")
}
"""

SOURCES = {"prime": PRIME, "map": MAP, "text": TEXT, "matmul": MATMUL}


def available(tools):
    return all(tools.get(k, {}).get("available") for k in TOOL_KEYS)


def filename(stem):
    return "hlc_%s.kt" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    """kotlinc <file> -include-runtime -d <jar> (Main-Class set)."""
    import os
    jar = "hlc_%s_kt.jar" % stem
    rc, out, err, _ = call(
        ["kotlinc", "hlc_%s.kt" % stem, "-include-runtime", "-d", jar],
        cwd=work, timeout=build_timeout)
    if rc == 0 and os.path.isfile(os.path.join(work, jar)):
        return {"jar": os.path.join(work, jar)}
    warnings.append("%s: kotlinc build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("kotlinc", {}).get("version", "") or "?"
    return [{"key": "kotlin", "label": "Kotlin %s" % (ver.split(".")[0] or "?"),
             "group": GROUP, "cmd": ["java", "-jar", built["jar"]]}]
