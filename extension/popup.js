document.addEventListener("DOMContentLoaded", () => {
  /* ── DOM refs ── */
  const mainView      = document.getElementById("main-view");
  const settingsView  = document.getElementById("settings-view");
  const goSettingsBtn = document.getElementById("go-settings-btn");
  const backMainBtn   = document.getElementById("back-to-main-btn");
  const saveSettingsBtn = document.getElementById("save-settings-btn");
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

  /* ── Submit state guard ── */
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

    if (data.company && !companyInput.value) { companyInput.value = data.company; flashField(companyInput); filled++; }
    if (data.role    && !roleInput.value)    { roleInput.value    = data.role;    flashField(roleInput);    filled++; }
    if (data.location && locationInput && !locationInput.value) { locationInput.value = data.location; flashField(locationInput); filled++; }
    if (data.salary  && salaryInput && !salaryInput.value)      { salaryInput.value   = data.salary;   flashField(salaryInput);   filled++; }
    if (data.jobBoard && !boardInput.value)  { boardInput.value   = data.jobBoard; }

    if (filled > 0) {
      showDetectedBadge(source || data.jobBoard || "page", filled);
    }
  }

  function showDetectedBadge(source, count) {
    if (!detectedBadge) return;
    detectedBadge.classList.remove("hidden");
    if (detectedText) {
      detectedText.textContent = `Auto-filled ${count} field${count !== 1 ? "s" : ""} from ${source}`;
    }
  }

  /* ── Main init flow ── */
  async function init() {
    // 1. Load settings
    const stored = await getStorage(["apiUrl", "apiToken", "scraped", "contextCompany", "contextRole", "contextBoard"]);

    document.getElementById("api-url").value   = stored.apiUrl   || "http://127.0.0.1:8000";
    document.getElementById("api-token").value = stored.apiToken || "";

    if (!stored.apiToken) { showSettings(); return; }

    // 2. Get current tab URL — fill job URL immediately
    const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
    const tab  = tabs[0];
    if (tab?.url) {
      urlInput.value = tab.url;
    }

    // 3. Apply context menu overrides (highest priority — user explicitly selected)
    const ctxFilled = {};
    if (stored.contextCompany) { companyInput.value = stored.contextCompany; ctxFilled.company = true; chrome.storage.local.remove("contextCompany"); flashField(companyInput); }
    if (stored.contextRole)    { roleInput.value    = stored.contextRole;    ctxFilled.role    = true; chrome.storage.local.remove("contextRole");    flashField(roleInput);    }
    if (stored.contextBoard)   { boardInput.value   = stored.contextBoard;   ctxFilled.board   = true; chrome.storage.local.remove("contextBoard");   }

    // 4. Apply proactively scraped data (content script pushed this before popup opened)
    if (stored.scraped && isScrapeFresh(stored.scraped, tab?.url)) {
      applyScraped(stored.scraped, stored.scraped.jobBoard || "page");
    } else {
      // 5. Fallback: try sending message to content script (already running in tab)
      tryContentScriptScrape(tab);
    }
  }

  function isScrapeFresh(scraped, currentUrl) {
    if (!scraped) return false;
    const ageMs = Date.now() - (scraped.scrapedAt || 0);
    const sameUrl = !currentUrl || scraped.url === currentUrl ||
      normaliseUrl(scraped.url) === normaliseUrl(currentUrl);
    return ageMs < 30_000 && sameUrl; // fresh within 30s and same URL
  }

  function normaliseUrl(url = "") {
    try {
      const u = new URL(url);
      return u.hostname + u.pathname;
    } catch { return url; }
  }

  function tryContentScriptScrape(tab) {
    if (!tab?.id) return;
    chrome.tabs.sendMessage(tab.id, { action: "scrape_job" }, (response) => {
      if (chrome.runtime.lastError || !response) {
        // Content script not injected — ask background to inject it
        chrome.runtime.sendMessage({ action: "inject_and_scrape" }, (injected) => {
          if (injected && (injected.company || injected.role)) {
            applyScraped(injected, injected.jobBoard || "page (injected)");
          } else {
            // Last resort: URL + title heuristics
            applyHeuristics(tab);
          }
        });
      } else {
        applyScraped(response, response.jobBoard || "page");
      }
    });
  }

  function applyHeuristics(tab) {
    const url   = tab?.url  || "";
    const title = tab?.title || "";

    // Detect board from URL
    const board = detectBoard(url);
    if (board && !boardInput.value) boardInput.value = board;

    // Try to extract company + role from page title
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

  /* ── Manual refresh button ── */
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.classList.add("spinning");
      // Clear current scraped cache and re-scrape
      await chrome.storage.local.remove("scraped");
      const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
      tryContentScriptScrape(tabs[0]);
      setTimeout(() => refreshBtn.classList.remove("spinning"), 1500);
    });
  }

  /* ── Listen for storage changes while popup is open (content script re-scrapes) ── */
  chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace !== "local") return;
    if (changes.scraped?.newValue) {
      const data = changes.scraped.newValue;
      applyScraped(data, data.jobBoard || "page");
    }
    if (changes.contextCompany?.newValue) {
      companyInput.value = changes.contextCompany.newValue;
      flashField(companyInput);
      chrome.storage.local.remove("contextCompany");
    }
    if (changes.contextRole?.newValue) {
      roleInput.value = changes.contextRole.newValue;
      flashField(roleInput);
      chrome.storage.local.remove("contextRole");
    }
    if (changes.contextBoard?.newValue) {
      boardInput.value = changes.contextBoard.newValue;
      chrome.storage.local.remove("contextBoard");
    }
  });

  /* ── Settings ── */
  goSettingsBtn.addEventListener("click", showSettings);
  backMainBtn.addEventListener("click", showMain);

  saveSettingsBtn.addEventListener("click", () => {
    const apiUrl   = document.getElementById("api-url").value.replace(/\/$/, "");
    const apiToken = document.getElementById("api-token").value;
    chrome.storage.local.set({ apiUrl, apiToken }, () => {
      showMain();
      showMessage("Settings saved!", "success");
    });
  });

  /* ── Submit ── */
  submitBtn.addEventListener("click", async () => {
    // Guard: prevent double-submit
    if (_submitting) return;

    const company = companyInput.value.trim();
    if (!company) {
      showMessage("Company Name is required.", "error");
      companyInput.focus();
      return;
    }

    _submitting = true;
    setSubmitLoading(true);

    let stored;
    try {
      stored = await getStorage(["apiUrl", "apiToken"]);
    } catch (err) {
      showMessage("Storage error. Try again.", "error");
      _submitting = false;
      setSubmitLoading(false);
      return;
    }

    const apiUrl   = stored.apiUrl   || "http://127.0.0.1:8000";
    const apiToken = stored.apiToken;

    if (!apiToken) {
      showMessage("API Token missing — check Settings.", "error");
      _submitting = false;
      setSubmitLoading(false);
      return;
    }

    const payload = {
      company_name: company,
      role:         roleInput.value.trim()                           || null,
      job_url:      urlInput.value.trim()                           || null,
      job_board:    boardInput.value.trim()                         || null,
      status:       document.getElementById("status").value,
      notes:        document.getElementById("notes").value.trim()   || null,
    };

    try {
      const response = await fetch(`${apiUrl}/api/applications`, {
        method:  "POST",
        headers: {
          "Content-Type":  "application/json",
          "Authorization": `Bearer ${apiToken}`,
        },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        showMessage("Application recorded! ✓", "success");
        // Clear transient fields, keep company + board for context
        roleInput.value  = "";
        urlInput.value   = "";
        document.getElementById("notes").value   = "";
        document.getElementById("status").value  = "Applied";
        if (locationInput) locationInput.value = "";
        if (salaryInput)   salaryInput.value   = "";
        chrome.storage.local.remove("scraped");
      } else {
        let errMsg = "Failed to record.";
        try {
          const errData = await response.json();
          errMsg = errData.detail || errData.message || errMsg;
        } catch (_) {}
        showMessage(errMsg, "error");
      }
    } catch (err) {
      // Network / fetch error
      const msg = err?.message?.includes("Failed to fetch")
        ? "Network error. Is the backend running?"
        : (err?.message || "Unknown error.");
      showMessage(msg, "error");
    } finally {
      // Always restore button state
      _submitting = false;
      setSubmitLoading(false);
    }
  });

  /* ── Utilities ── */
  function getStorage(keys) {
    return new Promise(resolve => chrome.storage.local.get(keys, resolve));
  }

  function showSettings() { mainView.classList.add("hidden"); settingsView.classList.remove("hidden"); statusMsg.textContent = ""; }
  function showMain()     { settingsView.classList.add("hidden"); mainView.classList.remove("hidden"); statusMsg.textContent = ""; }

  function showMessage(msg, type) {
    statusMsg.textContent = msg;
    statusMsg.className   = "status-msg " + type;
    setTimeout(() => { statusMsg.textContent = ""; statusMsg.className = "status-msg"; }, 4500);
  }

  function setSubmitLoading(loading) {
    // Grab elements fresh each call in case DOM was re-painted
    const icon    = submitBtn.querySelector(".btn-icon");
    const label   = submitBtn.querySelector(".btn-label");
    const spinner = submitBtn.querySelector(".btn-spinner");

    submitBtn.disabled = loading;
    // Visually indicate loading state on the button itself
    submitBtn.classList.toggle("is-loading", loading);

    if (loading) {
      icon?.classList.add("hidden");
      spinner?.classList.remove("hidden");
      if (label) label.textContent = "Recording\u2026"; // "Recording…"
    } else {
      // Restore: explicitly remove hidden from icon, add to spinner
      icon?.classList.remove("hidden");
      spinner?.classList.add("hidden");
      if (label) label.textContent = "Record Application";
    }
  }

  /* ── Kick off ── */
  init();
});
