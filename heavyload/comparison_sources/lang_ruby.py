"""Ruby workload sources (CRuby/MRI interpreter)."""

KEY = "ruby"
LABEL = "Ruby"
EXT = ".rb"
GROUP = "baseline"
TOOL_KEYS = ("ruby",)
NOTE = None

PRIME = r"""def is_prime(n)
  return false if n < 2
  d = 2
  while d * d <= n
    return false if n % d == 0
    d += 1
  end
  true
end
t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
count = 0
checksum = 0
i = 2
while i < 50000
  if is_prime(i)
    count += 1
    checksum += i
  end
  i += 1
end
us = (Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond) - t0) / 1000
puts "RESULT #{count} #{checksum}"
puts "TIME_US #{us}"
"""

JSON = r"""require 'json'
def build_rows(n)
  (0...n).map { |i| {"id" => i, "sku" => "SKU-#{i}",
                     "qty" => (i % 7) + 1, "price" => (i * 37) % 1000} }
end
rows = build_rows(200)
t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
total = 0
last_len = 0
600.times do
  s = JSON.generate(rows)
  back = JSON.parse(s)
  last_len = s.length
  back.each { |row| total += row["price"] * row["qty"] + row["id"] }
end
us = (Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond) - t0) / 1000
puts "RESULT #{total} #{last_len}"
puts "TIME_US #{us}"
"""

MAP = r"""def build_table(n)
  t = {}
  (0...n).each { |i| t["k#{i}"] = (i * 37 + 11) % 997 }
  t
end
t = build_table(64)
t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
acc = 0
100_000.times { |r| acc += t["k#{r % 64}"] }
us = (Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond) - t0) / 1000
puts "RESULT #{acc} 100000"
puts "TIME_US #{us}"
"""

TEXT = r"""ROWS = 50_000
t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
total_chars = 0
acc = 0
(0...ROWS).each do |i|
  s = "user:#{i}|score:#{(i * 37) % 100}|tag:t#{i % 13}"
  total_chars += s.length
  acc = (acc * 31 + s.length) % 1_000_000_007
end
us = (Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond) - t0) / 1000
puts "RESULT #{total_chars} #{acc}"
puts "TIME_US #{us}"
"""

MATMUL = r"""N = 128
A = Array.new(N) { |i| Array.new(N) { |j| (i * 7 + j * 13) % 19 - 9 } }
Bt = Array.new(N) { |k| Array.new(N) { |j| (k * 11 + j * 3) % 17 - 8 } }
t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond)
total = 0
diag = 0
(0...N).each do |i|
  ai = A[i]
  (0...N).each do |k|
    btk = Bt[k]
    s = 0
    (0...N).each { |j| s += ai[j] * btk[j] }
    total += s
    diag += s if i == k
  end
end
us = (Process.clock_gettime(Process::CLOCK_MONOTONIC, :nanosecond) - t0) / 1000
puts "RESULT #{total} #{diag}"
puts "TIME_US #{us}"
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("ruby", {}).get("available"))


def filename(stem):
    # "hlc_" prefix: keeps generated sources from shadowing same-named
    # modules in toolchains that resolve requires from the script directory.
    return "hlc_%s.rb" % stem


def variants(stem, work, tools, built):
    ver = tools.get("ruby", {}).get("version", "") or "?"
    parts = ver.split()
    ver_tok = parts[1] if len(parts) > 1 else ver
    ver_short = ".".join(ver_tok.split(".")[:2])
    return [{"key": "ruby", "label": "Ruby %s" % ver_short,
             "group": GROUP, "cmd": ["ruby", "hlc_%s.rb" % stem]}]
