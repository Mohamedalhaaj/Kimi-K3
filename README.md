# Kimi Workspace

A Streamlit AI workspace powered by TokenRouter. It supports:

- Streaming chat with configurable TokenRouter model IDs
- Fast, Balanced and Deep response modes
- Web search and public-link reading with numbered sources
- Isolated local Chromium rendering through Playwright
- Explicit browser commands for opening, inspecting and navigating public pages
- Image understanding
- PDF, Word, PowerPoint, text, code, CSV, JSON and Excel analysis
- Safe calculator command: `/calc 25*4`
- Conversation export/import and Word response downloads

## Local setup

```bash
cd ~/Documents/Kimi-K3
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Playwright browser binaries are installed separately from the Python package. Run the Chromium installation command again after major Playwright upgrades if the browser version changes.

Create `.env`:

```env
TOKENROUTER_API_KEY=YOUR_KEY
```

Run:

```bash
python -m streamlit run app.py
```

## Local browser commands

The browser uses a separate local Chromium profile under `~/.kimi-workspace`. It does not control your existing Chrome window.

```text
/browser open https://example.com
/browser inspect
/browser links
/browser click Visible link or button text
/browser type Search :: Libya news
/browser reload
/browser back
```

The app also uses Playwright automatically as a fallback when normal HTTP/Jina page extraction fails.

For safety, the browser refuses to type passwords, PINs, payment details and one-time codes. It also blocks clicks that clearly indicate purchases, payments, deletions, publishing, transfers or other consequential actions. Typing does not submit a form automatically.

Set this in `.env` to show the isolated browser window while actions run:

```env
KIMI_BROWSER_HEADLESS=false
```

The default is headless mode.

## Streamlit Community Cloud

Deploy `app.py` from the `main` branch and add this secret in App settings:

```toml
TOKENROUTER_API_KEY = "YOUR_KEY"
```

Never commit `.env` or `.streamlit/secrets.toml`.

The Playwright browser agent is intended primarily for local use. Streamlit Community Cloud requires additional browser installation and system-dependency configuration, and browser state is not guaranteed to persist there.

## Browser scope

The app can search the web, read public HTTP/HTTPS pages and use an isolated local Chromium browser. It does not control the user's personal browser, enter credentials, solve CAPTCHAs, complete payments, submit purchases or perform other sensitive or consequential actions.
