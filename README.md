# EDNIS

**eDesk & NetSuite** helper — a small always-on-top control panel that reads the
order / tracking / ecom-record details off whatever eDesk ticket you have open and
opens the matching NetSuite records for you, each in its own new tab.

It never launches its own browser and never touches your credentials. It attaches
to a Chrome window **you** started and logged into, over the Chrome DevTools
Protocol (port 9222).

> **Platform: Windows.** The `.bat` launcher scripts are Windows-only, and the UI
> uses the system WebView2 runtime (ships with Windows 10/11). The Python side
> (`pywebview` + `playwright`) is otherwise cross-platform — see
> [macOS / Linux](#macos--linux) to adapt it.

---

## What it does

You open an eDesk ticket in the automation Chrome window, select the tiles for
what you want, and hit **Open Selected**. The app then:

| Tile | What happens |
| --- | --- |
| **Sales Order** | Reads `ORDER NO.` off the ticket, runs NetSuite global search, opens every matching Sales Order in a new tab. |
| **Return Auth** | Opens any Return Authorization tied to that order. Independent of Sales Order — tick it alone to open only the RA, or with Sales Order to open both. |
| **Tracking** | Reads `TRACKING NO.` + carrier off the ticket, opens the carrier's tracking page (USPS / UPS / Amazon / FedEx / DHL). |
| **Ecom Record (Pre-Sales)** | For pre-sales tickets with no order yet: reads the `#number` from the subject, opens the Ecom Record and its PARENT record. |

If more than one ticket tab is open, it asks which one to use — or, for the
Sales Order / Return Auth flow, **Open ALL N tickets** to push every open
ticket's order to NetSuite in one pass. If no order number is found, there's a
manual entry box (Sales Order / RA only).

## Files

| File | Role |
| --- | --- |
| `command_center.py` | Entry point — opens the window (pywebview) and exposes `Api` to the page. |
| `ui.html` | The whole interface: HTML/CSS/JS, ported from the Canva mockup. |
| `assets/` | Bundled pixel font (Pixelify Sans, OFL). |
| `edesk_bridge.py` | Reads order / tracking / ecom numbers off the open eDesk ticket. |
| `netsuite_bridge.py` | Drives NetSuite global search, opens result records in new tabs. |
| `order_parser.py` | Normalizes a raw order number into a NetSuite search query. |
| `chrome_utils.py` | Shared tab-finding / background-tab helpers. |
| `launch_chrome_debug.bat` | Starts Chrome with remote debugging on port 9222. |
| `install.bat` | One-time setup: creates `.venv`, installs dependencies. |
| `run.bat` | Starts the control panel. |
| `requirements.txt` | Python dependencies. |

---

## Quick start (fresh PC)

No Git or Python, but Chrome is installed — the common case:

1. **Download the code** — on the GitHub page: **Code ▾ → Download ZIP**. Extract
   it somewhere like your Desktop.
2. **Set your NetSuite account** — open `launch_chrome_debug.bat` in Notepad and
   replace `3559546` (appears twice) with your real NetSuite account number.
3. **Run `install.bat`** — it installs Python (via `winget`), then asks you to
   close the terminal, reopen it, and run `install.bat` again to finish.
4. **Run `launch_chrome_debug.bat`** — log into NetSuite and eDesk in that window.
5. **Run `run.bat`** — the panel opens.

Steps 4–5 are all you repeat next time. The rest is one-time.

If `winget` is missing (older Windows), install Python manually first — see
[Manual downloads](#manual-downloads-if-you-dont-have-winget).

---

## Prerequisites

You need three things installed on Windows: **Git**, **Python 3.10+**, and
**Google Chrome**. `install.bat` will install Python for you (via `winget`) if
it's missing; the others you install once below. Git is only needed if you
`git clone` instead of downloading the ZIP.

### Using winget (Windows 10/11, recommended)

`winget` ships with modern Windows. Check with `winget --version`; if missing,
install **App Installer** from the Microsoft Store.

```bash
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
winget install --id Google.Chrome -e
```

Close and reopen your terminal afterwards so the updated `PATH` takes effect.

<a name="manual-downloads-if-you-dont-have-winget"></a>
### Manual downloads (if you don't have winget)

| Tool | Download | Notes |
| --- | --- | --- |
| Python 3.10+ | <https://www.python.org/downloads/windows/> | **Tick "Add python.exe to PATH"** in the installer. |
| Git | <https://git-scm.com/download/win> | Defaults are fine. |
| Google Chrome | <https://www.google.com/chrome/> | Regular install. |

Verify:

```bash
python --version
git --version
```

---

## Install

```bash
git clone <this-repo-url>
cd ednis
```

**Quick path** — run the setup script (installs Python via winget if needed,
creates the virtual environment, installs dependencies):

```bash
install.bat
```

If it installs Python, it will tell you to close the window, open a new one, and
run `install.bat` again.

**Manual path** — create a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies (from `requirements.txt`): `playwright`, `pywebview`.

> `playwright install` is **not** needed — the app connects to your existing
> Chrome over CDP and never uses Playwright's bundled browser.
>
> On Windows, `pywebview` uses the built-in **WebView2** runtime (already present
> on Windows 10/11). No extra download.

### One-time config

**Required:** `launch_chrome_debug.bat` — replace `3559546` in the
`https://3559546.app.netsuite.com` start URL (both lines) with your NetSuite
account number. This is the only value you must change; the Python code finds
the NetSuite tab by domain, so it's account-agnostic.

**Only if global search fails:** `netsuite_bridge.py` → `SEARCH_INPUT_SELECTORS`.
It matches the global search box by its `placeholder="Search"`; the tool also
skips NetSuite tabs that don't have a search box (Task / Media Item pages), so
having several NetSuite tabs open is fine. Adjust the selectors only if you get
the *"Could not find the NetSuite global search box"* error.

---

## How to launch

**1. Start the automation Chrome window** (once per session):

```bash
launch_chrome_debug.bat
```

This opens Chrome with `--remote-debugging-port=9222` using a dedicated profile
(`%LOCALAPPDATA%\NetSuiteAutomationProfile`), separate from your normal browser.

**2. Log in** to NetSuite and eDesk in that window. Open your NetSuite dashboard
tab and the eDesk ticket you're working.

**3. Start the app:**

```bash
run.bat
```

or manually:

```bash
.venv\Scripts\activate
python command_center.py
```

A small frameless panel appears, pinned on top — drag it anywhere by the body,
**⚙** for the window-scale (1× / 1.25× / 1.5× / 2×) and opacity (30–100%)
settings, both remembered between runs, **–** to minimize, **✕** to close. It auto-sizes to fit its contents at
any scale (the status line at the bottom grows with longer messages). Tap the
tiles for what you want to open, then click **Open Selected**. For a ticket with
no order number, paste one into the manual box and press Enter.

---

## macOS / Linux

The app runs, but you'll need to replace the three `.bat` files with your own
commands.

**Start the automation Chrome window** (instead of `launch_chrome_debug.bat`):

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.netsuite-automation-profile" \
  "https://YOURACCT.app.netsuite.com"

# Linux
google-chrome --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.netsuite-automation-profile" \
  "https://YOURACCT.app.netsuite.com"
```

**Setup** (instead of `install.bat`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Start the app** (instead of `run.bat`):

```bash
source .venv/bin/activate
python command_center.py
```

`pywebview` uses the OS webview — WebKit (via `pywebview[qt]` or the system
GTK/WebKit) on Linux, the native WebKit on macOS. Install the matching extra if
prompted (`pip install 'pywebview[qt]'` or `[gtk]`). Nothing in the Python code
is Windows-specific.

---

## Troubleshooting

| Message | Fix |
| --- | --- |
| *Can't reach Chrome on port 9222* | Run `launch_chrome_debug.bat` first; keep that window open. |
| *No eDesk tab found* | Open the ticket in the automation Chrome window (not your normal Chrome). |
| *No NetSuite tab found* | Open your NetSuite dashboard in the automation window. |
| *No NetSuite tab with the global search bar* | Open your NetSuite dashboard (or any standard record page) in the automation window — Task / Media Item pages don't have the search bar. |
| *Could not find the NetSuite global search box* | The search selectors need updating for your NetSuite theme — see `SEARCH_INPUT_SELECTORS` in `netsuite_bridge.py`. |
| *No Sales Order result showed up* | Double-check the order number; try the manual entry box. |
