"""Python workload sources (CPython; sys.executable is the measured binary)."""

KEY = "python"
LABEL = "Python"
EXT = ".py"
GROUP = "baseline"
TOOL_KEYS = ("python",)
NOTE = None

PRIME = r"""import time
def is_prime(n):
    if n < 2: return False
    d = 2
    while d * d <= n:
        if n % d == 0: return False
        d += 1
    return True
t0 = time.perf_counter_ns()
i, count, checksum = 2, 0, 0
while i < 50000:
    if is_prime(i):
        count += 1; checksum += i
    i += 1
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", count, checksum)
print("TIME_US", us)
"""

JSON = r"""import json, time
def build_rows(n):
    return [{"id": i, "sku": "SKU-" + str(i), "qty": (i % 7) + 1,
             "price": (i * 37) % 1000} for i in range(n)]
rows = build_rows(200)
t0 = time.perf_counter_ns()
total = 0
last_len = 0
for r in range(600):
    s = json.dumps(rows, separators=(",", ":"))
    back = json.loads(s)
    last_len = len(s)
    for row in back:
        total += row["price"] * row["qty"] + row["id"]
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", total, last_len)
print("TIME_US", us)
"""

MAP = r"""import time
def build_table(n):
    return {"k" + str(i): (i * 37 + 11) % 997 for i in range(n)}
t = build_table(64)
t0 = time.perf_counter_ns()
acc = 0
for r in range(100000):
    acc += t["k" + str(r % 64)]
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", acc, 100000)
print("TIME_US", us)
"""

TEXT = r"""import time
ROWS = 50000
t0 = time.perf_counter_ns()
total_chars = 0
acc = 0
for i in range(ROWS):
    s = ("user:" + str(i) + "|score:" + str((i * 37) % 100)
         + "|tag:t" + str(i % 13))
    total_chars += len(s)
    acc = (acc * 31 + len(s)) % 1000000007
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", total_chars, acc)
print("TIME_US", us)
"""

MATMUL = r"""import time
N = 128
A = [[(i * 7 + j * 13) % 19 - 9 for j in range(N)] for i in range(N)]
Bt = [[(k * 11 + j * 3) % 17 - 8 for j in range(N)] for k in range(N)]
t0 = time.perf_counter_ns()
total = 0
diag = 0
i = 0
while i < N:
    Ai = A[i]
    k = 0
    while k < N:
        Btk = Bt[k]
        s = 0
        j = 0
        while j < N:
            s += Ai[j] * Btk[j]
            j += 1
        total += s
        if i == k:
            diag += s
        k += 1
    i += 1
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", total, diag)
print("TIME_US", us)
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("python", {}).get("available"))


def filename(stem):
    # "hlc_" prefix: a bare "json.py" in the script directory SHADOWS the
    # stdlib json module (script dir precedes sys.path stdlib entries), which
    # silently double-executes workload B.
    return "hlc_%s.py" % stem


def variants(stem, work, tools, built, python_exe):
    ver = ".".join(tools.get("python", {}).get("version", "").split(".")[:2])
    return [{"key": "python", "label": "Python %s" % (ver or "?"),
             "group": GROUP, "cmd": [python_exe, filename(stem)]}]
