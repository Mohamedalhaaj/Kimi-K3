import base64
import io
import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from ddgs import DDGS
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps, UnidentifiedImageError


APP_DIR = Path(__file__).resolve().parent
MODEL = "moonshotai/kimi-k3-free"
MAX_HISTORY_MESSAGES = 24
MAX_IMAGE_EDGE = 2048
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
MAX_WEB_CONTEXT_CHARS = 24_000
MAX_URLS_PER_MESSAGE = 3

URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
AUTO_BROWSE_TERMS = (
    "search",
    "browse",
    "look up",
    "find online",
    "open this",
    "open the link",
    "latest",
    "today",
    "current",
    "recent",
    "news",
    "verify",
    "source",
    "ابحث",
    "تصفح",
    "افتح",
    "الرابط",
    "آخر",
    "أحدث",
    "اليوم",
    "حالي",
    "أخبار",
    "تحقق",
    "مصدر",
)


def get_api_key() -> str | None:
    """Load the API key locally from .env or from Streamlit Cloud secrets."""
    load_dotenv(APP_DIR / ".env")

    key = os.getenv("TOKENROUTER_API_KEY")
    if key:
        return key

    try:
        secret_key = st.secrets.get("TOKENROUTER_API_KEY")
        return str(secret_key) if secret_key else None
    except Exception:
        return None


st.set_page_config(
    page_title="Kimi Chat",
    page_icon="🌙",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        :root { --kimi-blue: #0171DD; }

        header[data-testid="stHeader"],
        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"],
        #MainMenu,
        footer {
            display: none !important;
            visibility: hidden !important;
        }

        .stApp {
            background:
                radial-gradient(circle at 8% 0%, rgba(1,113,221,.08), transparent 30rem),
                radial-gradient(circle at 92% 100%, rgba(1,113,221,.06), transparent 28rem);
        }

        .block-container {
            max-width: 1040px;
            padding-top: 3.4rem !important;
            padding-bottom: 7.5rem !important;
        }

        section[data-testid="stSidebar"] > div { padding-top: 2rem; }

        .kimi-header {
            display: flex;
            align-items: center;
            gap: .9rem;
            margin: .25rem 0 1.5rem;
        }

        .kimi-mark {
            min-width: 46px;
            width: 46px;
            height: 46px;
            border-radius: 15px;
            display: grid;
            place-items: center;
            background: var(--kimi-blue);
            color: white;
            font-size: 1.35rem;
            font-weight: 700;
            box-shadow: 0 12px 32px rgba(1,113,221,.22);
        }

        .kimi-title {
            margin: 0;
            font-size: 1.85rem;
            line-height: 1.15;
            font-weight: 760;
        }

        .kimi-subtitle {
            margin-top: .28rem;
            opacity: .62;
            font-size: .94rem;
        }

        [data-testid="stChatMessage"] {
            border: 1px solid rgba(128,128,128,.14);
            border-radius: 18px;
            padding: .3rem .45rem;
            box-shadow: 0 7px 24px rgba(0,0,0,.035);
        }

        [data-testid="stChatInput"] { border-radius: 18px; }

        div.stButton > button,
        div.stDownloadButton > button { border-radius: 12px; }

        @media (max-width: 768px) {
            .block-container {
                padding-top: 1.6rem !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }

            .kimi-title { font-size: 1.55rem; }
            .kimi-subtitle { font-size: .84rem; }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def initialize_state() -> None:
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    if "system_prompt" not in st.session_state:
        st.session_state.system_prompt = (
            "You are Kimi, a precise and helpful AI assistant. "
            "Answer in the same language as the user unless asked otherwise. "
            "When web context is supplied, ground factual claims in it and cite "
            "the numbered sources using [1], [2], and so on. Never invent sources."
        )

    if "web_mode" not in st.session_state:
        st.session_state.web_mode = "Automatic"

    if "browse_depth" not in st.session_state:
        st.session_state.browse_depth = "Fast"


def encode_uploaded_image(uploaded_file: Any) -> dict[str, str]:
    """Normalize, resize, and encode an uploaded image for the model."""
    raw = uploaded_file.getvalue()

    try:
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"{uploaded_file.name} is not a readable image.") from exc

    output = io.BytesIO()
    has_transparency = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )

    if has_transparency:
        image = image.convert("RGBA")
        image.save(output, format="PNG", optimize=True)
        mime_type = "image/png"
    else:
        image = image.convert("RGB")
        image.save(output, format="JPEG", quality=88, optimize=True)
        mime_type = "image/jpeg"

    processed = output.getvalue()
    encoded = base64.b64encode(processed).decode("utf-8")

    return {
        "name": uploaded_file.name,
        "mime_type": mime_type,
        "base64": encoded,
        "data_url": f"data:{mime_type};base64,{encoded}",
        "byte_size": str(len(processed)),
    }


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []

    for match in URL_PATTERN.findall(text):
        cleaned = match.rstrip(".,;:!?)]}'\"")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)

    return urls[:MAX_URLS_PER_MESSAGE]


def is_public_web_url(url: str) -> bool:
    """Reject local/private network targets before opening a user-supplied URL."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False

        hostname = parsed.hostname.lower()
        if hostname in {"localhost", "localhost.localdomain"}:
            return False

        addresses = socket.getaddrinfo(hostname, None)
        if not addresses:
            return False

        for address in addresses:
            ip_text = address[4][0]
            ip = ipaddress.ip_address(ip_text)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False

        return True
    except (OSError, ValueError):
        return False


def should_browse(text: str, mode: str) -> bool:
    if mode == "Off":
        return False

    if mode == "Always":
        return True

    if extract_urls(text):
        return True

    lowered = text.casefold()
    return any(term in lowered for term in AUTO_BROWSE_TERMS)


def safe_title(value: Any, fallback: str) -> str:
    title = str(value or "").strip()
    return title[:180] if title else fallback


def open_web_page(ddgs: DDGS, url: str) -> tuple[str, str | None]:
    if not is_public_web_url(url):
        return "", "The URL is not a permitted public web address."

    try:
        result = ddgs.extract(url, fmt="text_markdown")
        content = str(result.get("content", "")).strip()
        if not content:
            return "", "The page returned no readable text."
        return content[:12_000], None
    except Exception as error:
        return "", f"Could not open the page: {error}"


def browse_web(
    query: str,
    mode: str,
    depth: str,
) -> tuple[str, list[dict[str, str]], list[str]]:
    """Search the web and/or open links, returning model context and sources."""
    if not should_browse(query, mode):
        return "", [], []

    urls = extract_urls(query)
    sources: list[dict[str, str]] = []
    context_sections: list[str] = []
    warnings: list[str] = []

    try:
        ddgs = DDGS(timeout=10)

        for url in urls:
            content, warning = open_web_page(ddgs, url)
            source_number = len(sources) + 1
            parsed = urlparse(url)
            title = parsed.netloc or url
            sources.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": "Direct link supplied by the user.",
                }
            )

            if content:
                context_sections.append(
                    f"[{source_number}] {title}\nURL: {url}\n"
                    f"PAGE CONTENT:\n{content}"
                )
            elif warning:
                warnings.append(f"{url}: {warning}")

        clean_query = URL_PATTERN.sub(" ", query)
        clean_query = re.sub(r"\s+", " ", clean_query).strip()

        explicit_search_intent = any(
            term in query.casefold() for term in AUTO_BROWSE_TERMS
        )
        run_search = (
            not urls
            or mode == "Always"
            or (explicit_search_intent and len(clean_query) >= 3)
        )

        if run_search:
            search_query = clean_query or query
            results = ddgs.text(
                search_query,
                region="us-en",
                safesearch="moderate",
                max_results=5,
                backend="auto",
            )

            for result in results or []:
                url = str(result.get("href") or result.get("url") or "").strip()
                if not url or any(source["url"] == url for source in sources):
                    continue

                title = safe_title(result.get("title"), urlparse(url).netloc)
                snippet = str(result.get("body") or result.get("snippet") or "").strip()
                source_number = len(sources) + 1

                source = {
                    "title": title,
                    "url": url,
                    "snippet": snippet[:700],
                }
                sources.append(source)

                section = (
                    f"[{source_number}] {title}\nURL: {url}\n"
                    f"SEARCH SNIPPET:\n{snippet[:1_500]}"
                )

                if depth == "Deep" and source_number <= 3:
                    page_content, warning = open_web_page(ddgs, url)
                    if page_content:
                        section += f"\nPAGE CONTENT:\n{page_content}"
                    elif warning:
                        warnings.append(f"{url}: {warning}")

                context_sections.append(section)

                if len(sources) >= 7:
                    break

    except Exception as error:
        warnings.append(f"Web browsing failed: {error}")

    web_context = "\n\n---\n\n".join(context_sections)
    web_context = web_context[:MAX_WEB_CONTEXT_CHARS]

    if web_context:
        web_context = (
            "WEB CONTEXT\n"
            "Use only the following numbered sources for web-grounded claims. "
            "Cite them inline as [1], [2], etc. If the sources do not answer the "
            "question, say so explicitly.\n\n"
            f"{web_context}"
        )

    return web_context, sources, warnings


def api_message_from_ui_message(message: dict[str, Any]) -> dict[str, Any]:
    if message["role"] == "assistant":
        return {"role": "assistant", "content": message["text"]}

    images = message.get("images", [])
    text = message.get("text", "").strip()
    web_context = message.get("web_context", "").strip()

    combined_text = text
    if web_context:
        combined_text = f"{text}\n\n{web_context}".strip()

    if not images:
        return {"role": "user", "content": combined_text}

    content: list[dict[str, Any]] = [
        {
            "type": "image_url",
            "image_url": {"url": image["data_url"]},
        }
        for image in images
    ]
    content.append(
        {
            "type": "text",
            "text": combined_text
            or "Describe and analyze the uploaded image or images.",
        }
    )

    return {"role": "user", "content": content}


def build_api_messages() -> list[dict[str, Any]]:
    recent_messages = st.session_state.chat_messages[-MAX_HISTORY_MESSAGES:]
    return [
        {"role": "system", "content": st.session_state.system_prompt},
        *[api_message_from_ui_message(message) for message in recent_messages],
    ]


def render_images(images: list[dict[str, str]]) -> None:
    if not images:
        return

    columns = st.columns(min(len(images), 3))
    for index, image in enumerate(images):
        image_bytes = base64.b64decode(image["base64"])
        with columns[index % len(columns)]:
            st.image(
                image_bytes,
                caption=image["name"],
                use_container_width=True,
            )


def render_sources(
    sources: list[dict[str, str]],
    warnings: list[str] | None = None,
) -> None:
    if not sources and not warnings:
        return

    with st.expander(
        f"Web sources ({len(sources)})",
        expanded=False,
    ):
        for index, source in enumerate(sources, start=1):
            st.markdown(f"**[{index}] [{source['title']}]({source['url']})**")
            if source.get("snippet"):
                st.caption(source["snippet"])

        for warning in warnings or []:
            st.warning(warning)


def conversation_markdown() -> str:
    lines = ["# Kimi conversation", ""]
    for message in st.session_state.chat_messages:
        speaker = "You" if message["role"] == "user" else "Kimi"
        lines.append(f"## {speaker}")

        for image in message.get("images", []):
            lines.append(f"*Attached image: {image['name']}*")

        lines.append(message.get("text", ""))

        for index, source in enumerate(message.get("web_sources", []), start=1):
            lines.append(f"[{index}] {source['title']}: {source['url']}")

        lines.append("")

    return "\n".join(lines)


initialize_state()
API_KEY = get_api_key()

st.markdown(
    """
    <div class="kimi-header">
        <div class="kimi-mark">K</div>
        <div>
            <div class="kimi-title">Kimi Chat</div>
            <div class="kimi-subtitle">
                Text, image and web analysis through TokenRouter
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not API_KEY:
    st.error(
        "TOKENROUTER_API_KEY was not found. For local use, keep it in .env. "
        "For Streamlit Community Cloud, add it under App settings → Secrets."
    )
    st.code('TOKENROUTER_API_KEY = "sk-..."', language="toml")
    st.stop()

client = OpenAI(
    base_url="https://api.tokenrouter.com/v1",
    api_key=API_KEY,
    timeout=180.0,
)

with st.sidebar:
    st.subheader("Chat controls")

    if st.button("＋ New chat", use_container_width=True):
        st.session_state.chat_messages = []
        st.rerun()

    st.text_area(
        "Assistant instructions",
        key="system_prompt",
        height=125,
        help="These instructions apply to the whole conversation.",
    )

    st.divider()
    st.subheader("Web browsing")

    st.selectbox(
        "Web mode",
        options=["Automatic", "Always", "Off"],
        key="web_mode",
        help=(
            "Automatic opens pasted links and searches for requests involving "
            "current information. Always searches every message."
        ),
    )

    st.radio(
        "Browse depth",
        options=["Fast", "Deep"],
        key="browse_depth",
        horizontal=True,
        help=(
            "Fast uses search snippets and opens pasted links. "
            "Deep also opens the first search results and is slower."
        ),
    )

    st.caption("Paste a public URL into the chat and Kimi will read it.")
    st.caption(f"Model: `{MODEL}`")
    st.caption("Images are resized locally before being sent.")

    st.download_button(
        "Download conversation",
        data=conversation_markdown(),
        file_name="kimi-conversation.md",
        mime="text/markdown",
        use_container_width=True,
        disabled=not st.session_state.chat_messages,
    )

for message in st.session_state.chat_messages:
    avatar = "🧑" if message["role"] == "user" else "🌙"
    with st.chat_message(message["role"], avatar=avatar):
        render_images(message.get("images", []))
        if message.get("text"):
            st.markdown(message["text"])
        render_sources(
            message.get("web_sources", []),
            message.get("web_warnings", []),
        )

if not st.session_state.chat_messages:
    st.info(
        "Ask a question, paste a link, search the web, or attach images using "
        "the + button inside the message box."
    )

submission = st.chat_input(
    "Message Kimi, paste a link, or attach images…",
    accept_file="multiple",
    file_type=["jpg", "jpeg", "png", "webp"],
    max_upload_size=15,
    submit_mode="disable",
)

if submission:
    user_text = submission.text.strip()
    uploaded_files = list(submission.files)

    if not user_text and not uploaded_files:
        st.stop()

    processed_images: list[dict[str, str]] = []
    total_processed_bytes = 0

    try:
        for uploaded_file in uploaded_files:
            image = encode_uploaded_image(uploaded_file)
            total_processed_bytes += int(image["byte_size"])
            if total_processed_bytes > MAX_TOTAL_IMAGE_BYTES:
                raise ValueError(
                    "The processed images are too large together. "
                    "Please send fewer images."
                )
            processed_images.append(image)
    except ValueError as error:
        st.error(str(error))
        st.stop()

    with st.chat_message("user", avatar="🧑"):
        render_images(processed_images)
        if user_text:
            st.markdown(user_text)

    web_context = ""
    web_sources: list[dict[str, str]] = []
    web_warnings: list[str] = []

    if user_text and should_browse(user_text, st.session_state.web_mode):
        with st.status("Browsing the web…", expanded=False) as status:
            web_context, web_sources, web_warnings = browse_web(
                user_text,
                st.session_state.web_mode,
                st.session_state.browse_depth,
            )
            if web_sources:
                status.update(
                    label=f"Found {len(web_sources)} web source(s)",
                    state="complete",
                )
            else:
                status.update(
                    label="No usable web sources found",
                    state="error",
                )

    user_message = {
        "role": "user",
        "text": user_text,
        "images": processed_images,
        "web_context": web_context,
    }
    st.session_state.chat_messages.append(user_message)

    with st.chat_message("assistant", avatar="🌙"):
        response_placeholder = st.empty()
        full_response = ""

        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=build_api_messages(),
                stream=True,
                max_tokens=1_500,
            )

            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)

                if content:
                    full_response += content
                    response_placeholder.markdown(full_response + "▌")

            if not full_response:
                full_response = "The model returned no visible answer. Please try again."

            response_placeholder.markdown(full_response)
            render_sources(web_sources, web_warnings)

        except Exception as error:
            full_response = (
                "I could not complete that request.\n\n"
                f"**API error:** `{error}`"
            )
            response_placeholder.error(full_response)
            render_sources(web_sources, web_warnings)

    st.session_state.chat_messages.append(
        {
            "role": "assistant",
            "text": full_response,
            "images": [],
            "web_sources": web_sources,
            "web_warnings": web_warnings,
        }
    )
