"""Go workload sources (go build -> native binary; encoding/json stdlib).

Arithmetic uses int64 explicitly to match the other compiled languages.
"""

KEY = "go"
LABEL = "Go"
EXT = ".go"
GROUP = "reference"
TOOL_KEYS = ("go",)
NOTE = None

PRIME = r"""package main

import "fmt"
import "time"

func isPrime(n int64) bool {
	if n < 2 {
		return false
	}
	for d := int64(2); d*d <= n; d++ {
		if n%d == 0 {
			return false
		}
	}
	return true
}

func main() {
	t0 := time.Now()
	var count, checksum int64
	for i := int64(2); i < 50000; i++ {
		if isPrime(i) {
			count++
			checksum += i
		}
	}
	us := time.Since(t0).Microseconds()
	fmt.Printf("RESULT %d %d\n", count, checksum)
	fmt.Printf("TIME_US %d\n", us)
}
"""

JSON = r"""package main

import "encoding/json"
import "fmt"
import "time"

type Row struct {
	ID    int64  `json:"id"`
	SKU   string `json:"sku"`
	Qty   int64  `json:"qty"`
	Price int64  `json:"price"`
}

func buildRows(n int) []Row {
	rows := make([]Row, 0, n)
	for i := 0; i < n; i++ {
		rows = append(rows, Row{ID: int64(i), SKU: "SKU-" + fmt.Sprintf("%d", i),
			Qty: int64((i % 7) + 1), Price: int64((i * 37) % 1000)})
	}
	return rows
}

func main() {
	rows := buildRows(200)
	t0 := time.Now()
	var total int64
	lastLen := 0
	for r := 0; r < 600; r++ {
		s, err := json.Marshal(rows)
		if err != nil {
			panic(err)
		}
		var back []Row
		if err := json.Unmarshal(s, &back); err != nil {
			panic(err)
		}
		lastLen = len(s)
		for _, row := range back {
			total += row.Price*row.Qty + row.ID
		}
	}
	us := time.Since(t0).Microseconds()
	fmt.Printf("RESULT %d %d\n", total, lastLen)
	fmt.Printf("TIME_US %d\n", us)
}
"""

MAP = r"""package main

import "fmt"
import "time"

func main() {
	t := make(map[string]int64, 64)
	for i := 0; i < 64; i++ {
		t[fmt.Sprintf("k%d", i)] = int64((i*37 + 11) % 997)
	}
	t0 := time.Now()
	var acc int64
	for r := 0; r < 100000; r++ {
		acc += t[fmt.Sprintf("k%d", r%64)]
	}
	us := time.Since(t0).Microseconds()
	fmt.Printf("RESULT %d 100000\n", acc)
	fmt.Printf("TIME_US %d\n", us)
}
"""

TEXT = r"""package main

import "fmt"
import "time"

func main() {
	t0 := time.Now()
	var totalChars, acc int64
	for i := int64(0); i < 50000; i++ {
		s := fmt.Sprintf("user:%d|score:%d|tag:t%d", i, (i*37)%100, i%13)
		totalChars += int64(len(s))
		acc = (acc*31 + int64(len(s))) % 1000000007
	}
	us := time.Since(t0).Microseconds()
	fmt.Printf("RESULT %d %d\n", totalChars, acc)
	fmt.Printf("TIME_US %d\n", us)
}
"""

MATMUL = r"""package main

import "fmt"
import "time"

func main() {
	const N = 128
	var a, bt [N][N]int64
	for i := 0; i < N; i++ {
		for j := 0; j < N; j++ {
			a[i][j] = int64((i*7 + j*13) % 19 - 9)
		}
	}
	for k := 0; k < N; k++ {
		for j := 0; j < N; j++ {
			bt[k][j] = int64((k*11 + j*3) % 17 - 8)
		}
	}
	t0 := time.Now()
	var total, diag int64
	for i := 0; i < N; i++ {
		ai := &a[i]
		for k := 0; k < N; k++ {
			btk := &bt[k]
			var s int64
			for j := 0; j < N; j++ {
				s += ai[j] * btk[j]
			}
			total += s
			if i == k {
				diag += s
			}
		}
	}
	us := time.Since(t0).Microseconds()
	fmt.Printf("RESULT %d %d\n", total, diag)
	fmt.Printf("TIME_US %d\n", us)
}
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("go", {}).get("available"))


def filename(stem):
    return "hlc_%s.go" % stem


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    import os
    dest = "hlc_%s_go%s" % (stem, exe_suffix)
    rc, out, err, _ = call(["go", "build", "-o", dest, "hlc_%s.go" % stem],
                           cwd=work, timeout=build_timeout)
    if rc == 0 and os.path.isfile(os.path.join(work, dest)):
        return {"exe": os.path.join(work, dest)}
    warnings.append("%s: go build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("go", {}).get("version", "") or "?"
    # "go version go1.23.4 windows/amd64" -> "1.23"
    tok = ""
    for part in ver.split():
        if part.startswith("go") and part[2:3].isdigit():
            tok = part[2:]
            break
    return [{"key": "go", "label": "Go %s" % (".".join(tok.split(".")[:2])
                                               if tok else ver),
             "group": GROUP, "cmd": [built["exe"]]}]
