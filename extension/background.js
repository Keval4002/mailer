/**
 * Mailer Background Service Worker v2
 *
 * Responsibilities:
 *  - Context menu items (right-click → set company/role)
 *  - Badge feedback when content script pushes scraped data
 *  - Inject content script on-demand for tabs not covered by manifest matches
 */

/* ── Context menus ── */
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "set-company", title: "📌 Set as Company Name", contexts: ["selection"] });
  chrome.contextMenus.create({ id: "set-role",    title: "🎯 Set as Role / Position", contexts: ["selection"] });
  chrome.contextMenus.create({ id: "set-board",   title: "🔗 Set as Job Board", contexts: ["selection"] });
});

chrome.contextMenus.onClicked.addListener((info) => {
  const t = info.selectionText?.trim() || "";
  if (!t) return;
  if (info.menuItemId === "set-company") chrome.storage.local.set({ contextCompany: t });
  if (info.menuItemId === "set-role")    chrome.storage.local.set({ contextRole:    t });
  if (info.menuItemId === "set-board")   chrome.storage.local.set({ contextBoard:   t });
});

/* ── On-demand injection for pages not in manifest matches ──
   When popup requests a scrape on an unmatched page, we inject the
   content script programmatically using scripting API.
*/
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "inject_and_scrape") {
    chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      if (!tabs.length) { sendResponse({ error: "No active tab" }); return; }
      const tabId = tabs[0].id;
      try {
        await chrome.scripting.executeScript({
          target: { tabId },
          files: ["content.js"],
        });
        // Give content script a moment to run initial scrape and push to storage
        setTimeout(async () => {
          const data = await chrome.storage.local.get("scraped");
          sendResponse(data.scraped || {});
        }, 1500);
      } catch (e) {
        sendResponse({ error: e.message });
      }
    });
    return true; // async
  }
});

/* ── Storage change listener: show badge when scrape data arrives ── */
chrome.storage.onChanged.addListener((changes, namespace) => {
  if (namespace !== "local" || !changes.scraped) return;
  const scraped = changes.scraped.newValue;
  if (!scraped?.company && !scraped?.role) return;

  // Flash a green badge on the extension icon to signal data is ready
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs.length) return;
    chrome.action.setBadgeText({ text: "✓", tabId: tabs[0].id }).catch(() => {});
    chrome.action.setBadgeBackgroundColor({ color: "#2eaa65", tabId: tabs[0].id }).catch(() => {});
    // Clear badge after 4s
    setTimeout(() => {
      chrome.action.setBadgeText({ text: "", tabId: tabs[0].id }).catch(() => {});
    }, 4000);
  });
});
