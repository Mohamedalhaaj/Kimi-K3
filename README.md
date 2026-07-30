# Kimi K3 Streamlit Chat

A simple Streamlit chat interface for `moonshotai/kimi-k3-free` through TokenRouter, with text chat, multiple-image upload, streaming responses, editable assistant instructions, and conversation export.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a local `.env` file:

```env
TOKENROUTER_API_KEY=YOUR_TOKENROUTER_KEY
```

Run:

```bash
python -m streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. In Streamlit Community Cloud, create a new app from this repository.
2. Select branch `main`.
3. Set the main file path to `app.py`.
4. Open **App settings → Secrets**.
5. Add:

```toml
TOKENROUTER_API_KEY = "YOUR_TOKENROUTER_KEY"
```

Never commit the API key to GitHub. The repository ignores `.env` and `.streamlit/secrets.toml`.

## Image support

Use the attachment button in the chat input. Supported formats:

- JPG / JPEG
- PNG
- WEBP

Images are resized locally before being sent to reduce request size.
