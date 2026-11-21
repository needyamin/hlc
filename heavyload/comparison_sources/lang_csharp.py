"""C# / .NET workload sources (dotnet SDK build -c Release, then dotnet run).

Each workload is a minimal single-project build (Program.cs + app.csproj) so
no project scaffolding is required from the user. Requires the .NET SDK (a
runtime-only install cannot build). Arithmetic uses ``long`` (64-bit).
The TargetFramework is derived from the highest installed NETCore.App runtime
at detection time (tools["dotnet"]["tfm"]).
"""

import os
import re

KEY = "csharp"
LABEL = "C#"
EXT = ".cs"
GROUP = "reference"
TOOL_KEYS = ("dotnet",)
NOTE = ("Requires the .NET SDK (runtime-only installs cannot build). Each "
        "workload is a generated minimal project directory built with "
        "`dotnet build -c Release` (untimed); the run uses the built binary. "
        "TargetFramework is pinned to the highest installed "
        "Microsoft.NETCore.App runtime.")

_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>{tfm}</TargetFramework>
    <AssemblyName>HlcBench</AssemblyName>
    <RootNamespace>HlcBench</RootNamespace>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <ServerGarbageCollection>false</ServerGarbageCollection>
  </PropertyGroup>
</Project>
"""

PRIME = r"""using System;
using System.Diagnostics;

public static class Program
{
    static bool IsPrime(long n)
    {
        if (n < 2) return false;
        for (long d = 2; d * d <= n; d++) { if (n % d == 0) return false; }
        return true;
    }
    public static void Main()
    {
        var sw = Stopwatch.StartNew();
        long count = 0, checksum = 0;
        for (long i = 2; i < 50000; i++)
        {
            if (IsPrime(i)) { count++; checksum += i; }
        }
        sw.Stop();
        long us = sw.ElapsedTicks * 1000000L / Stopwatch.Frequency;
        Console.WriteLine("RESULT " + count + " " + checksum);
        Console.WriteLine("TIME_US " + us);
    }
}
"""

JSON = r"""using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.Json;

public sealed record Row(long id, string sku, long qty, long price);

public static class Program
{
    public static void Main()
    {
        var rows = new List<Row>(200);
        for (long i = 0; i < 200; i++)
            rows.Add(new Row(i, "SKU-" + i, (i % 7) + 1, (i * 37) % 1000));
        var sw = Stopwatch.StartNew();
        long total = 0; long lastLen = 0;
        for (int r = 0; r < 600; r++)
        {
            string s = JsonSerializer.Serialize(rows);
            var back = JsonSerializer.Deserialize<List<Row>>(s);
            lastLen = s.Length;
            foreach (var row in back)
                total += row.price * row.qty + row.id;
        }
        sw.Stop();
        long us = sw.ElapsedTicks * 1000000L / Stopwatch.Frequency;
        Console.WriteLine("RESULT " + total + " " + lastLen);
        Console.WriteLine("TIME_US " + us);
    }
}
"""

MAP = r"""using System;
using System.Collections.Generic;
using System.Diagnostics;

public static class Program
{
    public static void Main()
    {
        var t = new Dictionary<string, long>(64);
        for (long i = 0; i < 64; i++)
            t["k" + i] = (i * 37 + 11) % 997;
        var sw = Stopwatch.StartNew();
        long acc = 0;
        for (long r = 0; r < 100000; r++)
            acc += t["k" + (r % 64)];
        sw.Stop();
        long us = sw.ElapsedTicks * 1000000L / Stopwatch.Frequency;
        Console.WriteLine("RESULT " + acc + " 100000");
        Console.WriteLine("TIME_US " + us);
    }
}
"""

TEXT = r"""using System;
using System.Diagnostics;

public static class Program
{
    public static void Main()
    {
        var sw = Stopwatch.StartNew();
        long totalChars = 0, acc = 0;
        for (long i = 0; i < 50000; i++)
        {
            string s = "user:" + i + "|score:" + ((i * 37) % 100)
                     + "|tag:t" + (i % 13);
            totalChars += s.Length;
            acc = (acc * 31 + s.Length) % 1000000007L;
        }
        sw.Stop();
        long us = sw.ElapsedTicks * 1000000L / Stopwatch.Frequency;
        Console.WriteLine("RESULT " + totalChars + " " + acc);
        Console.WriteLine("TIME_US " + us);
    }
}
"""

MATMUL = r"""using System;
using System.Diagnostics;

public static class Program
{
    public static void Main()
    {
        const int N = 128;
        long[,] a = new long[N, N];
        long[,] bt = new long[N, N];
        for (int i = 0; i < N; i++)
            for (int j = 0; j < N; j++) a[i, j] = (i * 7 + j * 13) % 19 - 9;
        for (int k = 0; k < N; k++)
            for (int j = 0; j < N; j++) bt[k, j] = (k * 11 + j * 3) % 17 - 8;
        var sw = Stopwatch.StartNew();
        long total = 0, diag = 0;
        for (int i = 0; i < N; i++)
        {
            for (int k = 0; k < N; k++)
            {
                long s = 0;
                for (int j = 0; j < N; j++) s += a[i, j] * bt[k, j];
                total += s;
                if (i == k) diag += s;
            }
        }
        sw.Stop();
        long us = sw.ElapsedTicks * 1000000L / Stopwatch.Frequency;
        Console.WriteLine("RESULT " + total + " " + diag);
        Console.WriteLine("TIME_US " + us);
    }
}
"""

SOURCES = {"prime": PRIME, "json": JSON, "map": MAP,
           "text": TEXT, "matmul": MATMUL}


def available(tools):
    return bool(tools.get("dotnet", {}).get("available"))


def filename(stem):
    return "cs_%s/Program.cs" % stem    # one minimal project per workload


def extra_files(stem, tools):
    """Generated project files written next to Program.cs by the source
    writer (the same loop that writes every workload source)."""
    tfm = tools.get("dotnet", {}).get("tfm") or "net8.0"
    return {"cs_%s/app.csproj" % stem: _CSPROJ.replace("{tfm}", tfm)}


def build(work, stem, tools, build_timeout, call, warnings, exe_suffix):
    """dotnet build -c Release for the generated minimal project directory."""
    # stem comes from the pinned workload registry; enforce a strict charset
    # before it touches any path.
    if not re.fullmatch(r"[A-Za-z0-9_]+", stem or ""):
        raise ValueError("unsafe workload stem: %r" % stem)
    proj = os.path.join(work, "cs_%s" % stem)
    dll = os.path.join(proj, "out", "HlcBench.dll")
    rc, out, err, _ = call(
        ["dotnet", "build", "app.csproj", "-c", "Release", "-o", "out"],
        cwd=proj, timeout=build_timeout)
    if rc == 0 and os.path.isfile(dll):
        return {"dll": dll}
    warnings.append("%s: dotnet build failed (%s)"
                    % (stem, (err or out).strip().splitlines()[0]
                       if (err or out).strip() else "rc=%s" % rc))
    return None


def variants(stem, work, tools, built):
    if not built:
        return []
    ver = tools.get("dotnet", {}).get("version", "") or "?"
    return [{"key": "csharp", "label": "C# (.NET %s)" % ver, "group": GROUP,
             "cmd": ["dotnet", built["dll"]]}]
