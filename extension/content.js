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
    chrome.storage.local.set({
      scraped: {
        ...result,
        url:       window.location.href,
        pageTitle: document.title,
        scrapedAt: Date.now(),
      }
    });
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
    return true;
  });

})();
