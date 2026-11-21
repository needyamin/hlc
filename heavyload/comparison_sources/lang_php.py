"""PHP workload sources (CLI sapi)."""

KEY = "php"
LABEL = "PHP"
EXT = ".php"
GROUP = "baseline"
TOOL_KEYS = ("php",)
NOTE = None

PRIME = r"""<?php
function is_prime($n) {
    if ($n < 2) return false;
    $d = 2;
    while ($d * $d <= $n) { if ($n % $d == 0) return false; $d += 1; }
    return true;
}
$t0 = hrtime(true);
$i = 2; $count = 0; $checksum = 0;
while ($i < 50000) { if (is_prime($i)) { $count += 1; $checksum += $i; } $i += 1; }
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $count $checksum\n";
echo "TIME_US $us\n";
"""

JSON = r"""<?php
function build_rows($n) {
    $rows = [];
    for ($i = 0; $i < $n; $i++) {
        $rows[] = ["id" => $i, "sku" => "SKU-" . $i,
                   "qty" => ($i % 7) + 1, "price" => ($i * 37) % 1000];
    }
    return $rows;
}
$rows = build_rows(200);
$t0 = hrtime(true);
$total = 0;
$last_len = 0;
for ($r = 0; $r < 600; $r++) {
    $s = json_encode($rows);
    $back = json_decode($s, true);
    $last_len = strlen($s);
    foreach ($back as $row) {
        $total += $row["price"] * $row["qty"] + $row["id"];
    }
}
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $total $last_len\n";
echo "TIME_US $us\n";
"""

MAP = r"""<?php
function build_table($n) {
    $t = [];
    for ($i = 0; $i < $n; $i++) $t["k" . $i] = ($i * 37 + 11) % 997;
    return $t;
}
$t = build_table(64);
$t0 = hrtime(true);
$acc = 0;
for ($r = 0; $r < 100000; $r++) {
    $acc += $t["k" . ($r % 64)];
}
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $acc 100000\n";
echo "TIME_US $us\n";
"""

TEXT = r"""<?php
$ROWS = 50000;
$t0 = hrtime(true);
$total_chars = 0;
$acc = 0;
for ($i = 0; $i < $ROWS; $i++) {
    $s = "user:" . $i . "|score:" . (($i * 37) % 100) . "|tag:t" . ($i % 13);
    $total_chars += strlen($s);
    $acc = ($acc * 31 + strlen($s)) % 1000000007;
}
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $total_chars $acc\n";
echo "TIME_US $us\n";
"""

MATMUL = r"""<?php
$N = 128;
$A = [];
$Bt = [];
for ($i = 0; $i < $N; $i++) {
    $row = [];
    for ($j = 0; $j < $N; $j++) $row[] = ($i * 7 + $j * 13) % 19 - 9;
    $A[] = $row;
}
for ($k = 0; $k < $N; $k++) {
    $row = [];
    for ($j = 0; $j < $N; $j++) $row[] = ($k * 11 + $j * 3) % 17 - 8;
    $Bt[] = $row;
}
$t0 = hrtime(true);
$total = 0;
$diag = 0;
for ($i = 0; $i < $N; $i++) {
    $Ai = $A[$i];
    for ($k = 0; $k < $N; $k++) {
        $Btk = $Bt[$k];
        $s = 0;
        for ($j = 0; $j < $N; $j++) $s += $Ai[$j] * $Btk[$j];
        $total += $s;
        if ($i === $k) $diag += $s;
    }
}
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $total $diag\n";
echo "TIME_US $us\n";
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("php", {}).get("available"))


def filename(stem):
    # "hlc_" prefix: keeps generated sources from shadowing same-named
    # modules in toolchains that resolve imports from the script directory.
    return "hlc_%s.php" % stem


def variants(stem, work, tools, built):
    ver = ".".join(tools.get("php", {}).get("version", "").split(".")[:2])
    return [{"key": "php", "label": "PHP %s" % (ver or "?"),
             "group": GROUP, "cmd": ["php", "hlc_%s.php" % stem]}]
