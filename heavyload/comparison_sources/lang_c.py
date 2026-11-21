"""C workload sources (gcc/clang -O2 native builds).

C has no standard-library hash map, so workload C carries the idiomatic C
equivalent: a compact open-addressing table implemented in the source itself
(what any real C project writes). Workload B (JSON) is not applicable: the C
standard library has no JSON serializer (documented, never simulated).
Timing uses QueryPerformanceCounter on Windows and clock_gettime elsewhere.
"""

KEY = "c"
LABEL = "C"
EXT = ".c"
GROUP = "reference"
TOOL_KEYS = ("gcc",)
NOTE = ("Workload B (JSON) not applicable: the C standard library has no JSON "
        "serializer. Workload C uses a compact open-addressing hash table "
        "written in the source - the idiomatic C equivalent of a native map.")

_TIMER = r"""#if defined(_WIN32)
#include <windows.h>
static double hlc_sec(void) {
    LARGE_INTEGER f, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)f.QuadPart;
}
#else
#include <time.h>
static double hlc_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}
#endif
"""

PRIME = _TIMER + r"""
#include <stdio.h>
static int is_prime(long n) {
    if (n < 2) return 0;
    for (long d = 2; d * d <= n; d++) { if (n % d == 0) return 0; }
    return 1;
}
int main(void) {
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
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#define HLC_CAP 128
static char hlc_keys[HLC_CAP][32];
static long hlc_vals[HLC_CAP];
static uint64_t hlc_fnv(const char *s) {
    uint64_t h = 1469598103934665603ULL;
    while (*s) { h ^= (unsigned char)*s++; h *= 1099511628211ULL; }
    return h;
}
static void hlc_put(const char *k, long v) {
    uint64_t i = hlc_fnv(k) & (HLC_CAP - 1);
    while (hlc_keys[i][0]) {
        if (strcmp(hlc_keys[i], k) == 0) { hlc_vals[i] = v; return; }
        i = (i + 1) & (HLC_CAP - 1);
    }
    snprintf(hlc_keys[i], 32, "%s", k);
    hlc_vals[i] = v;
}
static long hlc_get(const char *k) {
    uint64_t i = hlc_fnv(k) & (HLC_CAP - 1);
    while (hlc_keys[i][0]) {
        if (strcmp(hlc_keys[i], k) == 0) return hlc_vals[i];
        i = (i + 1) & (HLC_CAP - 1);
    }
    return 0;
}
int main(void) {
    char k[32];
    for (int i = 0; i < 64; i++) {
        snprintf(k, 32, "k%d", i);
        hlc_put(k, (i * 37 + 11) % 997);
    }
    double t0 = hlc_sec();
    long acc = 0;
    for (long r = 0; r < 100000; r++) {
        snprintf(k, 32, "k%ld", r % 64);
        acc += hlc_get(k);
    }
    long us = (long)((hlc_sec() - t0) * 1e6);
    printf("RESULT %ld 100000\n", acc);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

TEXT = _TIMER + r"""
#include <stdio.h>
int main(void) {
    double t0 = hlc_sec();
    long total_chars = 0, acc = 0;
    for (long i = 0; i < 50000; i++) {
        char s[64];
        snprintf(s, 64, "user:%ld|score:%ld|tag:t%ld",
                 i, (i * 37) % 100, i % 13);
        size_t len = strlen(s);
        total_chars += (long)len;
        acc = (acc * 31 + (long)len) % 1000000007L;
    }
    long us = (long)((hlc_sec() - t0) * 1e6);
    printf("RESULT %ld %ld\n", total_chars, acc);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

MATMUL = _TIMER + r"""
#include <stdio.h>
int main(void) {
    enum { N = 128 };
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
    long us = (long)((hlc_sec() - t0) * 1.0e6);
    printf("RESULT %ld %ld\n", total, diag);
    printf("TIME_US %ld\n", us);
    return 0;
}
"""

SOURCES = {"prime": PRIME, "map": MAP, "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("gcc", {}).get("available"))


def filename(stem):
    return "hlc_%s.c" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    import os
    dest = "hlc_%s_c%s" % (stem, exe_suffix)
    rc, out, err, _ = call(["gcc", "-O2", "-std=c11", "hlc_%s.c" % stem,
                            "-o", dest],
                           cwd=work, timeout=build_timeout)
    if rc == 0 and os.path.isfile(os.path.join(work, dest)):
        return {"exe": os.path.join(work, dest)}
    warnings.append("%s: gcc build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("gcc", {}).get("version", "") or "?"
    return [{"key": "c", "label": "C (gcc %s) -O2" % ver.splitlines()[0],
             "group": GROUP, "cmd": [built["exe"]]}]
