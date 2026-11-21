"""Per-language workload sources for the cross-language runtime comparison.

One module per language keeps every implementation reviewable next to its
pinned checksum. Every module follows the same contract:

  KEY        registry key used in reports and ratio tables
  LABEL      display prefix ("Python", "Java", ...)
  EXT        source file extension (".py", ".java", ...)
  GROUP      report colour group: "baseline" | "reference" | "spid3r"
  TOOL_KEYS  detection keys that must be available for this language to run
  NOTE       optional honesty note (e.g. why workload B is not applicable)
  SOURCES    {workload_id: source text} - only workloads this language
             implements with its NATIVE standard library
  available(tools)      -> bool: every TOOL_KEYS entry detected
  filename(stem)        -> source file name (Java files must match the class)
  build(...)            -> optional compile step, returns artifacts dict
  variants(...)         -> list of {key, label, group, cmd} run variants

Workload B (JSON round-trip) exists ONLY for languages whose standard library
ships a JSON serializer; the absent entries are documented in each NOTE and
reported as "not applicable" - a hand-rolled parser would not be the native
serializer this workload measures, so it is never simulated.
"""

from . import lang_spid3r, lang_python, lang_javascript, lang_typescript
from . import lang_php, lang_ruby, lang_java, lang_kotlin, lang_c, lang_cpp
from . import lang_csharp, lang_go, lang_rust, lang_swift

# Report order: Spid3r (subject) first, then interpreted baselines, then
# compiled references. The order also drives the ratio-matrix column order.
LANG_MODULES = [
    lang_spid3r,
    lang_python,
    lang_javascript,
    lang_typescript,
    lang_php,
    lang_ruby,
    lang_java,
    lang_kotlin,
    lang_go,
    lang_csharp,
    lang_swift,
    lang_rust,
    lang_c,
    lang_cpp,
]


def module_by_key(key):
    for m in LANG_MODULES:
        if m.KEY == key:
            return m
    return None
