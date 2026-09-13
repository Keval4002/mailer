/**
 * Mailer — Content Script v3
 *
 * Extraction priority (highest → lowest):
 *  1. JSON-LD  (schema.org/JobPosting) — perfect structured data, used by most ATS
 *  2. OpenGraph / meta tags             — reliable titles
 *  3. Board-specific DOM selectors      — per-site hardened chains
 *  4. Universal heuristics              — title parsing, domain extraction
 *
 * Push strategy: write to chrome.storage.local immediately (popup reads
 * without waiting for a message roundtrip). Retry for SPA lazy-load.
 */

(function () {
  "use strict";

  /* ──────────────────────────────────────────────
     TEXT UTILITIES
  ────────────────────────────────────────────── */

  /**
   * Get clean text from a DOM element.
   * Uses innerText (renders HTML, resolves entities, respects CSS display:none)
   * falls back to textContent with manual entity decode.
   */
  function elText(el) {
    if (!el) return "";
    // innerText is best — it respects rendering and decodes entities
    let t = (el.innerText || "").trim();
    if (!t) {
      // Fallback: manually decode common entities from textContent
      t = (el.textContent || "").trim();
    }
    return cleanStr(t);
  }

  /** Decode HTML entities and strip extraneous whitespace/chars */
  function cleanStr(t) {
    if (!t) return "";
    // Decode HTML entities via a temporary DOM node (works in extension context)
    const tmp = document.createElement("textarea");
    tmp.innerHTML = t;
    t = tmp.value;
    return t
      .replace(/[\u00A0\u00AD\u200B\u200C\u200D\uFEFF\u202F\u205F]/g, " ") // special spaces / zero-width
      .replace(/\s+/g, " ")
      .replace(/^[\s\-–—|]+|[\s\-–—|]+$/g, "") // strip leading/trailing separators
      .trim();
  }

  /** Query multiple selectors, return first non-empty clean text */
  function text(...selectors) {
    for (const sel of selectors) {
      try {
        const el = document.querySelector(sel);
        const t = elText(el);
        if (t) return t;
      } catch (_) {}
    }
    return "";
  }

  /** Get all matching elements' text, return the best (longest non-empty) */
  function textAll(selector) {
    try {
      return [...document.querySelectorAll(selector)]
        .map(el => elText(el))
        .filter(Boolean)
        .sort((a, b) => b.length - a.length)[0] || "";
    } catch { return ""; }
  }

  function metaContent(...names) {
    for (const name of names) {
      const el = document.querySelector(
        `meta[property="${name}"], meta[name="${name}"]`
      );
      const c = el ? cleanStr(el.getAttribute("content") || "") : "";
      if (c) return c;
    }
    return "";
  }

  /* ──────────────────────────────────────────────
     1. JSON-LD EXTRACTION (highest priority)
     Most modern ATS embed schema.org/JobPosting data.
     LinkedIn, Indeed, Greenhouse, Ashby, SmartRecruiters,
     Workday, Lever, BambooHR, Rippling all do this.
  ────────────────────────────────────────────── */

  function extractFromJsonLD() {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of scripts) {
      try {
        const raw  = script.textContent || script.innerHTML || "";
        const data = JSON.parse(raw);
        // Handle arrays (some pages embed multiple items)
        const items = Array.isArray(data) ? data : [data];
        for (const item of items) {
          const type = item["@type"];
          if (type === "JobPosting" || type === "Job") {
            return parseJobPostingLD(item);
          }
          // Some pages nest it under @graph
          if (item["@graph"]) {
            const job = item["@graph"].find(n => n["@type"] === "JobPosting" || n["@type"] === "Job");
            if (job) return parseJobPostingLD(job);
          }
        }
      } catch (_) {}
    }
    return null;
  }

  function parseJobPostingLD(item) {
    const role    = cleanStr(item.title || "");
    const company = cleanStr(
      item.hiringOrganization?.name ||
      item.hiringOrganization?.["@id"] ||
      ""
    );

    // Location: try multiple shapes
    let location = "";
    const loc = item.jobLocation;
    if (Array.isArray(loc) && loc.length) {
      location = formatLocation(loc[0]);
    } else if (loc) {
      location = formatLocation(loc);
    }
    if (!location && item.jobLocationType) {
      // TELECOMMUTE = remote
      location = item.jobLocationType === "TELECOMMUTE" ? "Remote" : cleanStr(item.jobLocationType);
    }

    // Salary
    const sal = item.baseSalary || item.estimatedSalary;
    let salary = "";
    if (sal) {
      if (typeof sal === "string") {
        salary = cleanStr(sal);
      } else {
        const val = sal.value;
        if (val) {
          if (typeof val === "number") {
            salary = `${sal.currency || ""} ${val}`.trim();
          } else if (val.minValue && val.maxValue) {
            salary = `${sal.currency || ""} ${val.minValue}–${val.maxValue}`.trim();
          } else if (val.value) {
            salary = `${sal.currency || ""} ${val.value}`.trim();
          }
        }
      }
    }

    const jobType = cleanStr(
      Array.isArray(item.employmentType) ? item.employmentType.join(", ") : (item.employmentType || "")
    );

    return { role, company, location, salary, jobType };
  }

  function formatLocation(loc) {
    if (!loc) return "";
    const addr = loc.address || loc;
    const parts = [
      addr.addressLocality,
      addr.addressRegion,
      addr.addressCountry,
    ].filter(Boolean).map(cleanStr);
    return parts.join(", ");
  }

  /* ──────────────────────────────────────────────
     2. BOARD-SPECIFIC DOM SCRAPERS
  ────────────────────────────────────────────── */

  const SCRAPERS = [

    /* ── LinkedIn ── */
    {
      match: (url) => url.includes("linkedin.com/jobs"),
      board: "LinkedIn",
      scrape() {
        // LinkedIn often has JSON-LD; also try DOM
        const role = text(
          ".job-details-jobs-unified-top-card__job-title h1",
          ".job-details-jobs-unified-top-card__job-title",
          "h1.t-24",
          "[data-test-id='job-title']",
          ".jobs-details-top-card__job-title"
        );
        const company = text(
          ".job-details-jobs-unified-top-card__company-name a",
          ".job-details-jobs-unified-top-card__company-name",
          ".job-details-jobs-unified-top-card__primary-description-container a",
          ".jobs-details-top-card__company-info a"
        );
        const location = text(
          ".job-details-jobs-unified-top-card__bullet",
          ".jobs-unified-top-card__bullet",
          ".job-details-jobs-unified-top-card__workplace-type"
        );
        const salary = text(
          ".job-details-jobs-unified-top-card__job-insight--highlight span",
          ".job-details-jobs-unified-top-card__salary-main-rail-text",
          ".compensation__salary"
        );
        const jobType = text(
          ".job-details-jobs-unified-top-card__job-insight span",
          "[data-test-id='workplace-type']"
        );
        return { role, company, location, salary, jobType };
      }
    },

    /* ── Indeed ── */
    {
      match: (url) => url.includes("indeed.com"),
      board: "Indeed",
      scrape() {
        const role = stripSuffix(
          text(
            "h1[data-testid='simpler-jobTitle']",
            "h1.jobsearch-JobInfoHeader-title",
            "h2.jobsearch-JobInfoHeader-title",
            ".jobsearch-JobInfoHeader-title"
          ),
          /\s*[-–]\s*job post\s*$/i
        );
        const company = text(
          "[data-testid='inlineHeader-companyName'] a",
          "[data-testid='inlineHeader-companyName']",
          "[data-company-name='true']",
          ".jobsearch-CompanyReview--heading",
          ".jobsearch-InlineCompanyRating a"
        );
        const location = text(
          "[data-testid='job-location']",
          ".jobsearch-JobInfoHeader-subtitle [data-testid='job-location']",
          "[data-testid='inlineHeader-location']"
        );
        const salary = text(
          "[data-testid='attribute_snippet_testid']",
          "#salaryInfoAndJobType .attribute_snippet",
          ".jobsearch-JobInfoHeader-salary"
        );
        const jobType = text("[data-testid='jobType-text']");
        return { role, company, location, salary, jobType };
      }
    },

    /* ── Workday (myworkdayjobs.com AND myworkdaysite.com) ── */
    {
      match: (url) =>
        url.includes("workday.com") ||
        url.includes("myworkdayjobs.com") ||
        url.includes("myworkdaysite.com"),
      board: "Workday",
      scrape() {
        // Workday is heavily React — selectors change often, use multiple chains
        const role = text(
          "[data-automation-id='jobPostingHeader']",
          "h1[data-automation-id='jobPostingHeader']",
          ".WEXKZ",                              // class-based (minified)
          "h2.css-1q2dra3",
          "h1"
        );

        // Company: extract from URL path — Workday URLs follow:
        // /recruiting/{company}/{CompanyDisplay}/job/...
        // OR from page title "Role - Company | Workday"
        const company =
          extractWorkdayCompany() ||
          text(
            "[data-automation-id='company-name']",
            ".css-lz6ly8",
            ".css-129m7dg"
          );

        const location = text(
          "[data-automation-id='locations']",
          "[data-automation-id='location']",
          ".css-56kyx5 button",
          "[data-automation-id='locationText']"
        );
        const salary = text(
          "[data-automation-id='pay-range-details']",
          "[data-automation-id='payRangeDetails']",
          "[data-automation-id='salaryRange']"
        );
        const jobType = text("[data-automation-id='time-type']");
        return { role, company, location, salary, jobType };
      }
    },

    /* ── Lever ── */
    {
      match: (url) => url.includes("lever.co"),
      board: "Lever",
      scrape() {
        const role = text(
          ".posting-headline h2",
          "h2.posting-name",
          "[data-qa='posting-name']",
          "h2"
        );
        const company =
          extractLeverCompany() ||
          text(".main-header-logo img[alt]");
        const location = text(
          ".posting-categories .location",
          "[data-qa='posting-location']",
          ".posting-category"
        );
        const jobType = text(
          ".posting-categories .commitment",
          "[data-qa='posting-work-type']"
        );
        return { role, company, location, jobType };
      }
    },

    /* ── Greenhouse (boards.greenhouse.io + self-hosted) ── */
    {
      match: (url) => url.includes("greenhouse.io"),
      board: "Greenhouse",
      scrape() {
        const role = text(
          "h1.app-title",
          ".job-post-overview h1",
          "#main_content h1",
          "h1"
        );
        const company =
          extractGreenhouseCompany() ||
          text(".company-name", ".header--greenhouse .company");
        const location = text(".location", "#job_details .location", ".office-location");
        const jobType  = text(".employment-type", ".job-type");
        return { role, company, location, jobType };
      }
    },

    /* ── Wellfound / AngelList ── */
    {
      match: (url) => url.includes("wellfound.com"),
      board: "Wellfound",
      scrape() {
        const role    = text("h1", "[class*='jobTitle']", "[class*='title']");
        const company = text("[class*='companyName']", "[class*='startupName']", "[class*='startup-name']");
        const location= text("[class*='location']", "[class*='Location']");
        const salary  = text("[class*='compensation']", "[class*='Compensation']", "[class*='salary']");
        return { role, company, location, salary };
      }
    },

    /* ── Ashby ── */
    {
      match: (url) => url.includes("ashbyhq.com"),
      board: "Ashby",
      scrape() {
        const role    = text("h1.ashby-job-posting-heading", "h1");
        const company = text(
          ".ashby-application-form-header-company-name",
          ".ashby-job-posting-company-name",
          "title"
        );
        const location = textAll(".ashby-job-posting-brief-items li");
        return { role, company, location };
      }
    },

    /* ── SmartRecruiters ── */
    {
      match: (url) => url.includes("smartrecruiters.com"),
      board: "SmartRecruiters",
      scrape() {
        const role    = text("h1[itemprop='title']", "h1.sr-job-position-name", "h1");
        const company = text(
          ".sr-company-name",
          "[itemprop='hiringOrganization'] [itemprop='name']"
        );
        const location = text("[itemprop='jobLocation'] [itemprop='name']", ".sr-location");
        return { role, company, location };
      }
    },

    /* ── Naukri ── */
    {
      match: (url) => url.includes("naukri.com"),
      board: "Naukri",
      scrape() {
        const role    = text("h1.jd-header-title", ".jd-header h1", "h1");
        const company = text("a.jd-header-comp-name", ".jd-comp-name", ".comp-name");
        const location= text(".loc span", ".locWdth", ".location");
        const salary  = text(".salary-detail-container span", ".salary");
        return { role, company, location, salary };
      }
    },

    /* ── Internshala ── */
    {
      match: (url) => url.includes("internshala.com"),
      board: "Internshala",
      scrape() {
        const role    = text(".internship_heading .heading_4_5", ".job_heading .heading_4_5", "h1");
        const company = text(".company_name a", ".company-name", ".company_name");
        const location= text(".location_link", ".location span", ".location");
        const salary  = text(".stipend_header", ".salary");
        return { role, company, location, salary };
      }
    },

    /* ── Jobvite ── */
    {
      match: (url) => url.includes("jobvite.com"),
      board: "Jobvite",
      scrape() {
        const role    = text(".jv-job-detail-meta h2.jv-header", "h2.jv-header");
        const company = text(".jv-header-company-name", "[itemprop='hiringOrganization'] [itemprop='name']");
        const location= text(".jv-job-detail-meta ul li:first-child");
        return { role, company, location };
      }
    },

    /* ── iCIMS ── */
    {
      match: (url) => url.includes("icims.com"),
      board: "iCIMS",
      scrape() {
        const role    = text("#icims_content_main_header h1", "h1.iCIMS_Header", "h1");
        const company = text(".iCIMS_CompanyIntro_Header");
        const location= text(".iCIMS_Header_Job_Location");
        return { role, company, location };
      }
    },

    /* ── BambooHR ── */
    {
      match: (url) => url.includes("bamboohr.com"),
      board: "BambooHR",
      scrape() {
        const role    = text("h2.ResumatorJobTitle", "h1", "[data-bi-id='job-title']");
        const company = text(".ResumatorCompanyName", "[itemprop='hiringOrganization'] [itemprop='name']");
        const location= text("[data-bi-id='job-location']", ".ResumatorJobLocation");
        return { role, company, location };
      }
    },

    /* ── Rippling ── */
    {
      match: (url) => url.includes("rippling.com"),
      board: "Rippling",
      scrape() {
        const role    = text("h1", "[class*='jobTitle']");
        const company = text("[class*='companyName']", "[class*='company-name']");
        const location= text("[class*='location']", "[class*='Location']");
        return { role, company, location };
      }
    },

    /* ── Taleo ── */
    {
      match: (url) => url.includes("taleo.net"),
      board: "Taleo",
      scrape() {
        const role    = text(".requisitionTitle", "h1#requisitionTitle", "h1");
        const company = text(".orgTitle", "#company");
        const location= text(".location", "#location");
        return { role, company, location };
      }
    },

    /* ── Universal fallback ── */
    {
      match: () => true,
      board: null,
      scrape() {
        const role    = text("h1", "[itemprop='title']") || metaContent("og:title", "twitter:title");
        const company = text("[itemprop='hiringOrganization'] [itemprop='name']") ||
                        extractCompanyFromTitle() ||
                        extractCompanyFromDomain();
        const location= text("[itemprop='jobLocation']", ".location", ".job-location");
        const salary  = text("[itemprop='baseSalary']", ".salary", ".compensation");
        return { role, company, location, salary };
      }
    }
  ];

  /* ──────────────────────────────────────────────
     BOARD-SPECIFIC URL EXTRACTORS
  ────────────────────────────────────────────── */

  function extractWorkdayCompany() {
    const pathname = window.location.pathname;
    // Pattern: /recruiting/{company}/{CompanyDisplay}/job/...
    // e.g.    /recruiting/magna/Magna/job/Bangalore-IN/...
    const m = pathname.match(/\/recruiting\/([^/]+)/);
    if (m && m[1]) {
      // Pick the better-cased segment if it exists
      const parts = pathname.split("/").filter(Boolean);
      const idx   = parts.indexOf("recruiting");
      if (idx >= 0 && parts[idx + 2] && parts[idx + 2] !== "job") {
        // parts[idx+2] is often the display name e.g. "Magna"
        return capitalise(decodeURIComponent(parts[idx + 2]).replace(/[-_]/g, " "));
      }
      return capitalise(decodeURIComponent(m[1]).replace(/[-_]/g, " "));
    }
    // Subdomain e.g. magna.wd3.myworkdaysite.com  — skip "wd#" segments
    const sub = window.location.hostname.split(".");
    const company = sub.find(s => !/^(wd\d+|www|jobs|careers)$/i.test(s));
    if (company) return capitalise(company.replace(/[-_]/g, " "));
    return "";
  }

  function extractLeverCompany() {
    if (window.location.hostname === "jobs.lever.co") {
      const parts = window.location.pathname.split("/").filter(Boolean);
      if (parts[0]) return capitalise(decodeURIComponent(parts[0]).replace(/-/g, " "));
    }
    return "";
  }

  function extractGreenhouseCompany() {
    if (window.location.hostname === "boards.greenhouse.io") {
      const parts = window.location.pathname.split("/").filter(Boolean);
      if (parts[0]) return capitalise(decodeURIComponent(parts[0]).replace(/[_-]/g, " "));
    }
    return "";
  }

  function extractCompanyFromTitle() {
    const title = cleanStr(document.title || "");
    if (!title) return "";
    const sep   = /\s*[|\-–—·•]\s*/;
    const parts = title.split(sep).map(s => cleanStr(s)).filter(Boolean);
    if (parts.length >= 2) {
      // Last part is usually company name; shorter middle parts could be too
      const last = parts[parts.length - 1];
      // Avoid generic platform names
      if (!/linkedin|indeed|greenhouse|workday|lever|ashby/i.test(last)) {
        return last;
      }
      if (parts.length >= 3) return parts[parts.length - 2];
    }
    return "";
  }

  function extractCompanyFromDomain() {
    const hostname = window.location.hostname.replace(/^www\./, "");
    const parts    = hostname.split(".");
    return parts.length > 0 ? capitalise(parts[0]) : "";
  }

  function detectBoard(url) {
    if (url.includes("linkedin.com"))          return "LinkedIn";
    if (url.includes("indeed.com"))            return "Indeed";
    if (url.includes("lever.co"))              return "Lever";
    if (url.includes("greenhouse.io"))         return "Greenhouse";
    if (url.includes("workday.com") || url.includes("myworkdayjobs.com") || url.includes("myworkdaysite.com")) return "Workday";
    if (url.includes("wellfound.com"))         return "Wellfound";
    if (url.includes("ashbyhq.com"))           return "Ashby";
    if (url.includes("smartrecruiters.com"))   return "SmartRecruiters";
    if (url.includes("naukri.com"))            return "Naukri";
    if (url.includes("internshala.com"))       return "Internshala";
    if (url.includes("jobvite.com"))           return "Jobvite";
    if (url.includes("icims.com"))             return "iCIMS";
    if (url.includes("taleo.net"))             return "Taleo";
    if (url.includes("bamboohr.com"))          return "BambooHR";
    if (url.includes("rippling.com"))          return "Rippling";
    if (url.includes("breezy.hr"))             return "Breezy";
    if (url.includes("freshteam.com"))         return "Freshteam";
    return "Direct";
  }

  function capitalise(s) {
    return s.replace(/\b([a-z])/g, c => c.toUpperCase());
  }

  function stripSuffix(t, re) {
    return t ? t.replace(re, "").trim() : t;
  }

  /* ──────────────────────────────────────────────
     CORE SCRAPE FUNCTION
     Order: JSON-LD → board-specific DOM → fallback
  ────────────────────────────────────────────── */

  function runScrape() {
    const url = window.location.href;

    let result = {
      company:  "",
      role:     "",
      location: "",
      salary:   "",
      jobType:  "",
      jobBoard: detectBoard(url),
    };

    // 1. JSON-LD — highest fidelity
    const ld = extractFromJsonLD();
    if (ld) {
      result = { ...result, ...filterEmpty(ld) };
    }

    // 2. Board-specific DOM scraper (fills in anything still missing)
    for (const scraper of SCRAPERS) {
      if (scraper.match(url)) {
        try {
          const dom = scraper.scrape();
          if (scraper.board) result.jobBoard = scraper.board;

          // Merge: only fill fields still empty after JSON-LD
          for (const [k, v] of Object.entries(filterEmpty(dom))) {
            if (!result[k]) result[k] = v;
          }
        } catch (e) {
          console.warn("[Mailer] Scraper error:", e);
        }
        break;
      }
    }

    // 3. Last-resort meta / title fallback for role/company
    if (!result.role)    result.role    = cleanStr(metaContent("og:title", "twitter:title"));
    if (!result.company) result.company = extractCompanyFromTitle() || extractCompanyFromDomain();

    // Final clean — cap length, strip newlines
    for (const k of ["company", "role", "location", "salary", "jobType"]) {
      result[k] = (result[k] || "").replace(/\n.*/s, "").trim().substring(0, 150);
    }

    return result;
  }

  function filterEmpty(obj) {
    return Object.fromEntries(Object.entries(obj).filter(([, v]) => v && v.trim()));
  }

  /* ──────────────────────────────────────────────
     PROACTIVE PUSH — write to storage immediately
  ────────────────────────────────────────────── */

  function pushToStorage(result) {
    if (!result.company && !result.role) return; // nothing useful
    try {
      const p = chrome.storage.local.set({
        scraped: {
          ...result,
          url:       window.location.href,
          pageTitle: document.title,
          scrapedAt: Date.now(),
        }
      });
      if (p && p.catch) p.catch(() => {});
    } catch (e) {
      // Suppress synchronous context invalidated errors
    }
  }

  /* ──────────────────────────────────────────────
     SPA OBSERVER — re-scrape on navigation / DOM change
  ────────────────────────────────────────────── */

  let lastUrl      = window.location.href;
  let debounce     = null;

  function scheduleRescrape(delay = 800) {
    clearTimeout(debounce);
    debounce = setTimeout(() => pushToStorage(runScrape()), delay);
  }

  function checkUrlChange() {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      scheduleRescrape(1400);
    }
  }

  const observer = new MutationObserver(() => {
    checkUrlChange();
    scheduleRescrape(700);
  });

  // Watch only first-level children changes (avoid thrash on minor mutations)
  observer.observe(document.body, {
    childList: true,
    subtree:   true,
  });

  // Intercept history API (SPA routing)
  const origPush    = history.pushState.bind(history);
  const origReplace = history.replaceState.bind(history);
  history.pushState    = (...a) => { origPush(...a);    scheduleRescrape(1400); };
  history.replaceState = (...a) => { origReplace(...a); scheduleRescrape(1400); };
  window.addEventListener("popstate", () => scheduleRescrape(1400));

  /* ──────────────────────────────────────────────
     INITIAL SCRAPE
  ────────────────────────────────────────────── */

  (function initialScrape() {
    const result = runScrape();
    pushToStorage(result);

    // Workday & LinkedIn render content 1–3s after document_idle
    if (!result.role || !result.company) {
      setTimeout(() => pushToStorage(runScrape()), 1500);
      setTimeout(() => pushToStorage(runScrape()), 3500);
      setTimeout(() => pushToStorage(runScrape()), 6000); // extra pass for slow ATSes
    }
  })();

  /* ──────────────────────────────────────────────
     ON-DEMAND MESSAGE LISTENER (from popup)
  ────────────────────────────────────────────── */

  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "scrape_job") {
      try {
        const result = runScrape();
        pushToStorage(result);
        sendResponse(result);
      } catch (e) {
        sendResponse({ company: "", role: "", error: e.toString() });
      }
    }

    if (request.action === "paste_linkedin_message") {
      // pasteIntoLinkedInMessageBox is scoped inside the IIFE below;
      // use the window.__ namespace it exposes.
      const pasteFn = window.__mailerPasteLinkedIn;
      if (!pasteFn) {
        sendResponse({ ok: false, error: "Paste handler not ready (not a LinkedIn page)." });
      } else {
        try {
          pasteFn(request.text);
          sendResponse({ ok: true });
        } catch (e) {
          sendResponse({ ok: false, error: e.toString() });
        }
      }
    }

    if (request.action === "get_linkedin_recipient") {
      // scrapeLinkedInRecipient is also IIFE-scoped; use the exposed wrapper.
      const scrapeFn = window.__mailerScrapeRecipient;
      if (!scrapeFn) {
        sendResponse({ recipient: null });
      } else {
        try {
          const data = scrapeFn();
          if (data) {
            // Also persist it so storage.onChanged fires in the popup
            chrome.storage.local.set({ linkedinRecipient: { ...data, detectedAt: Date.now() } });
          }
          sendResponse({ recipient: data });
        } catch (e) {
          sendResponse({ recipient: null, error: e.toString() });
        }
      }
    }

    return true;
  });

  /* ──────────────────────────────────────────────
     LINKEDIN MESSAGING ASSISTANT
     Detects recipient info from LinkedIn messaging
     contexts (dedicated page, overlay, InMail).
  ────────────────────────────────────────────── */

  (function linkedInMessagingAssistant() {
    const isLinkedIn = () => window.location.hostname.includes("linkedin.com");
    if (!isLinkedIn()) return;

    /* ── Recipient selectors (ordered best → worst) ── */
    const RECIPIENT_NAME_SELECTORS = [
      // Dedicated messaging page — thread header
      ".msg-thread__link-underline",
      ".msg-s-message-list-container .msg-entity-lockup__entity-title",
      // InMail compose modal header (profile page)
      ".artdeco-modal .msg-entity-lockup__entity-title",
      ".artdeco-modal .pvs-header__title span[aria-hidden='true']",
      // Messaging overlay (bottom-right bubble)
      ".msg-overlay-conversation-bubble--active .msg-entity-lockup__entity-title",
      ".msg-overlay-bubble-header__title",
      // Profile page InMail / Connect modal
      ".artdeco-modal .send-invite__headline",
      ".artdeco-modal [data-test-modal-header-title]",
      // Connection request modal
      ".connect-button-send-invite__profile-info h2",
      
      // Profile page h1 (scoped to intro card to avoid modal noise)
      ".pv-text-details__left-panel h1",
      "main .ph5 h1",
      "section.artdeco-card h1.text-heading-xlarge",
      ".pv-top-card-v2-ctas h1",
      "[data-generated-suggestion-target] h1",
      "h1.text-heading-xlarge",
      "h1",
      "h2",
    ];

    const RECIPIENT_SUBTITLE_SELECTORS = [
      // Messaging modals / threads
      ".msg-entity-lockup__subtitle",
      ".artdeco-modal .msg-entity-lockup__subtitle",
      ".msg-overlay-conversation-bubble--active .msg-entity-lockup__subtitle",
      
      // Profile page headline (scoped to intro card to avoid picking up "Add a note" modal text)
      ".pv-text-details__left-panel .text-body-medium.break-words",
      "main .ph5 .text-body-medium.break-words",
      "section.artdeco-card .text-body-medium.break-words",
      ".pv-top-card--list .text-body-medium",
      "[data-generated-suggestion-target] .text-body-medium",
    ];

    /** Extract name and headline directly from LinkedIn's internal JSON-based state hydration */
    function parseLinkedInHydrationState() {
      try {
        const codeBlocks = document.querySelectorAll('code[id^="bpr-guid-"]');
        for (const block of codeBlocks) {
          try {
            const raw = block.textContent.trim();
            if (!raw.startsWith('{')) continue;
            
            const data = JSON.parse(raw);
            if (data && Array.isArray(data.included)) {
              for (const item of data.included) {
                // Ensure we are reading the profile of the person we are currently viewing
                const publicId = item.publicIdentifier;
                if (!publicId || !window.location.href.includes(publicId)) continue;
                
                // Name might be nested localized object or a direct string depending on Voyager version
                let fName = item.firstName || "";
                let lName = item.lastName || "";
                if (typeof fName === "object") fName = fName.text || fName.localized || ""; 
                if (typeof lName === "object") lName = lName.text || lName.localized || "";
                
                let head = item.headline || "";
                if (typeof head === "object") head = head.text || head.localized || "";

                if (fName && head) {
                  return {
                    name: `${fName} ${lName}`.trim(),
                    subtitle: head
                  };
                }
              }
            }
          } catch (e) {}
        }
      } catch (e) {}
      return null;
    }

    function scrapeLinkedInRecipient() {
      let name = "";
      let subtitle = "";

      // 1. Try to pull structured data from hydration state (Bulletproof for profile pages)
      if (window.location.href.includes("/in/")) {
        const hydrated = parseLinkedInHydrationState();
        if (hydrated) {
          name = hydrated.name;
          subtitle = hydrated.subtitle;
        }
      }

      // 2. DOM Selectors Fallback
      if (!name) {
        for (const sel of RECIPIENT_NAME_SELECTORS) {
          try {
            const el = document.querySelector(sel);
            const t  = elText(el);
            if (t && t.length > 1) { name = t; break; }
          } catch (_) {}
        }
      }

      if (!subtitle) {
        for (const sel of RECIPIENT_SUBTITLE_SELECTORS) {
          try {
            const el = document.querySelector(sel);
            const t  = elText(el);
            if (t) { subtitle = t; break; }
          } catch (_) {}
        }
      }

      // Robust fallback for LinkedIn's new obfuscated DOM (which removes h1s and standard classes).
      // Profile page titles usually look like: "Kiran Poojary - Chief Technology Officer at Simple Energy | LinkedIn"
      if ((!name || !subtitle) && document.title.includes("LinkedIn")) {
        const titleText = document.title.replace(/\s*\|\s*LinkedIn\s*$/, "");
        const parts = titleText.split(/\s+-\s+/);
        if (!name && parts.length > 0) {
          name = cleanStr(parts[0]);
        }
        if (!subtitle && parts.length > 1) {
          subtitle = cleanStr(parts.slice(1).join(" - "));
        }
      }

      // Parse title + company from headline using robust multi-pattern extractor
      let title   = "";
      let company = "";
      if (subtitle) {
        const extracted = extractTitleAndCompany(subtitle);
        title   = extracted.title;
        company = extracted.company;
      }

      // Fallback: If no company found in headline, check the profile's 'Current company' badge
      if (!company) {
        const companyBadge = document.querySelector(".pv-text-details__right-panel .inline-show-more-text, button[aria-label*='Current company']");
        if (companyBadge) {
          company = elText(companyBadge);
        }
      }

      const firstName = name ? name.trim().split(/\s+/)[0] : "";

      return name ? { name, firstName, title, company } : null;
    }

    /** Extract title + company from a LinkedIn headline/subtitle string.
     *  Handles all common LinkedIn headline formats:
     *    "Software Engineer at Qualcomm"          → standard
     *    "Software Engineer @Qualcomm | 802.15.4" → @symbol + noise
     *    "@Qualcomm | Software Engineer"           → company first
     *    "Software Engineer | Qualcomm | NIT"      → pipe-separated
     *    "Software Engineer · Qualcomm"            → bullet-separated
     */
    function extractTitleAndCompany(subtitle) {
      if (!subtitle) return { title: "", company: "" };
      const s = subtitle.trim();

      // 1. "Title at Company [noise]"
      const atWord = s.match(/^(.+?)\s+at\s+([^|·•\n]+?)(?:\s*[|·•||].*)?$/i);
      if (atWord) return { title: cleanStr(atWord[1]), company: cleanStr(atWord[2]) };

      // 2. "Title @Company [noise]" — e.g. "Software Engineer @Qualcomm || 802.15.4"
      const atSym = s.match(/^(.*?)\s*@([A-Za-z][A-Za-z0-9\s\-&.,']+?)(?:\s*(?:[|·•]|\|\|).*)?$/);
      if (atSym && atSym[2]) {
        return { title: cleanStr(atSym[1] || ""), company: cleanStr(atSym[2]) };
      }

      // 3. "@Company" alone (no title in subtitle — title may be on another element)
      const atOnly = s.match(/^@([A-Za-z][A-Za-z0-9\s\-&.,']+?)(?:\s*(?:[|·•]|\|\|).*)?$/);
      if (atOnly) return { title: "", company: cleanStr(atOnly[1]) };

      // 4. "Title | Company [| ...]", "Title · Company", or "Title - Company"
      // Safely split by pipe, bullet, or spaced dashes
      const parts = s.split(/\s*(?:[|·•]|\|\|)\s*|\s+[-–—]\s+/);
      if (parts.length >= 2) {
        // Heuristic: company-like part is title-cased and short (< 40 chars),
        // not a number, not a hash/spec token
        const companyPart = parts.find((p, i) => i > 0 && /^[A-Z]/.test(p) && p.length < 40 && !/^\d/.test(p));
        if (companyPart) return { title: cleanStr(parts[0]), company: cleanStr(companyPart) };
        return { title: cleanStr(parts[0]), company: "" };
      }

      return { title: cleanStr(s), company: "" };
    }

    function pushRecipientToStorage(data) {
      if (!data) return;
      try {
        const p = chrome.storage.local.set({ linkedinRecipient: { ...data, detectedAt: Date.now() } });
        if (p && p.catch) p.catch(() => {});
      } catch (e) {
        // Suppress orphaned script errors
      }
    }

    /* ── Paste into LinkedIn's contenteditable message box ──
       Strategies tried in order (most specific → most permissive):
         1. document.activeElement — if user clicked the box before pressing Paste
         2. Named compose selectors — covers all known LinkedIn compose surfaces
         3. Any visible contenteditable on page
         4. Any visible <textarea> (fallback for non-React surfaces)
       Text insertion also uses multiple techniques because LinkedIn's React
       synthetic event system ignores plain DOM mutations on some builds.
    ────────────────────────────────────────────── */
    function pasteIntoLinkedInMessageBox(text) {
      const box = findComposeBox();
      if (!box) throw new Error("LinkedIn message box not found on page.");

      insertTextIntoBox(box, text);
    }

    function findComposeBox() {
      // 1. Best: use whatever element currently has keyboard focus
      const active = document.activeElement;
      if (active && isEditableBox(active)) return active;

      // 2. Named selectors — ordered from most-specific to broadest
      //    Covers: InMail modal, regular messaging page, overlay bubble,
      //    connection request note, and generic artdeco modal compose areas.
      const SELECTORS = [
        // InMail / "New message" modal opened from a profile page
        ".artdeco-modal__content .msg-form__contenteditable",
        ".artdeco-modal__content [contenteditable='true']",
        ".artdeco-modal [contenteditable='true']",
        // Standard full-page messaging (/messaging/thread/*)
        ".msg-form__contenteditable",
        // Messaging overlay bubble (bottom-right of screen)
        ".msg-overlay-conversation-bubble--active .msg-form__contenteditable",
        ".msg-overlay-list-bubble .msg-form__contenteditable",
        // Connection request note textarea
        ".connect-button-send-invite__custom-message",
        // Any focused/active compose wrapper
        "[data-artdeco-is-focused='true'] [contenteditable='true']",
        // Generic role=textbox (React accessibility pattern)
        "[contenteditable='true'][role='textbox']",
        "[contenteditable='true']",
      ];

      for (const sel of SELECTORS) {
        try {
          const el = document.querySelector(sel);
          if (el && isVisible(el)) return el;
        } catch (_) {}
      }

      // 3. Any visible contenteditable on page (last resort)
      const allEditable = [...document.querySelectorAll("[contenteditable='true']")];
      const visible = allEditable.find(isVisible);
      if (visible) return visible;

      // 4. Any visible textarea
      const allTextareas = [...document.querySelectorAll("textarea")];
      return allTextareas.find(isVisible) || null;
    }

    function isEditableBox(el) {
      if (!el) return false;
      const tag = el.tagName.toLowerCase();
      return tag === "textarea" ||
             el.getAttribute("contenteditable") === "true" ||
             el.getAttribute("role") === "textbox";
    }

    function isVisible(el) {
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return false;
      const style = window.getComputedStyle(el);
      return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
    }

    function insertTextIntoBox(box, text) {
      box.focus();

      // ── Strategy A: Native Input Event (works with React 16+ synthetic events) ──
      // This is the most reliable for LinkedIn's React build.
      const nativeInput = Object.getOwnPropertyDescriptor(
        window.HTMLElement.prototype, "innerHTML"
      );
      try {
        // Set selection to end of existing content
        const sel = window.getSelection();
        if (sel && box.childNodes.length > 0) {
          const range = document.createRange();
          range.selectNodeContents(box);
          range.collapse(false); // collapse to end
          sel.removeAllRanges();
          sel.addRange(range);
        }

        // Fire a beforeinput event (React listens to this)
        const beforeInput = new InputEvent("beforeinput", {
          bubbles: true, cancelable: true,
          inputType: "insertText", data: text,
        });
        const cancelled = !box.dispatchEvent(beforeInput);

        if (!cancelled) {
          // If beforeinput wasn't cancelled by React, also fire execCommand
          // as Chrome uses both paths
          if (document.execCommand) {
            document.execCommand("insertText", false, text);
          }
        }

        // Always fire input + change so LinkedIn's send button activates
        box.dispatchEvent(new InputEvent("input",  { bubbles: true, inputType: "insertText", data: text }));
        box.dispatchEvent(new Event("change", { bubbles: true }));

        // Verify text was actually inserted
        const boxText = box.textContent || box.value || "";
        if (boxText.trim().length > 0) return; // success
      } catch (_) {}

      // ── Strategy B: execCommand fallback ──
      try {
        box.focus();
        if (document.execCommand("selectAll", false)) {
          document.execCommand("delete", false);
        }
        document.execCommand("insertText", false, text);
        box.dispatchEvent(new Event("input",  { bubbles: true }));
        box.dispatchEvent(new Event("change", { bubbles: true }));
        const b = box.textContent || box.value || "";
        if (b.trim().length > 0) return;
      } catch (_) {}

      // ── Strategy C: Direct DOM manipulation ──
      // Works if strategies A & B both fail (non-React textareas, etc.)
      if (box.tagName.toLowerCase() === "textarea") {
        box.value = text;
      } else {
        // Preserve LinkedIn's <p> wrapper structure if present
        const p = document.createElement("p");
        p.textContent = text;
        box.innerHTML = "";
        box.appendChild(p);
      }
      box.dispatchEvent(new Event("input",  { bubbles: true }));
      box.dispatchEvent(new Event("change", { bubbles: true }));
      // Trigger React's internal fiber update
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLElement.prototype, "innerText"
      );
      if (nativeInputValueSetter?.set) {
        nativeInputValueSetter.set.call(box, text);
        box.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }


    // Expose handlers via window so the message listener (outer scope) can call them
    window.__mailerPasteLinkedIn    = pasteIntoLinkedInMessageBox;
    window.__mailerScrapeRecipient  = scrapeLinkedInRecipient;

    /* -- Observe and push recipient continuously -- */
    let lastRecipientName = "";
    let recipientDebounce = null;

    function scheduleRecipientScrape(delay = 600) {
      clearTimeout(recipientDebounce);
      recipientDebounce = setTimeout(() => {
        const data = scrapeLinkedInRecipient();
        if (data && data.name !== lastRecipientName) {
          lastRecipientName = data.name;
          pushRecipientToStorage(data);
        }
      }, delay);
    }

    // Initial scrape — run at 800ms and again at 2.5s for SPAs that render late
    scheduleRecipientScrape(800);
    scheduleRecipientScrape(2500);

    // Re-scrape on any DOM change (conversation switches, modal opens)
    const recipientObserver = new MutationObserver(() => scheduleRecipientScrape(500));
    recipientObserver.observe(document.body, { childList: true, subtree: true });

    // Also re-scrape on SPA navigation
    const origPushLI    = history.pushState.bind(history);
    const origReplaceLI = history.replaceState.bind(history);
    history.pushState    = (...a) => { origPushLI(...a);    scheduleRecipientScrape(1200); };
    history.replaceState = (...a) => { origReplaceLI(...a); scheduleRecipientScrape(1200); };
  })();

})();
