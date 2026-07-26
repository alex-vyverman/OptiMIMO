# Chrome Web Store listing — OptiMIMO REW Bridge

- Extension ID: `oachlggjgkbbahocplendbppodahmpid`
- Listing URL: https://chromewebstore.google.com/detail/oachlggjgkbbahocplendbppodahmpid

## Dev testing (unpacked)

The store rejects manifests with a `key` field, so `manifest.json` has
none and unpacked builds get an unstable path-derived ID. For a stable
dev ID (`moojndmfeecbgpfpkpnilhmcbioojpmo`, in the webapp's fallback
list), copy `manifest.dev.json` over `manifest.json` before "Load
unpacked":

    cp manifest.dev.json manifest.json

Restore (`git checkout -- manifest.json`) before rebuilding the store
zip; `build_zip.sh` excludes `manifest.dev.json` either way.

## Short summary (max 132 chars)

Connects optimimo.app to REW (Room EQ Wizard) on your computer, so you can import acoustic measurements directly in the browser.

## Detailed description

OptiMIMO (https://optimimo.app) is a browser-based MIMO room-correction
solver: it computes multi-speaker FIR filters from acoustic measurements
— entirely on your device, using WebAssembly.

REW (Room EQ Wizard) is where those measurements usually live. Browsers
deliberately block public websites from reaching software on your own
computer, so this tiny bridge does exactly one thing: it relays requests
from optimimo.app to the REW API running on your machine
(127.0.0.1:4735). Measurements then flow straight from REW into the
solver — no file exports, no Python, no local servers.

What it does
  • Lets the Measurements page on optimimo.app list your REW measurements
    and import their impulse responses for filter computation.

What it does not do
  • No data ever leaves your computer. The bridge only talks to REW on
    127.0.0.1; nothing is sent to optimimo.app's servers or anywhere else.
  • No analytics, no tracking, no cookies, no accounts.
  • No access to your browsing, files, or any other website.

Requirements
  • REW (Room EQ Wizard) running on this computer with its API enabled
    (REW → Preferences → API → Start).
  • The OptiMIMO web app at https://optimimo.app.

How to use
  1. Install the bridge.
  2. Start REW and enable its API.
  3. Open optimimo.app → Measurements → REW Import → Fetch Measurements.

OptiMIMO and this bridge are open source:
https://github.com/alex-vyverman/OptiMIMO

## Permission justifications (dashboard "Privacy practices" tab)

- **host_permissions: http://127.0.0.1:4735/* and http://localhost:4735/***
  Required — this is REW's local API port. It is the only host the
  extension can contact, and it is the entire point of the extension:
  reading measurement metadata and impulse responses from REW.

- **externally_connectable**
  Required so that the optimimo.app page (and local development servers)
  can ask the bridge for REW data via chrome.runtime messages. Any page
  not in the list cannot contact the extension.

- **Remote code**: No remote code is used. The extension ships all its
  code; it only proxies JSON from the local REW API.

- **Data collection**: None. No user data is collected, transmitted, or
  stored anywhere. Measurement data moves only between the local REW
  process and the browser tab displaying optimimo.app, over loopback.

## Data safety answers (if asked)

- Does the extension collect or transmit user data? **No.**
- Is data processed off the user's device? **No** — all traffic is to
  127.0.0.1 (the user's own machine).
- Data types accessed: acoustic measurement data from REW (user-provided,
  stays on device).
