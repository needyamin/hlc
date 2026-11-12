/* Heavy-Load Checker - single page UI. Vanilla JS, no dependencies. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  let CFG = null;
  let PRESETS = [];
  let CONTAINERS = [];
  let RUNTIME_ENVS = [];
  let DOCKER_AVAILABLE = false;
  let PATHS = [];
  let lastLogSeq = 0;
  let rpsHistory = [];
  let activeProfile = "quick";
  let selectedRuns = new Set();
  let lastCampSeq = 0;
  let lastCampaignRunning = false;

  async function api(path, options) {
    const o = Object.assign({ headers: { "Content-Type": "application/json" } },
                            options || {});
    const r = await fetch(path, o);
    const j = await r.json().catch(() => ({ ok: false, error: "bad response" }));
    if (!r.ok || j.ok === false) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }

  function toast(msg, kind) {
    const box = $("verdict-box");
    box.className = "verdict show " + (kind || "warn");
    box.textContent = msg;
  }

  /* ------------------------------------------------------------- config -- */
  function fill(cfg) {
    $("url").value = cfg.target_url || "";
    $("health-path").value = cfg.health_path || "/health";
    const env = (cfg.runtime_environment || "windows").toLowerCase();
    $("rt-windows").checked = env !== "docker";
    $("rt-docker").checked = env === "docker";
    applyRuntimeUI();
    $("max-concurrency").value = cfg.max_concurrency;
    $("request-timeout").value = cfg.request_timeout_s;
    $("keep-alive").checked = !!cfg.keep_alive;
    $("max-anon").value = cfg.guardrails.max_anon_mib;
    $("consecutive-samples").value = cfg.guardrails.consecutive_samples;
    $("min-free").value = cfg.guardrails.min_host_free_gb;
    $("max-error-rate").value = cfg.guardrails.max_error_rate_pct;
    $("max-consecutive-failures").value = cfg.guardrails.max_consecutive_failures;
    $("cooldown-s").value = cfg.cooldown_s;
    $("reprobe-s").value = cfg.reprobe_s;
    $("slo-error").value = cfg.slo.max_error_pct;
    $("slo-p95").value = cfg.slo.max_p95_ms;
    $("slo-p99").value = cfg.slo.max_p99_ms;
    $("slo-rps").value = cfg.slo.min_rps;
    $("slo-growth").value = cfg.slo.max_memory_growth_mib;
    $("slo-retained").value = cfg.slo.max_retained_pct;
    $("slo-health").value = cfg.slo.min_health_success_pct;
    activeProfile = cfg.profile || "quick";
    renderPaths(cfg.paths || [{ path: "/", weight: 1 }]);
    renderSchedule(cfg.phases || []);
    renderChips();
  }

  function num(id, fallback) {
    const v = parseFloat($(id).value);
    return isNaN(v) ? fallback : v;
  }

  function collect() {
    const c = JSON.parse(JSON.stringify(CFG));
    c.target_url = $("url").value.trim();
    c.container = $("container").value;
    c.mysql_container = $("mysql-container").value;
    c.health_path = $("health-path").value.trim() || "/health";
    // The exclusive switch is the ONLY runtime control. Derived keys are
    // never posted: the server preserves advanced overrides when the
    // selector is unchanged and re-applies the coherent preset when it
    // changes. Posting stale derived keys would defeat both behaviours.
    c.runtime_environment = currentRuntimeEnv();
    delete c.mode;
    delete c.benchmark_mode;
    if (c.runtime_comparison) {
      delete c.runtime_comparison.include_container;
      delete c.runtime_comparison.include_windows;
    }
    c.paths = PATHS.filter(p => p.path && p.path.trim())
      .map(p => ({ path: p.path.trim(), weight: Math.max(1, parseInt(p.weight, 10) || 1) }));
    c.max_concurrency = Math.max(1, num("max-concurrency", c.max_concurrency));
    c.request_timeout_s = Math.max(1, num("request-timeout", c.request_timeout_s));
    c.keep_alive = $("keep-alive").checked;
    c.cooldown_s = Math.max(0, num("cooldown-s", c.cooldown_s));
    c.reprobe_s = Math.max(0, num("reprobe-s", c.reprobe_s));
    c.guardrails = {
      max_anon_mib: num("max-anon", c.guardrails.max_anon_mib),
      consecutive_samples: Math.max(1, num("consecutive-samples", c.guardrails.consecutive_samples)),
      min_host_free_gb: num("min-free", c.guardrails.min_host_free_gb),
      max_error_rate_pct: num("max-error-rate", c.guardrails.max_error_rate_pct),
      max_consecutive_failures: Math.max(1, num("max-consecutive-failures", c.guardrails.max_consecutive_failures))
    };
    c.slo = {
      max_error_pct: num("slo-error", c.slo.max_error_pct),
      max_p95_ms: num("slo-p95", c.slo.max_p95_ms),
      max_p99_ms: num("slo-p99", c.slo.max_p99_ms),
      min_rps: num("slo-rps", c.slo.min_rps),
      max_memory_growth_mib: num("slo-growth", c.slo.max_memory_growth_mib),
      max_retained_pct: num("slo-retained", c.slo.max_retained_pct),
      min_health_success_pct: num("slo-health", c.slo.min_health_success_pct)
    };
    return c;
  }

  async function saveConfig() {
    const r = await api("/api/config", {
      method: "POST", body: JSON.stringify({ config: collect() })
    });
    CFG = r.config;
    return r;
  }

  /* -------------------------------------------------- runtime environment -- */
  function currentRuntimeEnv() {
    return $("rt-docker").checked ? "docker" : "windows";
  }

  function runtimeOption(env) {
    return (RUNTIME_ENVS || []).find(x => x.value === env) || {};
  }

  function applyRuntimeUI() {
    const env = currentRuntimeEnv();
    const docker = env === "docker";
    const opt = runtimeOption(env);

    const pill = $("runtime-pill");
    pill.textContent = docker ? "Runtime: Docker (full server)"
                              : "Runtime: Windows (client-only)";
    pill.className = "pill " + (docker ? "ok" : "warn");

    // Derived, read-only. There is deliberately NO selector here: letting a
    // user pick "force Docker cgroup" while Windows is selected would produce
    // a client_only run labelled full_server.
    $("mode").value = docker
      ? ((opt.mode || "auto") + " / " + (opt.benchmark_mode || "auto") +
         " (container cgroup instrumentation)")
      : ((opt.mode || "direct") + " / " + (opt.benchmark_mode || "live_blackbox") +
         " (client-only)");

    // Disable, never hide: the configured container stays visible and is
    // reused when the user switches to Docker.
    ["container", "mysql-container"].forEach(id => { $(id).disabled = !docker; });
    $("container-field").classList.toggle("disabled", !docker);
    $("mysql-container-field").classList.toggle("disabled", !docker);
    $("container-field-note").textContent = docker ? "" : "(Docker only)";

    $("target-hint").innerHTML = docker
      ? "Enter the URL of any website. In Docker runtime mode the container " +
        "is auto-detected from the published port and cgroup memory is " +
        "instrumented."
      : "Enter the URL of any website. In Windows runtime mode the HTTP test " +
        "is <strong>client-only</strong>; no container is contacted.";

    const box = $("runtime-disclosure");
    if (docker) {
      box.className = "disclosure " + (DOCKER_AVAILABLE ? "docker" : "bad");
      box.innerHTML = (DOCKER_AVAILABLE
        ? "<strong>Docker runtime mode &mdash; full server visibility.</strong> "
        : "<strong>Docker is not reachable.</strong> ") +
        "Installed Windows runtimes are still measured as baselines; the " +
        "container adds Spid3r-in-Docker variants plus cgroup CPU, memory, " +
        "PSI and OOM metrics. <em>Docker rows run in a different environment " +
        "than the host baselines, so their ratios are approximate, never an " +
        "apples-to-apples deployment benchmark.</em>";
    } else {
      box.className = "disclosure windows";
      box.innerHTML = "<strong>Windows runtime &mdash; client-only run.</strong> " +
        "Installed host runtimes (PHP, Python, Node, Rust, Spid3r) are " +
        "load-checked. No server CPU, cgroup memory, PSI or OOM metrics are " +
        "collected; memory-recovery SLOs (S5 / S6) are graded N/A. " +
        "<em>This run must never be ranked against a Docker lab run.</em> " +
        "Switch to Docker for full server visibility.";
    }

    $("measured-mode").innerHTML = docker
      ? "<ul><li><strong>Container (Docker only):</strong> cgroup v2 " +
        "<code>memory.stat anon</code> in raw bytes, <code>memory.current</code>, " +
        "CPU from <code>cpu.stat</code>, PSI, OOM kills, PIDs, restarts.</li>" +
        "<li><strong>Recovery (Docker only):</strong> a zero-load cooldown and a " +
        "1&nbsp;Hz availability re-probe separate a retained allocator " +
        "high-water mark from a true unbounded leak.</li></ul>" +
        '<p class="hint">Docker variants are <em>added</em> to the Windows ' +
        "host baselines, not substituted for them.</p>"
      : '<div class="note"><strong>Not measured in Windows mode:</strong> ' +
        "server CPU, cgroup memory, PSI, OOM kills, container restarts, " +
        "memory-recovery SLOs (S5 / S6) and Spid3r-in-Docker variants. " +
        "The report marks these fields <code>unavailable</code>.</div>";
  }

  /* -------------------------------------------------------------- render -- */
  function renderPaths(paths) {
    PATHS = paths.map(p => ({ path: p.path, weight: p.weight || 1 }));
    const box = $("path-rows");
    box.innerHTML = "";
    PATHS.forEach((p, i) => {
      const row = document.createElement("div");
      row.className = "pathrow";
      row.innerHTML =
        '<input class="p" type="text" spellcheck="false" value="' + esc(p.path) + '">' +
        '<input class="w" type="number" min="1" max="1000" title="weight" value="' +
        esc(p.weight) + '"><button class="rm" title="remove">&times;</button>';
      row.querySelector(".p").oninput = (e) => { PATHS[i].path = e.target.value; };
      row.querySelector(".w").oninput = (e) => { PATHS[i].weight = e.target.value; };
      row.querySelector(".rm").onclick = () => {
        PATHS.splice(i, 1);
        if (!PATHS.length) PATHS = [{ path: "/", weight: 1 }];
        renderPaths(PATHS);
      };
      box.appendChild(row);
    });
  }

  function renderChips() {
    const box = $("presets");
    box.innerHTML = "";
    PRESETS.forEach(p => {
      const b = document.createElement("button");
      b.className = "chip" + (p.name === activeProfile ? " active" : "");
      b.textContent = p.label;
      b.onclick = async () => {
        try {
          await saveConfig();
          const r = await api("/api/preset", {
            method: "POST", body: JSON.stringify({ name: p.name })
          });
          CFG = r.config;
          activeProfile = p.name;
          $("max-concurrency").value = CFG.max_concurrency;
          renderChips();
          renderSchedule(CFG.phases);
          toast("Profile switched to " + p.label + " (" + p.schedule_s + " s schedule).", "good");
        } catch (e) { toast(e.message, "bad"); }
      };
      box.appendChild(b);
    });
  }

  function renderSchedule(phases) {
    const box = $("schedule-preview");
    box.innerHTML = "";
    (phases || []).forEach(p => {
      const c = p.steps ? p.steps.join("-") : p.concurrency;
      const d = document.createElement("div");
      d.className = "sc";
      d.innerHTML = "<b>" + esc(p.name) + "</b><span>C=" + esc(c) +
        " &middot; " + esc(p.duration_s) + "s</span>";
      box.appendChild(d);
    });
  }

  function renderContainers(cfgContainer, mysqlContainer) {
    const opts = ['<option value="">Auto-detect by published port</option>'];
    CONTAINERS.forEach(c => {
      opts.push('<option value="' + esc(c.name) + '">' + esc(c.name) +
        "  [" + esc(c.published_ports.join(",") || "no ports") + "]</option>");
    });
    if (cfgContainer && !CONTAINERS.some(c => c.name === cfgContainer)) {
      opts.push('<option value="' + esc(cfgContainer) + '">' +
        esc(cfgContainer) + " (configured)</option>");
    }
    $("container").innerHTML = opts.join("");
    $("container").value = cfgContainer || "";
    const m = ['<option value="">None</option>'].concat(
      CONTAINERS.map(c => '<option value="' + esc(c.name) + '">' +
        esc(c.name) + "</option>"));
    $("mysql-container").innerHTML = m.join("");
    $("mysql-container").value = mysqlContainer || "";
  }

  function renderPreflight(pf) {
    const box = $("preflight-result");
    if (!pf) { box.innerHTML = ""; return; }
    box.innerHTML = pf.checks.map(c =>
      '<div class="pf ' + (c.ok ? "ok" : "bad") + '"><b>' +
      (c.ok ? "OK" : "!") + "</b><div><strong>" + esc(c.name) +
      "</strong><br><span>" + esc(c.detail) + "</span></div></div>").join("");
    if (pf.container && !$("container").value) $("container").value = pf.container;
  }

  function renderRoutes(rows) {
    const box = $("routes-result");
    box.className = "routes show";
    box.innerHTML = rows.map(r =>
      '<div class="rt"><input type="checkbox" ' + (r.include ? "checked" : "") +
      ' data-path="' + esc(r.path) + '"><span class="st" style="color:' +
      (r.include ? "#16a34a" : "#94a3b8") + '">' + r.status + "</span><code>" +
      esc(r.path) + "</code><span>" + r.bytes + "B / " + r.latency_ms +
      'ms</span><button class="add">Add</button></div>').join("");
    box.querySelectorAll(".add").forEach(b => {
      b.onclick = () => {
        const p = b.parentElement.querySelector("input").dataset.path;
        PATHS.push({ path: p, weight: 1 });
        renderPaths(PATHS);
      };
    });
    const btn = document.createElement("button");
    btn.className = "btn ghost small";
    btn.textContent = "Add all checked routes";
    btn.style.margin = "8px";
    btn.onclick = () => {
      const chosen = Array.from(box.querySelectorAll("input:checked"))
        .map(x => x.dataset.path);
      if (!chosen.length) return;
      renderPaths(chosen.map(p => ({ path: p, weight: 1 })));
      toast(chosen.length + " routes added to the workload.", "good");
    };
    box.appendChild(btn);
  }

  function renderReports(rows) {
    const box = $("reports");
    if (!rows || !rows.length) {
      box.innerHTML = '<div class="empty">No runs yet. Start a benchmark above.</div>';
      return;
    }
    const ids = new Set(rows.map(r => r.run_id));
    Array.from(selectedRuns).forEach(id => { if (!ids.has(id)) selectedRuns.delete(id); });
    box.innerHTML = "<table><thead><tr><th class='sel'><input id='selall' type='checkbox'></th>" +
      "<th>Run</th><th>Target</th><th>Profile</th><th>Verdict</th><th>Report</th></tr></thead><tbody>" +
      rows.map(r => {
        const sel = selectedRuns.has(r.run_id) ? "checked" : "";
        return "<tr><td class='sel'><input class='rsel' type='checkbox' data-id='" +
          esc(r.run_id) + "' " + sel + "></td><td>" + esc(r.run_id) +
          "</td><td>" + esc(r.target_url) + "</td><td>" + esc(r.profile) +
          "</td><td>" + esc(r.verdict || r.state) + "</td><td>" + (r.has_report
            ? '<a href="/report/' + esc(r.run_id) + '" target="_blank">HTML</a> ' +
              '<a href="/runs/' + esc(r.run_id) + '/results.json" target="_blank">JSON</a>'
            : esc(r.state)) + "</td></tr>";
      }).join("") + "</tbody></table>";
    const selall = $("selall");
    if (selall) {
      selall.onchange = () => {
        const checked = selall.checked;
        box.querySelectorAll(".rsel").forEach(c => { c.checked = checked; });
        box.querySelectorAll(".rsel").forEach(c => {
          const id = c.dataset.id;
          if (checked) selectedRuns.add(id); else selectedRuns.delete(id);
        });
        updateCompareCount();
      };
      box.querySelectorAll(".rsel").forEach(c => {
        c.onchange = () => {
          if (c.checked) selectedRuns.add(c.dataset.id);
          else selectedRuns.delete(c.dataset.id);
          updateCompareCount();
        };
      });
    }
    updateCompareCount();
  }

  function updateCompareCount() {
    const el = $("compare-count");
    if (el) el.textContent = selectedRuns.size + " selected";
  }

  async function loadCampaigns() {
    try {
      const r = await api("/api/campaign/list");
      renderCampaigns(r.campaigns || []);
    } catch (e) { /* transient */ }
  }

  function renderCampaigns(rows) {
    const box = $("campaigns");
    if (!rows || !rows.length) {
      box.innerHTML = '<div class="empty">No campaigns yet. Create ' +
        'benchmarks/lang-runtime.json and press Start.</div>';
      return;
    }
    box.innerHTML = "<table><thead><tr><th>Campaign</th><th>Profile</th>" +
      "<th>Sites</th><th>Comparable</th><th>Report</th></tr></thead><tbody>" +
      rows.map(c => "<tr><td>" + esc(c.campaign_id) + "</td><td>" + esc(c.profile) +
        "</td><td>" + (c.sites || 0) + "</td><td>" + (c.comparable ? "yes" : "MIXED") +
        '</td><td>' + (c.has_report
          ? '<a href="/campaign/' + esc(c.campaign_id) + '/report.html" target="_blank">HTML</a>'
          : "—") + "</td></tr>").join("") + "</tbody></table>";
  }

  function updateCampaign(s) {
    const c = (s.campaign) || {};
    const running = !!c.running;
    $("btn-campaign-start").disabled = running;
    $("btn-campaign-stop").disabled = !running;
    const busy = s.state === "running" || s.state === "starting";
    $("btn-start").disabled = busy || running;
    if (running) {
      $("state-pill").textContent = "campaign";
      $("state-pill").className = "pill warn";
      $("campaign-progress").innerHTML = "<span>" + esc(c.title || c.campaign_id) +
        "</span><span>site " + (c.sites_done || 0) + "/" + (c.sites_total || 0) +
        (c.current_site ? " — " + esc(c.current_site) : "") +
        " · run " + esc(c.current_run || "") + "</span>";
    } else if (s.state === "idle") {
      $("campaign-progress").innerHTML = "";
    }
    if (c.last_error && !running) {
      toast("Campaign error: " + c.last_error, "bad");
    }
  }

  function renderKpis(live) {
    if (!live || !Object.keys(live).length) {
      $("live-kpis").innerHTML = "";
      return;
    }
    const sm = live.sample || {};
    const items = [
      ["Requests", (live.requests || 0).toLocaleString(), "total GETs"],
      ["Throughput", live.rps || 0, "req / s"],
      ["Error rate", (live.error_pct || 0) + "%", live.errors + " failures"],
      ["Active C", live.active_concurrency || 0, "worker threads"],
      ["Anon mem", sm.anon_mib != null ? Number(sm.anon_mib).toFixed(0) + " MiB" : "n/a", "cgroup raw"],
      ["CPU", sm.cpu_pct != null ? Number(sm.cpu_pct).toFixed(0) + "%" : "n/a", "one-core norm."]
    ];
    $("live-kpis").innerHTML = items.map(x =>
      '<div class="kpi"><div class="l">' + x[0] + '</div><div class="v">' +
      esc(x[1]) + '</div><div class="s">' + esc(x[2]) + "</div></div>").join("");
  }

  function renderSpark() {
    const svg = $("spark");
    const w = 600, h = 110, pad = 6;
    const data = rpsHistory.length ? rpsHistory : [0];
    const max = Math.max(1, ...data) * 1.15;
    const n = Math.max(2, data.length);
    const pts = data.map((v, i) => {
      const x = pad + (i / (n - 1)) * (w - 2 * pad);
      const y = h - pad - (v / max) * (h - 2 * pad);
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    svg.innerHTML =
      '<line x1="0" y1="' + (h - pad) + '" x2="' + w + '" y2="' + (h - pad) +
      '" stroke="#e3e8f0"/>' +
      '<polyline points="' + pts.join(" ") +
      '" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linejoin="round"/>' +
      '<text x="8" y="16" font-size="11" fill="#68758b">peak ' +
      Math.max(...data).toFixed(1) + " req/s</text>";
  }

  function appendLog(rows) {
    if (!rows || !rows.length) return;
    const box = $("log");
    rows.forEach(r => {
      const d = document.createElement("div");
      if (r.level === "error") d.className = "error";
      else if (r.level === "warn") d.className = "warn";
      d.innerHTML = '<span class="t">' + esc((r.ts || "").slice(11, 19)) +
        "</span> " + esc(r.msg);
      box.appendChild(d);
    });
    while (box.childElementCount > 400) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  function updateStatus(s) {
    const pill = $("state-pill");
    const state = s.state || "idle";
    pill.textContent = state;
    pill.className = "pill " + (state === "complete" ? "ok" :
      state === "failed" ? "bad" : state === "running" || state === "starting" ?
        "warn" : "gray");
    const busy = state === "running" || state === "starting";
    $("btn-start").disabled = busy;
    $("btn-stop").disabled = !busy;

    const live = s.live || {};
    if (busy || state === "complete") {
      $("progress-bar").style.width = (live.progress_pct || 0) + "%";
      $("run-phase").textContent = live.phase
        ? "Phase " + live.phase_no + "/" + live.phase_total + " — " + live.phase
        : (state === "starting" ? "Starting ..." : "Finishing ...");
      $("run-time").textContent = (live.elapsed_s || 0) + " / " +
        (live.schedule_s || 0) + " s";
      renderKpis(live);
      if (busy && live.rps != null) {
        rpsHistory.push(live.rps);
        if (rpsHistory.length > 120) rpsHistory.shift();
        renderSpark();
      }
    }
    if (state === "failed") {
      toast("Run failed: " + (s.last_error || "see console"), "bad");
    }
    const res = s.last_result;
    if (state === "complete" && res) {
      const good = /^GO$/.test(res.verdict);
      const warn = /CONDITIONS/.test(res.verdict);
      toast("Verdict: " + res.verdict + "  ·  Health " + res.health_score +
        "/100", good ? "good" : warn ? "warn" : "bad");
      $("report-link").innerHTML =
        '<a href="/report/' + esc(res.run_id) + '" target="_blank">Open HTML report &#8599;</a>' +
        (res.runtime_comparison
          ? '<a href="/runs/' + esc(res.run_id) + '/runtime_comparison.html" target="_blank">runtime_comparison.html &#8599;</a>' +
            '<a href="/runs/' + esc(res.run_id) + '/runtime_comparison.json" target="_blank">runtime_comparison.json &#8599;</a>'
          : '') +
        '<a href="/runs/' + esc(res.run_id) + '/results.json" target="_blank">results.json &#8599;</a>' +
        '<a href="/runs/' + esc(res.run_id) + '/raw.jsonl" target="_blank">raw.jsonl &#8599;</a>';
    }
  }

  /* --------------------------------------------------------------- events -- */
  function bind() {
    ["rt-windows", "rt-docker"].forEach(id => {
      $(id).onchange = async () => {
        applyRuntimeUI();
        try {
          const r = await saveConfig();
          CFG = r.config;
          fill(CFG);               // re-sync derived values from the server
          if (CFG.runtime_environment === "docker") {
            try {
              const cr = await api("/api/containers");
              CONTAINERS = cr.containers || [];
              renderContainers(CFG.container, CFG.mysql_container);
            } catch (e) { /* transient */ }
          }
          toast("Runtime environment: " +
            (CFG.runtime_environment === "docker"
              ? "Docker (full server metrics, host baselines kept)."
              : "Windows (installed runtimes, client-only)."), "good");
        } catch (e) {
          toast(e.message, "bad");
          fill(CFG);               // roll the radios back on failure
        }
      };
    });
    $("btn-add-path").onclick = () => {
      PATHS.push({ path: "/", weight: 1 });
      renderPaths(PATHS);
    };
    $("btn-containers").onclick = async () => {
      try {
        const r = await api("/api/containers");
        CONTAINERS = r.containers || [];
        renderContainers($("container").value, $("mysql-container").value);
        toast("Found " + CONTAINERS.length + " running containers.", "good");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("btn-preflight").onclick = async () => {
      try {
        toast("Running read-only preflight checks ...", "warn");
        await saveConfig();
        const r = await api("/api/preflight", { method: "POST", body: "{}" });
        renderPreflight(r.preflight);
        const bad = r.preflight.checks.filter(c => !c.ok).length;
        toast("Preflight complete: " + (r.preflight.checks.length - bad) +
          " OK, " + bad + " warnings.", bad ? "warn" : "good");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("btn-routes").onclick = async () => {
      try {
        toast("Probing common routes on the target ...", "warn");
        await saveConfig();
        const r = await api("/api/routes", { method: "POST", body: "{}" });
        renderRoutes(r.routes || []);
        toast((r.routes || []).filter(x => x.include).length +
          " usable routes found. Add them with the buttons.", "good");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("btn-start").onclick = async () => {
      try {
        rpsHistory = [];
        renderSpark();
        $("report-link").innerHTML = "";
        await saveConfig();
        const r = await api("/api/run/start", { method: "POST", body: "{}" });
        if (!r.ok) throw new Error(r.message);
        toast("Benchmark started. Live telemetry below.", "warn");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("btn-stop").onclick = async () => {
      try {
        const r = await api("/api/run/stop", { method: "POST", body: "{}" });
        toast(r.ok ? "Stop requested; finishing safely and observing cooldown."
          : r.message, "warn");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("btn-refresh-reports").onclick = loadReports;
    $("btn-compare").onclick = () => {
      const ids = Array.from(selectedRuns);
      if (ids.length < 2) {
        toast("Select at least two runs to compare.", "warn");
        return;
      }
      window.open("/api/compare/report?ids=" + encodeURIComponent(ids.join(",")),
        "_blank");
    };
    $("btn-export-csv").onclick = () => {
      const ids = Array.from(selectedRuns);
      if (!ids.length) { toast("Select runs to export.", "warn"); return; }
      window.open("/api/export?ids=" + encodeURIComponent(ids.join(",")) +
        "&format=csv", "_blank");
    };
    $("btn-campaign-start").onclick = async () => {
      try {
        const path = $("campaign-path").value.trim();
        if (!path) { toast("Enter a campaign file path.", "warn"); return; }
        const r = await api("/api/campaign/start", {
          method: "POST", body: JSON.stringify({ path })
        });
        if (!r.ok) throw new Error(r.message);
        toast("Campaign queued. Progress shown below.", "warn");
      } catch (e) { toast(e.message, "bad"); }
    };
    $("btn-campaign-stop").onclick = async () => {
      try {
        const r = await api("/api/run/stop", { method: "POST", body: "{}" });
        toast(r.ok ? "Stop sent to the current measurement."
          : r.message, "warn");
      } catch (e) { toast(e.message, "bad"); }
    };
  }

  async function loadReports() {
    try {
      const r = await api("/api/reports");
      renderReports(r.reports || []);
    } catch (e) { /* transient */ }
  }

  async function poll() {
    try {
      const s = await api("/api/status");
      updateStatus(s);
      updateCampaign(s);
      const l = await api("/api/log?seq=" + lastLogSeq);
      appendLog(l.rows);
      lastLogSeq = l.log_seq || lastLogSeq;
      if (s.state === "complete" || s.state === "failed") await loadReports();
      const c = s.campaign || {};
      if (c.running) {
        const cl = await api("/api/campaign/status?seq=" + lastCampSeq);
        appendLog(cl.log || []);
        lastCampSeq = cl.log_seq || lastCampSeq;
      }
      if (lastCampaignRunning && !c.running) await loadCampaigns();
      lastCampaignRunning = !!c.running;
    } catch (e) { /* server restarting */ }
  }

  async function boot() {
    bind();
    renderSpark();
    try {
      const b = await api("/api/bootstrap");
      CFG = b.config;
      PRESETS = b.presets || [];
      CONTAINERS = b.containers || [];
      RUNTIME_ENVS = b.runtime_environments || [];
      const d = b.docker || {};
      DOCKER_AVAILABLE = !!d.available;
      const dp = $("docker-pill");
      dp.textContent = d.available
        ? "Docker " + d.server_version + " · " + CONTAINERS.length + " containers"
        : "Docker unavailable";
      dp.className = "pill " + (d.available ? "ok" : "warn");
      // Gate the Docker option on daemon availability, but never disable the
      // currently selected value (that would leave an uncheckable active
      // radio); the disclosure carries the warning instead.
      $("rt-docker").disabled = !d.available
        && CFG.runtime_environment !== "docker";
      renderContainers(CFG.container, CFG.mysql_container);
      fill(CFG);
      renderPreflight(b.preflight);
      renderReports(b.reports);
      updateStatus(b.status);
      await loadCampaigns();
      appendLog([{ ts: new Date().toISOString(), level: "info",
                   msg: "Interface ready. Configure a target, run preflight, then start." }]);
    } catch (e) {
      toast("Cannot load configuration: " + e.message, "bad");
    }
    setInterval(poll, 1000);
  }

  boot();
})();
