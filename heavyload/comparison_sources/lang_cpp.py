"""C++ workload sources (g++/clang++ -O2 native builds).

Workload B (JSON) is not applicable: the C++ standard library has no JSON
serializer (documented, never simulated).
"""

KEY = "cpp"
LABEL = "C++"
EXT = ".cpp"
GROUP = "reference"
TOOL_KEYS = ("gpp",)
NOTE = ("Workload B (JSON) not applicable: the C++ standard library has no "
        "JSON serializer.")

_TIMER = r"""#include <chrono>
static double hlc_sec() {
    return std::chrono::duration<double>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
"""

PRIME = _TIMER + r"""
#include <cstdio>
static bool is_prime(long n) {
    if (n < 2) return false;
    for (long d = 2; d * d <= n; d++) { if (n % d == 0) return false; }
    return true;
}
int main() {
    double t0 = hlc_sec();
    long count = 0, checksum = 0;
    for (long i = 2; i < 50000; i++) {
        if (is_prime(i)) { count++; checksum += i; }
    }
    long us = (long)((hlc_sec() - t0) * 1e6);
    printf("RESULT %ld %ld\n", count, checksum);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

MAP = _TIMER + r"""
#include <cstdio>
#include <string>
#include <unordered_map>
int main() {
    std::unordered_map<std::string, long> t;
    t.reserve(64);
    for (long i = 0; i < 64; i++) t["k" + std::to_string(i)] = (i * 37 + 11) % 997;
    double t0 = hlc_sec();
    long acc = 0;
    for (long r = 0; r < 100000; r++) acc += t["k" + std::to_string(r % 64)];
    long us = (long)((hlc_sec() - t0) * 1e6);
    printf("RESULT %ld 100000\n", acc);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

TEXT = _TIMER + r"""
#include <cstdio>
#include <string>
int main() {
    double t0 = hlc_sec();
    long total_chars = 0, acc = 0;
    for (long i = 0; i < 50000; i++) {
        std::string s = "user:" + std::to_string(i)
                      + "|score:" + std::to_string((i * 37) % 100)
                      + "|tag:t" + std::to_string(i % 13);
        total_chars += (long)s.size();
        acc = (acc * 31 + (long)s.size()) % 1000000007L;
    }
    long us = (long)((hlc_sec() - t0) * 1e6);
    printf("RESULT %ld %ld\n", total_chars, acc);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

MATMUL = _TIMER + r"""
#include <cstdio>
int main() {
    const int N = 128;
    static long a[N][N], bt[N][N];
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) a[i][j] = (i * 7 + j * 13) % 19 - 9;
    }
    for (int k = 0; k < N; k++) {
        for (int j = 0; j < N; j++) bt[k][j] = (k * 11 + j * 3) % 17 - 8;
    }
    double t0 = hlc_sec();
    long total = 0, diag = 0;
    for (int i = 0; i < N; i++) {
        const long *ai = a[i];
        for (int k = 0; k < N; k++) {
            const long *btk = bt[k];
            long s = 0;
            for (int j = 0; j < N; j++) s += ai[j] * btk[j];
            total += s;
            if (i == k) diag += s;
        }
    }
    long us = (long)((hlc_sec() - t0) * 1e6);
    printf("RESULT %ld %ld\n", total, diag);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

SOURCES = {"prime": PRIME, "map": MAP, "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("gpp", {}).get("available"))


def filename(stem):
    return "hlc_%s.cpp" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    import os
    dest = "hlc_%s_cpp%s" % (stem, exe_suffix)
    rc, out, err, _ = call(["g++", "-O2", "-std=c++17", "hlc_%s.cpp" % stem,
                            "-o", dest],
                           cwd=work, timeout=build_timeout)
    if rc == 0 and os.path.isfile(os.path.join(work, dest)):
        return {"exe": os.path.join(work, dest)}
    warnings.append("%s: g++ build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("gpp", {}).get("version", "") or "?"
    return [{"key": "cpp", "label": "C++ (g++ %s) -O2" % ver.splitlines()[0],
             "group": GROUP, "cmd": [built["exe"]]}]
