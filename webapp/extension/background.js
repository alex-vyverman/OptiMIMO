// OptiMIMO REW Bridge — background service worker.
//
// Fetches from the local REW API (http://127.0.0.1:4735) on behalf of
// optimimo.app. Extension fetches carry host_permissions, so they are not
// subject to the CORS / Private Network Access rules that block a public
// page from reaching loopback. Only message type "rew_fetch" with a path
// starting "/" is accepted; responses are returned as { ok, status, body }.
//
// Message size note: IR payloads are REW's base64 JSON (~1.4 MB for a
// 262144-sample IR), far below Chrome's 64 MB message limit.

const REW_BASE = "http://127.0.0.1:4735";

chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== "rew_fetch" || typeof msg.path !== "string" || !msg.path.startsWith("/")) {
    return false;
  }
  fetch(REW_BASE + msg.path, { headers: { Accept: "application/json" } })
    .then(async (resp) => {
      const body = await resp.text();
      sendResponse({ ok: resp.ok, status: resp.status, body });
    })
    .catch((err) => {
      sendResponse({ ok: false, status: 0, error: String(err) });
    });
  return true; // keep the channel open for the async response
});
