"""Java workload sources (javac build + JVM run; single-file-per-workload).

Arithmetic uses 64-bit ``long`` to match the i64/wrapping semantics of the
other compiled implementations. Workload B (JSON) is not applicable: the Java
standard library has no JSON serializer (documented, never simulated).
"""

KEY = "java"
LABEL = "Java"
EXT = ".java"
GROUP = "reference"
TOOL_KEYS = ("java", "javac")
NOTE = ("Workload B (JSON) not applicable: the Java standard library has no "
        "JSON serializer - a hand-rolled parser would not be the "
        "native-serializer comparison workload B measures.")

PRIME = r"""public class HlcPrime {
    static boolean isPrime(long n) {
        if (n < 2) return false;
        for (long d = 2; d * d <= n; d++) { if (n % d == 0) return false; }
        return true;
    }
    public static void main(String[] args) {
        long t0 = System.nanoTime();
        long count = 0, checksum = 0;
        for (long i = 2; i < 50000; i++) {
            if (isPrime(i)) { count++; checksum += i; }
        }
        long us = (System.nanoTime() - t0) / 1000;
        System.out.println("RESULT " + count + " " + checksum);
        System.out.println("TIME_US " + us);
    }
}
"""

MAP = r"""import java.util.HashMap;
import java.util.Map;
public class HlcMap {
    public static void main(String[] args) {
        Map<String, Long> t = new HashMap<>();
        for (long i = 0; i < 64; i++) t.put("k" + i, (i * 37 + 11) % 997);
        long t0 = System.nanoTime();
        long acc = 0;
        for (long r = 0; r < 100000; r++) acc += t.get("k" + (r % 64));
        long us = (System.nanoTime() - t0) / 1000;
        System.out.println("RESULT " + acc + " 100000");
        System.out.println("TIME_US " + us);
    }
}
"""

TEXT = r"""public class HlcText {
    public static void main(String[] args) {
        long t0 = System.nanoTime();
        long totalChars = 0, acc = 0;
        for (long i = 0; i < 50000; i++) {
            String s = "user:" + i + "|score:" + ((i * 37) % 100)
                     + "|tag:t" + (i % 13);
            totalChars += s.length();
            acc = (acc * 31 + s.length()) % 1000000007L;
        }
        long us = (System.nanoTime() - t0) / 1000;
        System.out.println("RESULT " + totalChars + " " + acc);
        System.out.println("TIME_US " + us);
    }
}
"""

MATMUL = r"""public class HlcMatmul {
    public static void main(String[] args) {
        int n = 128;
        long[][] a = new long[n][n];
        long[][] bt = new long[n][n];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) a[i][j] = (i * 7 + j * 13) % 19 - 9;
        }
        for (int k = 0; k < n; k++) {
            for (int j = 0; j < n; j++) bt[k][j] = (k * 11 + j * 3) % 17 - 8;
        }
        long t0 = System.nanoTime();
        long total = 0, diag = 0;
        for (int i = 0; i < n; i++) {
            long[] ai = a[i];
            for (int k = 0; k < n; k++) {
                long[] btk = bt[k];
                long s = 0;
                for (int j = 0; j < n; j++) s += ai[j] * btk[j];
                total += s;
                if (i == k) diag += s;
            }
        }
        long us = (System.nanoTime() - t0) / 1000;
        System.out.println("RESULT " + total + " " + diag);
        System.out.println("TIME_US " + us);
    }
}
"""

SOURCES = {"prime": PRIME, "map": MAP, "text": TEXT, "matmul": MATMUL}

# Class name must equal the file name for javac; the "Hlc" prefix keeps the
# generated class names unambiguous and mirrors the hlc_ source naming.
CLASS_NAMES = {"prime": "HlcPrime", "map": "HlcMap",
               "text": "HlcText", "matmul": "HlcMatmul"}


def available(tools):
    return all(tools.get(k, {}).get("available") for k in TOOL_KEYS)


def filename(stem):
    return "%s.java" % CLASS_NAMES.get(stem, "Hlc" + stem.capitalize())


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    """javac -d <classes> <file> -> artifacts {"classes": dir, "main": class}."""
    import os
    classes = os.path.join(work, "jclasses")
    os.makedirs(classes, exist_ok=True)
    src = filename(stem)
    rc, out, err, _ = call(["javac", "-d", classes, src],
                           cwd=work, timeout=build_timeout)
    if rc == 0:
        return {"classes": classes, "main": CLASS_NAMES.get(stem, stem)}
    warnings.append("%s: javac build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("java", {}).get("version", "")
    # `java -version` prints to stderr: "openjdk version \"21.0.1\" ..." or
    # "java version ..."; extract the first version-like token.
    ver_tok = ver.split('"')[1] if '"' in ver else (ver or "?")
    major = ver_tok.split(".")[0] if ver_tok else "?"
    return [{"key": "java", "label": "Java %s" % major, "group": GROUP,
             "cmd": ["java", "-cp", built["classes"], built["main"]]}]
