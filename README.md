# Kimi Workspace

A Streamlit AI workspace powered by TokenRouter. It supports:

- Streaming chat with configurable TokenRouter model IDs
- Fast, Balanced and Deep response modes
- Web search and public-link reading with numbered sources
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
```

Create `.env`:

```env
TOKENROUTER_API_KEY=YOUR_KEY
```

Run:

```bash
python -m streamlit run app.py
```

## Streamlit Community Cloud

Deploy `app.py` from the `main` branch and add this secret in App settings:

```toml
TOKENROUTER_API_KEY = "YOUR_KEY"
```

Never commit `.env` or `.streamlit/secrets.toml`.

## Browser scope

The app can search the web and read public HTTP/HTTPS pages. It deliberately does not enter passwords or PINs, solve CAPTCHAs, sign into accounts, submit forms, or control the user's personal browser.
