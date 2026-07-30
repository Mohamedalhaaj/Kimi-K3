import base64
import io
import os
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps, UnidentifiedImageError


APP_DIR = Path(__file__).resolve().parent
MODEL = "moonshotai/kimi-k3-free"
MAX_HISTORY_MESSAGES = 24
MAX_IMAGE_EDGE = 2048
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024


def get_api_key() -> str | None:
    """Load the API key locally from .env or on Streamlit Cloud from secrets."""
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
        :root {
            --kimi-blue: #0171DD;
        }

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
                radial-gradient(circle at 8% 0%, rgba(1, 113, 221, .08), transparent 30rem),
                radial-gradient(circle at 92% 100%, rgba(1, 113, 221, .06), transparent 28rem);
        }

        .block-container {
            max-width: 1040px;
            padding-top: 3.4rem !important;
            padding-bottom: 7.5rem !important;
        }

        section[data-testid="stSidebar"] > div {
            padding-top: 2rem;
        }

        .kimi-header {
            display: flex;
            align-items: center;
            gap: .9rem;
            margin: .25rem 0 1.5rem 0;
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
            box-shadow: 0 12px 32px rgba(1, 113, 221, .22);
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
            border: 1px solid rgba(128, 128, 128, .14);
            border-radius: 18px;
            padding: .3rem .45rem;
            box-shadow: 0 7px 24px rgba(0, 0, 0, .035);
        }

        [data-testid="stChatInput"] {
            border-radius: 18px;
        }

        div.stButton > button,
        div.stDownloadButton > button {
            border-radius: 12px;
        }

        @media (max-width: 768px) {
            .block-container {
                padding-top: 1.6rem !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }

            .kimi-title {
                font-size: 1.55rem;
            }

            .kimi-subtitle {
                font-size: .84rem;
            }
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
            "Answer in the same language as the user unless asked otherwise."
        )


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


def api_message_from_ui_message(message: dict[str, Any]) -> dict[str, Any]:
    if message["role"] == "assistant":
        return {"role": "assistant", "content": message["text"]}

    images = message.get("images", [])
    text = message.get("text", "").strip()

    if not images:
        return {"role": "user", "content": text}

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
            "text": text or "Describe and analyze the uploaded image or images.",
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


def conversation_markdown() -> str:
    lines = ["# Kimi conversation", ""]
    for message in st.session_state.chat_messages:
        speaker = "You" if message["role"] == "user" else "Kimi"
        lines.append(f"## {speaker}")
        for image in message.get("images", []):
            lines.append(f"*Attached image: {image['name']}*")
        lines.append(message.get("text", ""))
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
            <div class="kimi-subtitle">Text and image analysis through TokenRouter</div>
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

if not st.session_state.chat_messages:
    st.info(
        "Ask a question, or attach one or more images using the + button "
        "inside the message box."
    )

submission = st.chat_input(
    "Message Kimi or attach images…",
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

    user_message = {
        "role": "user",
        "text": user_text,
        "images": processed_images,
    }
    st.session_state.chat_messages.append(user_message)

    with st.chat_message("user", avatar="🧑"):
        render_images(processed_images)
        if user_text:
            st.markdown(user_text)

    with st.chat_message("assistant", avatar="🌙"):
        response_placeholder = st.empty()
        full_response = ""

        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=build_api_messages(),
                stream=True,
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

        except Exception as error:
            full_response = (
                "I could not complete that request.\n\n"
                f"**API error:** `{error}`"
            )
            response_placeholder.error(full_response)

    st.session_state.chat_messages.append(
        {
            "role": "assistant",
            "text": full_response,
            "images": [],
        }
    )
