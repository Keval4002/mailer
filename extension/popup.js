document.addEventListener("DOMContentLoaded", () => {

  /* ════════════════════════════════════════════
     TEMPLATES
     3 battle-tested cold outreach messages.
     Variables: {first_name} {name} {role} {company} {title}
  ════════════════════════════════════════════ */

  const TEMPLATES = [
    {
      id:    "referral",
      label: "Referral Request",
      desc:  "Subtle request for a referral after applying",
      body:
`Hi {first_name},

I applied for the {role} role at {company} and wanted to reach out. I'm genuinely excited about your team's work.

I'd really appreciate any guidance or a potential referral. Happy to share my resume if it helps.

Thanks for your time.`,
    },
    {
      id:    "shortlisting",
      label: "Interview Shortlisting",
      desc:  "Highlight a key skill for current openings",
      body:
`Hi {first_name},

I saw the {role} opening at {company}. Given my background in software development and agentic AI, I believe I could contribute meaningfully.

I'd love to be considered for an interview if there's a mutual fit. Open to a brief chat?

Thanks!`,
    },
    {
      id:    "future",
      label: "Future Consideration",
      desc:  "Keep in touch for future roles",
      body:
`Hi {first_name},

I've been following {company} and am really impressed by what the team is building. I wanted to connect and stay on your radar for any future {role} opportunities.

Thanks for connecting, and I look forward to keeping in touch.`,
    },
    {
      id:    "alumni",
      label: "Alumni Connection",
      desc:  "Reach out to fellow TIET alumni",
      body:
`Hi {first_name},

Great to see a fellow alum from Thapar Institute of Engineering and Technology! I recently applied for the {role} role at {company} and would love to connect.

If you have a moment, I'd appreciate any guidance or a referral.

Go TIET!`,
    },
  ];


  let _activeTemplateId = TEMPLATES[0].id;

  /* ════════════════════════════════════════════
     TAB SYSTEM
  ════════════════════════════════════════════ */

  const tabBtns  = document.querySelectorAll(".tab-btn");
  const views    = document.querySelectorAll(".view");

  function switchTab(targetViewId) {
    views.forEach(v    => v.classList.toggle("hidden", v.id !== targetViewId));
    tabBtns.forEach(b  => b.classList.toggle("active", b.dataset.tab === targetViewId));
    if (targetViewId === "message-view") initMessageTab();
    if (targetViewId === "settings-view") loadSettingsView();
  }

  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  /* ════════════════════════════════════════════
     RECORD TAB — DOM REFS
  ════════════════════════════════════════════ */

  const submitBtn     = document.getElementById("submit-btn");
  const statusMsg     = document.getElementById("status-msg");
  const detectedBadge = document.getElementById("detected-badge");
  const detectedText  = document.getElementById("detected-text");
  const refreshBtn    = document.getElementById("refresh-scrape-btn");

  const urlInput     = document.getElementById("job-url");
  const boardInput   = document.getElementById("job-board");
  const companyInput = document.getElementById("company-name");
  const roleInput    = document.getElementById("role");
  const locationInput= document.getElementById("location");
  const salaryInput  = document.getElementById("salary");

  let _submitting = false;

  /* ── Field flash on auto-fill ── */
  function flashField(el) {
    if (!el || !el.value) return;
    el.classList.remove("field-flashed");
    void el.offsetWidth;
    el.classList.add("field-flashed");
    el.addEventListener("animationend", () => el.classList.remove("field-flashed"), { once: true });
  }

  /* ── Apply scraped data to form ── */
  function applyScraped(data, source = "") {
    let filled = 0;
    if (data.company  && !companyInput.value)  { companyInput.value  = data.company;   flashField(companyInput);  filled++; }
    if (data.role     && !roleInput.value)     { roleInput.value     = data.role;      flashField(roleInput);     filled++; }
    if (data.location && locationInput && !locationInput.value) { locationInput.value = data.location; flashField(locationInput); filled++; }
    if (data.salary   && salaryInput   && !salaryInput.value)   { salaryInput.value   = data.salary;   flashField(salaryInput);   filled++; }
    if (data.jobBoard && !boardInput.value)    { boardInput.value    = data.jobBoard; }
    if (filled > 0) showDetectedBadge(source || data.jobBoard || "page", filled);
  }

  function showDetectedBadge(source, count) {
    if (!detectedBadge) return;
    detectedBadge.classList.remove("hidden");
    if (detectedText) {
      detectedText.textContent = `Auto-filled ${count} field${count !== 1 ? "s" : ""} from ${source}`;
    }
  }

  /* ════════════════════════════════════════════
     RECORD TAB — INIT FLOW
  ════════════════════════════════════════════ */

  async function initRecordTab() {
    const stored = await getStorage(["apiUrl", "apiToken", "scraped", "contextCompany", "contextRole", "contextBoard"]);

    if (!stored.apiToken) { switchTab("settings-view"); return; }

    const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
    const tab  = tabs[0];
    if (tab?.url) urlInput.value = tab.url;

    // Context menu overrides (highest priority)
    if (stored.contextCompany) { companyInput.value = stored.contextCompany; chrome.storage.local.remove("contextCompany"); flashField(companyInput); }
    if (stored.contextRole)    { roleInput.value    = stored.contextRole;    chrome.storage.local.remove("contextRole");    flashField(roleInput);    }
    if (stored.contextBoard)   { boardInput.value   = stored.contextBoard;   chrome.storage.local.remove("contextBoard");   }

    // Proactively scraped data
    if (stored.scraped && isScrapeFresh(stored.scraped, tab?.url)) {
      applyScraped(stored.scraped, stored.scraped.jobBoard || "page");
    } else {
      tryContentScriptScrape(tab);
    }
  }

  function isScrapeFresh(scraped, currentUrl) {
    if (!scraped) return false;
    const ageMs   = Date.now() - (scraped.scrapedAt || 0);
    const sameUrl = !currentUrl || scraped.url === currentUrl || normaliseUrl(scraped.url) === normaliseUrl(currentUrl);
    return ageMs < 30_000 && sameUrl;
  }

  function normaliseUrl(url = "") {
    try { const u = new URL(url); return u.hostname + u.pathname; } catch { return url; }
  }

  function tryContentScriptScrape(tab) {
    if (!tab?.id) return;
    chrome.tabs.sendMessage(tab.id, { action: "scrape_job" }, (response) => {
      if (chrome.runtime.lastError || !response) {
        chrome.runtime.sendMessage({ action: "inject_and_scrape" }, (injected) => {
          if (injected && (injected.company || injected.role)) {
            applyScraped(injected, injected.jobBoard || "page (injected)");
          } else {
            applyHeuristics(tab);
          }
        });
      } else {
        applyScraped(response, response.jobBoard || "page");
      }
    });
  }

  function applyHeuristics(tab) {
    const url   = tab?.url   || "";
    const title = tab?.title || "";
    const board = detectBoard(url);
    if (board && !boardInput.value) boardInput.value = board;
    const sep   = /\s*[|\-–—]\s*/;
    const parts = title.split(sep).map(p => p.trim()).filter(Boolean);
    if (parts.length >= 2) {
      if (!roleInput.value)    roleInput.value    = parts[0];
      if (!companyInput.value) companyInput.value = parts[parts.length - 1];
    }
  }

  function detectBoard(url) {
    if (url.includes("linkedin.com"))         return "LinkedIn";
    if (url.includes("indeed.com"))           return "Indeed";
    if (url.includes("lever.co"))             return "Lever";
    if (url.includes("greenhouse.io"))        return "Greenhouse";
    if (url.includes("workday.com") || url.includes("myworkdayjobs.com")) return "Workday";
    if (url.includes("wellfound.com"))        return "Wellfound";
    if (url.includes("ashbyhq.com"))          return "Ashby";
    if (url.includes("smartrecruiters.com"))  return "SmartRecruiters";
    if (url.includes("naukri.com"))           return "Naukri";
    if (url.includes("internshala.com"))      return "Internshala";
    if (url.includes("jobvite.com"))          return "Jobvite";
    if (url.includes("icims.com"))            return "iCIMS";
    if (url.includes("taleo.net"))            return "Taleo";
    return "";
  }

  /* ── Refresh button ── */
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.classList.add("spinning");
      await chrome.storage.local.remove("scraped");
      const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
      tryContentScriptScrape(tabs[0]);
      setTimeout(() => refreshBtn.classList.remove("spinning"), 1500);
    });
  }

  /* ── Storage change listener ── */
  chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace !== "local") return;
    if (changes.scraped?.newValue) {
      const data = changes.scraped.newValue;
      applyScraped(data, data.jobBoard || "page");
    }
    if (changes.contextCompany?.newValue) { companyInput.value = changes.contextCompany.newValue; flashField(companyInput); chrome.storage.local.remove("contextCompany"); }
    if (changes.contextRole?.newValue)    { roleInput.value    = changes.contextRole.newValue;    flashField(roleInput);    chrome.storage.local.remove("contextRole");    }
    if (changes.contextBoard?.newValue)   { boardInput.value   = changes.contextBoard.newValue;   chrome.storage.local.remove("contextBoard"); }
    // Recipient arrived from content script while popup is open
    if (changes.linkedinRecipient?.newValue) {
      const msgView = document.getElementById("message-view");
      if (msgView && !msgView.classList.contains("hidden")) {
        const r = changes.linkedinRecipient.newValue;
        _currentRecipient = r;
        renderRecipient(r);
        // Kick off DB lookup now that we have the company name
        if (r.company && r.company !== (_currentApp?.company_name || "")) {
          lookupApplication(r.company);
        } else {
          buildPreview();
        }
      }
    }
  });

  /* ── Submit ── */
  submitBtn.addEventListener("click", async () => {
    if (_submitting) return;
    const company = companyInput.value.trim();
    if (!company) { showMessage("Company Name is required.", "error"); companyInput.focus(); return; }

    _submitting = true;
    setSubmitLoading(true);

    let stored;
    try {
      stored = await getStorage(["apiUrl", "apiToken"]);
    } catch {
      showMessage("Storage error. Try again.", "error");
      _submitting = false; setSubmitLoading(false); return;
    }

    const apiUrl   = stored.apiUrl   || "http://127.0.0.1:8000";
    const apiToken = stored.apiToken;
    if (!apiToken) { showMessage("API Token missing — check Settings.", "error"); _submitting = false; setSubmitLoading(false); return; }

    const payload = {
      company_name: company,
      role:      roleInput.value.trim()                          || null,
      job_url:   urlInput.value.trim()                           || null,
      job_board: boardInput.value.trim()                         || null,
      status:    document.getElementById("status").value,
      notes:     document.getElementById("notes").value.trim()   || null,
    };

    try {
      const response = await fetch(`${apiUrl}/api/applications`, {
        method:  "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${apiToken}` },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        showMessage("Application recorded! \u2713", "success");
        roleInput.value = ""; urlInput.value = "";
        document.getElementById("notes").value  = "";
        document.getElementById("status").value = "Applied";
        if (locationInput) locationInput.value = "";
        if (salaryInput)   salaryInput.value   = "";
        chrome.storage.local.remove("scraped");
      } else {
        let errMsg = "Failed to record.";
        try { const e = await response.json(); errMsg = e.detail || e.message || errMsg; } catch (_) {}
        showMessage(errMsg, "error");
      }
    } catch (err) {
      const msg = err?.message?.includes("Failed to fetch")
        ? "Network error. Is the backend running?"
        : (err?.message || "Unknown error.");
      showMessage(msg, "error");
    } finally {
      _submitting = false; setSubmitLoading(false);
    }
  });

  /* ════════════════════════════════════════════
     MESSAGE ASSISTANT TAB
  ════════════════════════════════════════════ */

  let _currentRecipient = null;
  let _currentApp       = null;
  let _msgTabInitialized = false;


  async function initMessageTab() {
    if (_msgTabInitialized) { buildPreview(); return; }
    _msgTabInitialized = true;

    // Load saved state
    const stored = await getStorage(["linkedinMsgTemplate", "linkedinActiveTemplate", "linkedinRecipient", "apiUrl", "apiToken"]);

    // Restore active template id (default to first)
    _activeTemplateId = stored.linkedinActiveTemplate || TEMPLATES[0].id;

    // Build the template picker pills
    renderTemplatePicker();

    // Load custom body if user edited it, else the preset body
    const activePreset  = TEMPLATES.find(t => t.id === _activeTemplateId) || TEMPLATES[0];
    const editorContent = stored.linkedinMsgTemplate || activePreset.body;
    document.getElementById("msg-template-editor").value = editorContent;

    // Live preview update
    document.getElementById("msg-template-editor").addEventListener("input", buildPreview);

    // Recipient
    if (stored.linkedinRecipient && isRecipientFresh(stored.linkedinRecipient)) {
      _currentRecipient = stored.linkedinRecipient;
      renderRecipient(_currentRecipient);
      await lookupApplication(_currentRecipient.company);
    } else {
      // Actively ask content script if no fresh recipient in storage
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]?.id) {
          chrome.tabs.sendMessage(tabs[0].id, { action: "get_linkedin_recipient" }, async (response) => {
            if (chrome.runtime.lastError || !response?.recipient) {
              showNoRecipient();
            } else {
              _currentRecipient = response.recipient;
              renderRecipient(_currentRecipient);
              await lookupApplication(_currentRecipient.company);
            }
          });
        } else {
          showNoRecipient();
        }
      });
    }

    buildPreview();

    // {vars} hint popover
    const hintBtn     = document.getElementById("template-vars-hint");
    const hintPopover = document.getElementById("template-vars-popover");
    hintBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      hintPopover.classList.toggle("hidden");
    });
    document.addEventListener("click", () => hintPopover.classList.add("hidden"));

    document.getElementById("paste-msg-btn").addEventListener("click", pasteMessage);
    document.getElementById("copy-msg-btn").addEventListener("click", copyMessage);
    document.getElementById("retry-fetch-btn")?.addEventListener("click", retryFetchRecipient);
  }

  function retryFetchRecipient() {
    const btn = document.getElementById("retry-fetch-btn");
    if (btn) btn.innerHTML = "Fetching...";
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]?.id) {
        chrome.tabs.sendMessage(tabs[0].id, { action: "get_linkedin_recipient" }, async (response) => {
          if (btn) btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px; height:14px; margin-right:4px;"><path d="M2.5 8a5.5 5.5 0 1 1 1.61 3.89L2.5 13.5M2.5 8v5.5h5.5"/></svg>Retry Fetch`;
          if (chrome.runtime.lastError || !response?.recipient) {
            showNoRecipient();
          } else {
            _currentRecipient = response.recipient;
            renderRecipient(_currentRecipient);
            await lookupApplication(_currentRecipient.company);
          }
        });
      } else {
        if (btn) btn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:14px; height:14px; margin-right:4px;"><path d="M2.5 8a5.5 5.5 0 1 1 1.61 3.89L2.5 13.5M2.5 8v5.5h5.5"/></svg>Retry Fetch`;
      }
    });
  }

  /** Render 3 pill buttons into #template-picker (created in HTML) */
  function renderTemplatePicker() {
    const container = document.getElementById("template-picker");
    if (!container) return;
    container.innerHTML = "";

    TEMPLATES.forEach(tmpl => {
      const btn = document.createElement("button");
      btn.className = "tmpl-pill" + (tmpl.id === _activeTemplateId ? " active" : "");
      btn.dataset.id = tmpl.id;
      btn.title = tmpl.desc;
      btn.textContent = tmpl.label;
      btn.addEventListener("click", () => selectTemplate(tmpl.id));
      container.appendChild(btn);
    });
  }

  function selectTemplate(id) {
    const tmpl = TEMPLATES.find(t => t.id === id);
    if (!tmpl) return;
    _activeTemplateId = id;

    // Update pill active state
    document.querySelectorAll(".tmpl-pill").forEach(p =>
      p.classList.toggle("active", p.dataset.id === id)
    );

    // Fill editor with this template body
    const editor = document.getElementById("msg-template-editor");
    if (editor) {
      editor.value = tmpl.body;
      editor.dispatchEvent(new Event("input"));
    }

    // Persist selection
    chrome.storage.local.set({ linkedinActiveTemplate: id, linkedinMsgTemplate: tmpl.body });
  }

  function isRecipientFresh(r) {
    return r && (Date.now() - (r.detectedAt || 0)) < 120_000; // 2 min
  }

  function showNoRecipient() {
    document.getElementById("recipient-card").classList.add("hidden");
    document.getElementById("no-recipient").classList.remove("hidden");
    document.getElementById("matched-app-badge").classList.add("hidden");
    document.getElementById("no-app-badge").classList.add("hidden");
    document.getElementById("paste-msg-btn").disabled = false; // allow copy/paste without recipient
  }

  function renderRecipient(r) {
    document.getElementById("no-recipient").classList.add("hidden");
    const card = document.getElementById("recipient-card");
    card.classList.remove("hidden");

    document.getElementById("recipient-name").textContent    = r.name    || "—";
    document.getElementById("recipient-title").textContent   = r.title   || "";
    document.getElementById("recipient-company").textContent = r.company  ? `@ ${r.company}` : "";

    // Avatar initials
    const av = document.getElementById("recipient-avatar");
    const initials = (r.name || "")
      .split(/\s+/).filter(Boolean).slice(0, 2)
      .map(w => w[0].toUpperCase()).join("");
    if (initials) {
      av.textContent = initials;
      av.classList.add("has-initials");
    } else {
      av.classList.remove("has-initials");
      av.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>`;
    }

    // Show tab dot indicator
    document.getElementById("tab-message-dot")?.classList.remove("hidden");
  }

  async function lookupApplication(company) {
    // Show loading state immediately
    showAppLoading();

    if (!company || !company.trim()) {
      // Company not detected yet — wait briefly for content script to push an update,
      // then re-check storage once before giving up
      setTimeout(async () => {
        const fresh = await getStorage(["linkedinRecipient"]);
        const r     = fresh.linkedinRecipient;
        if (r?.company && r.company !== (_currentRecipient?.company || "")) {
          _currentRecipient = { ..._currentRecipient, ...r };
          renderRecipient(_currentRecipient);
          await lookupApplication(r.company);
        } else {
          showNoApp();
          buildPreview();
        }
      }, 2000);
      return;
    }

    try {
      const stored   = await getStorage(["apiUrl", "apiToken"]);
      const apiUrl   = stored.apiUrl   || "http://127.0.0.1:8000";
      const apiToken = stored.apiToken;
      
      if (!apiToken) { 
        console.error("No API Token found in Extension Settings.");
        document.getElementById("msg-status").textContent = "API Token missing! Please configure in Settings.";
        document.getElementById("msg-status").style.display = "block";
        document.getElementById("msg-status").style.color = "#f44336";
        showNoApp(); 
        buildPreview(); 
        return; 
      }

      const res = await fetch(
        `${apiUrl}/api/applications?search=${encodeURIComponent(company)}&sort=newest`,
        { headers: { "Authorization": `Bearer ${apiToken}` } }
      );
      
      if (!res.ok) { 
        console.error("Backend fetch failed. Status:", res.status);
        document.getElementById("msg-status").textContent = `Backend error: ${res.status}`;
        document.getElementById("msg-status").style.display = "block";
        document.getElementById("msg-status").style.color = "#f44336";
        showNoApp(); 
        buildPreview(); 
        return; 
      }

      const data = await res.json();
      const apps = data.applications || [];

      // Prefer exact company name match, then fuzzy (search already filters), then first result
      const exact = apps.find(a =>
        a.company_name.toLowerCase().trim() === company.toLowerCase().trim()
      );
      _currentApp = exact || apps[0] || null;

      if (_currentApp) {
        showMatchedApp(_currentApp);
      } else {
        showNoApp();
      }
    } catch (e) {
      showNoApp();
    }
    buildPreview();
  }

  function showAppLoading() {
    document.getElementById("matched-app-badge").classList.add("hidden");
    const noBadge = document.getElementById("no-app-badge");
    noBadge.classList.remove("hidden");
    const span = noBadge.querySelector("span");
    if (span) span.textContent = "Looking up application...";
  }

  function showMatchedApp(app) {
    document.getElementById("no-app-badge").classList.add("hidden");
    const badge = document.getElementById("matched-app-badge");
    badge.classList.remove("hidden");
    document.getElementById("matched-app-text").textContent =
      `Applied for ${app.role || "—"} \u2022 ${app.status || "—"}`;
  }

  function showNoApp() {
    document.getElementById("matched-app-badge").classList.add("hidden");
    document.getElementById("no-app-badge").classList.remove("hidden");
  }

  function buildPreview() {
    const template = document.getElementById("msg-template-editor")?.value || "";
    const r        = _currentRecipient || {};
    const app      = _currentApp;

    const resolved = template
      .replace(/\{first_name\}/g, r.firstName || r.name?.split(" ")[0] || "{first_name}")
      .replace(/\{name\}/g,       r.name      || "{name}")
      .replace(/\{title\}/g,      r.title     || "{title}")
      .replace(/\{company\}/g,    r.company   || app?.company_name || "{company}")
      .replace(/\{role\}/g,       app?.role   || "{role}");

    const preview = document.getElementById("msg-preview");
    if (!preview) return;
    preview.textContent = resolved;

    // Highlight any still-unresolved placeholders so user notices
    const hasUnresolved = /\{(first_name|name|title|company|role)\}/.test(resolved);
    preview.classList.toggle("has-unresolved", hasUnresolved);

    // Show a small hint below the preview if vars are missing
    const hint = document.getElementById("preview-hint");
    if (hint) {
      if (hasUnresolved) {
        const missing = [];
        if (/\{first_name\}/.test(resolved)) missing.push("recipient name");
        if (/\{company\}/.test(resolved))    missing.push("company");
        if (/\{role\}/.test(resolved))       missing.push("role from DB");
        hint.textContent = `Still resolving: ${missing.join(", ")}`;
        hint.classList.remove("hidden");
      } else {
        hint.classList.add("hidden");
      }
    }
    // Toggle the "All vars resolved" badge
    const resolvedBadge = document.getElementById("preview-resolved-badge");
    if (resolvedBadge) resolvedBadge.classList.toggle("hidden", hasUnresolved);
  }

  async function pasteMessage() {

    const pasteBtn  = document.getElementById("paste-msg-btn");
    const msgStatus = document.getElementById("msg-status");
    const text      = document.getElementById("msg-preview")?.textContent || "";
    if (!text.trim()) { showMsgStatus("Nothing to paste.", "error"); return; }

    // Save template on paste
    const tmpl = document.getElementById("msg-template-editor")?.value || "";
    chrome.storage.local.set({ linkedinMsgTemplate: tmpl });

    setPasteLoading(true);

    const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
    const tab  = tabs[0];
    if (!tab?.id) { showMsgStatus("No active tab.", "error"); setPasteLoading(false); return; }

    chrome.tabs.sendMessage(tab.id, { action: "paste_linkedin_message", text }, async (response) => {
      setPasteLoading(false);
      if (chrome.runtime.lastError || !response?.ok) {
        // Auto-fallback: copy to clipboard so user can just Ctrl+V
        try {
          await navigator.clipboard.writeText(text);
          showMsgStatus("Copied! Click inside the message box, then press Ctrl+V.", "success");
        } catch {
          showMsgStatus("Paste failed — click inside the LinkedIn message box first, then try again.", "error");
        }
      } else {
        showMsgStatus("Pasted into message box! \u2713", "success");
      }
    });
  }

  async function copyMessage() {
    const text = document.getElementById("msg-preview")?.textContent || "";
    if (!text.trim()) { showMsgStatus("Nothing to copy.", "error"); return; }
    try {
      await navigator.clipboard.writeText(text);
      // Save template
      const tmpl = document.getElementById("msg-template-editor")?.value || "";
      chrome.storage.local.set({ linkedinMsgTemplate: tmpl });
      showMsgStatus("Copied to clipboard! \u2713", "success");
    } catch {
      showMsgStatus("Clipboard access denied.", "error");
    }
  }

  function showMsgStatus(msg, type) {
    const el = document.getElementById("msg-status");
    if (!el) return;
    el.textContent = msg;
    el.className   = "status-msg " + type;
    setTimeout(() => { el.textContent = ""; el.className = "status-msg"; }, 4000);
  }

  function setPasteLoading(loading) {
    const btn     = document.getElementById("paste-msg-btn");
    const label   = btn?.querySelector(".btn-label");
    const spinner = btn?.querySelector(".btn-spinner");
    if (!btn) return;
    btn.disabled = loading;
    btn.classList.toggle("is-loading", loading);
    if (loading) {
      spinner?.classList.remove("hidden");
      if (label) label.textContent = "Pasting\u2026";
    } else {
      spinner?.classList.add("hidden");
      if (label) label.textContent = "Paste into LinkedIn";
    }
  }

  /* ════════════════════════════════════════════
     SETTINGS TAB
  ════════════════════════════════════════════ */

  async function loadSettingsView() {
    const stored = await getStorage(["apiUrl", "apiToken", "linkedinActiveTemplate"]);
    document.getElementById("api-url").value   = stored.apiUrl   || "http://127.0.0.1:8000";
    document.getElementById("api-token").value  = stored.apiToken || "";

    // Render template reference cards in settings
    const refContainer = document.getElementById("settings-template-list");
    if (refContainer) {
      const activeId = stored.linkedinActiveTemplate || TEMPLATES[0].id;
      refContainer.innerHTML = TEMPLATES.map(t => `
        <div class="settings-tmpl-card ${t.id === activeId ? "is-active" : ""}" data-id="${t.id}">
          <div class="settings-tmpl-label">${t.label}${t.id === activeId ? ' <span class="active-pip">Active</span>' : ""}</div>
          <div class="settings-tmpl-desc">${t.desc}</div>
        </div>
      `).join("");
      // Clicking a card activates that template
      refContainer.querySelectorAll(".settings-tmpl-card").forEach(card => {
        card.addEventListener("click", () => {
          selectTemplate(card.dataset.id);
          _msgTabInitialized = false;
          // Update active pip UI
          refContainer.querySelectorAll(".settings-tmpl-card").forEach(c => {
            const isNow = c.dataset.id === card.dataset.id;
            c.classList.toggle("is-active", isNow);
            const lbl = c.querySelector(".settings-tmpl-label");
            if (lbl) lbl.innerHTML = TEMPLATES.find(t => t.id === c.dataset.id)?.label +
              (isNow ? ' <span class="active-pip">Active</span>' : "");
          });
        });
      });
    }
  }

  document.getElementById("save-settings-btn").addEventListener("click", async () => {
    const apiUrl   = document.getElementById("api-url").value.replace(/\/$/, "");
    const apiToken = document.getElementById("api-token").value;

    chrome.storage.local.set({ apiUrl, apiToken }, () => {
      _msgTabInitialized = false;
      const el = document.getElementById("settings-status");
      if (el) { el.textContent = "Settings saved! \u2713"; el.className = "status-msg success"; setTimeout(() => { el.textContent = ""; el.className = "status-msg"; }, 3000); }
    });
  });

  /* ════════════════════════════════════════════
     SHARED UTILITIES
  ════════════════════════════════════════════ */

  function getStorage(keys) {
    return new Promise(resolve => chrome.storage.local.get(keys, resolve));
  }

  function showMessage(msg, type) {
    statusMsg.textContent = msg;
    statusMsg.className   = "status-msg " + type;
    setTimeout(() => { statusMsg.textContent = ""; statusMsg.className = "status-msg"; }, 4500);
  }

  function setSubmitLoading(loading) {
    const icon    = submitBtn.querySelector(".btn-icon");
    const label   = submitBtn.querySelector(".btn-label");
    const spinner = submitBtn.querySelector(".btn-spinner");
    submitBtn.disabled = loading;
    submitBtn.classList.toggle("is-loading", loading);
    if (loading) {
      icon?.classList.add("hidden");
      spinner?.classList.remove("hidden");
      if (label) label.textContent = "Recording\u2026";
    } else {
      icon?.classList.remove("hidden");
      spinner?.classList.add("hidden");
      if (label) label.textContent = "Record Application";
    }
  }

  /* ════════════════════════════════════════════
     BOOT
  ════════════════════════════════════════════ */

  async function boot() {
    // Load API token — if missing, go to settings first
    const stored = await getStorage(["apiToken"]);
    if (!stored.apiToken) {
      switchTab("settings-view");
      return;
    }

    // Check if we're on a LinkedIn page — switch to message tab automatically
    const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
    const tab  = tabs[0];
    const url  = tab?.url || "";
    const isLinkedInMessaging = url.includes("linkedin.com/messaging") ||
                                url.includes("linkedin.com/in/") ||
                                url.includes("linkedin.com/mynetwork");

    if (isLinkedInMessaging) {
      // Check if we have a fresh recipient already
      const s = await getStorage(["linkedinRecipient"]);
      if (s.linkedinRecipient && isRecipientFresh(s.linkedinRecipient)) {
        _currentRecipient = s.linkedinRecipient;
      }
      switchTab("message-view");
    } else {
      // Default: show record tab
      await initRecordTab();
    }
  }

  boot();
});
