from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

from core.calculator import calculate
from core.export_tools import (
    answer_to_docx,
    conversation_to_json,
    conversation_to_markdown,
)
from core.file_tools import ParsedUpload, build_attachment_context, parse_upload
from core.web_tools import ToolResult, browse_web, should_browse


APP_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "moonshotai/kimi-k3-free"
SUPPORTED_FILE_TYPES = [
    "jpg",
    "jpeg",
    "png",
    "webp",
    "pdf",
    "docx",
    "pptx",
    "txt",
    "md",
    "markdown",
    "csv",
    "xlsx",
    "xlsm",
    "json",
    "py",
    "js",
    "ts",
    "tsx",
    "jsx",
    "html",
    "css",
    "xml",
    "yaml",
    "yml",
    "srt",
    "vtt",
    "log",
]
MODE_SETTINGS = {
    "Fast": {
        "history": 8,
        "max_tokens": 850,
        "max_web_context": 18_000,
        "label": "Lowest latency",
    },
    "Balanced": {
        "history": 16,
        "max_tokens": 1_500,
        "max_web_context": 32_000,
        "label": "Best default",
    },
    "Deep": {
        "history": 24,
        "max_tokens": 2_500,
        "max_web_context": 55_000,
        "label": "More research, slower",
    },
}
CALC_PATTERN = re.compile(r"^\s*/(?:calc|calculate)\s+(.+)$", re.IGNORECASE | re.DOTALL)


st.set_page_config(
    page_title="Kimi Workspace",
    page_icon="🌙",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        :root {
            --brand: #0171DD;
            --surface-border: rgba(128, 128, 128, .15);
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
                radial-gradient(circle at 5% -5%, rgba(1,113,221,.09), transparent 31rem),
                radial-gradient(circle at 100% 100%, rgba(1,113,221,.06), transparent 30rem);
        }

        .block-container {
            max-width: 1080px;
            padding-top: 2.8rem !important;
            padding-bottom: 8rem !important;
        }

        section[data-testid="stSidebar"] > div {
            padding-top: 1.65rem;
        }

        .workspace-header {
            display: flex;
            align-items: center;
            gap: .9rem;
            margin: .35rem 0 1.35rem;
        }

        .workspace-logo {
            min-width: 48px;
            width: 48px;
            height: 48px;
            display: grid;
            place-items: center;
            border-radius: 16px;
            background: var(--brand);
            color: white;
            font-weight: 780;
            font-size: 1.35rem;
            box-shadow: 0 14px 34px rgba(1,113,221,.23);
        }

        .workspace-title {
            font-size: 1.85rem;
            font-weight: 770;
            line-height: 1.12;
            margin: 0;
        }

        .workspace-subtitle {
            opacity: .61;
            margin-top: .28rem;
            font-size: .94rem;
        }

        [data-testid="stChatMessage"] {
            border: 1px solid var(--surface-border);
            border-radius: 19px;
            padding: .35rem .5rem;
            box-shadow: 0 8px 28px rgba(0,0,0,.032);
        }

        [data-testid="stChatInput"] {
            border-radius: 19px;
        }

        div.stButton > button,
        div.stDownloadButton > button {
            border-radius: 12px;
        }

        .capability-card {
            border: 1px solid var(--surface-border);
            border-radius: 16px;
            padding: 1rem 1.05rem;
            min-height: 115px;
            background: rgba(255,255,255,.34);
        }

        .capability-title {
            font-weight: 700;
            margin-bottom: .35rem;
        }

        .capability-copy {
            opacity: .68;
            font-size: .9rem;
            line-height: 1.45;
        }

        .attachment-chip {
            display: inline-block;
            border: 1px solid var(--surface-border);
            border-radius: 999px;
            padding: .25rem .58rem;
            margin: 0 .3rem .32rem 0;
            font-size: .78rem;
            opacity: .78;
        }

        @media (max-width: 768px) {
            .block-container {
                padding-top: 1.3rem !important;
                padding-left: .85rem !important;
                padding-right: .85rem !important;
            }
            .workspace-title { font-size: 1.5rem; }
            .workspace-subtitle { font-size: .82rem; }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_secret(name: str) -> str | None:
    load_dotenv(APP_DIR / ".env")
    value = os.getenv(name)
    if value:
        return value
    try:
        secret = st.secrets.get(name)
        return str(secret) if secret else None
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def create_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://api.tokenrouter.com/v1",
        api_key=api_key,
        timeout=180.0,
        max_retries=1,
    )


def initialize_state() -> None:
    defaults: dict[str, Any] = {
        "messages": [],
        "system_prompt": (
            "You are Kimi Workspace, a precise, practical AI assistant. Answer in "
            "the same language as the user unless asked otherwise. You may receive "
            "results from web, file, image, and calculator tools. Use those results "
            "directly instead of claiming you cannot access them. Cite web sources "
            "with [1], [2], etc. Never invent sources. Distinguish clearly between "
            "what is verified, inferred, and unavailable. You can search and read "
            "public web pages, but you cannot sign in, enter credentials or PINs, "
            "solve CAPTCHAs, or control the user's personal browser."
        ),
        "model_id": DEFAULT_MODEL,
        "mode": "Fast",
        "web_mode": "Automatic",
        "browse_depth": "Fast",
        "web_results": 5,
        "show_tool_details": True,
        "pending_prompt": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_attachment_dict(upload: ParsedUpload) -> dict[str, Any]:
    return {
        "name": upload.name,
        "kind": upload.kind,
        "summary": upload.summary,
        "image_base64": upload.image_base64,
        "image_data_url": upload.image_data_url,
        "mime_type": upload.mime_type,
        "size_bytes": upload.size_bytes,
    }


def render_attachments(attachments: list[dict[str, Any]]) -> None:
    if not attachments:
        return

    image_attachments = [item for item in attachments if item.get("kind") == "image"]
    other_attachments = [item for item in attachments if item.get("kind") != "image"]

    if image_attachments:
        columns = st.columns(min(len(image_attachments), 3))
        for index, item in enumerate(image_attachments):
            image_bytes = base64.b64decode(item["image_base64"])
            with columns[index % len(columns)]:
                st.image(
                    image_bytes,
                    caption=item.get("name", "Image"),
                    use_container_width=True,
                )

    if other_attachments:
        chips = "".join(
            f'<span class="attachment-chip">📎 {item.get("name", "file")}</span>'
            for item in other_attachments
        )
        st.markdown(chips, unsafe_allow_html=True)


def render_sources(
    sources: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> None:
    if not sources and not warnings:
        return

    with st.expander(f"Sources and browser details ({len(sources)})", expanded=False):
        for index, source in enumerate(sources, start=1):
            title = source.get("title") or source.get("url") or "Source"
            url = source.get("url", "")
            st.markdown(f"**[{index}] [{title}]({url})**")
            if source.get("snippet"):
                st.caption(source["snippet"])
        for warning in warnings or []:
            st.warning(warning)


def render_tool_events(events: list[str]) -> None:
    if not events or not st.session_state.show_tool_details:
        return
    with st.expander("Tool activity", expanded=False):
        for event in events:
            st.markdown(f"- {event}")


def user_content_for_api(
    message: dict[str, Any],
    include_images: bool,
) -> str | list[dict[str, Any]]:
    text_parts = [message.get("text", "").strip()]
    if message.get("attachment_context"):
        text_parts.append(message["attachment_context"])
    if message.get("tool_context"):
        text_parts.append(message["tool_context"])
    combined_text = "\n\n".join(part for part in text_parts if part).strip()

    images = [
        item
        for item in message.get("attachments", [])
        if item.get("kind") == "image" and item.get("image_data_url")
    ]
    if not include_images or not images:
        return combined_text or "[No text was supplied.]"

    content: list[dict[str, Any]] = [
        {
            "type": "image_url",
            "image_url": {"url": item["image_data_url"]},
        }
        for item in images
    ]
    content.append(
        {
            "type": "text",
            "text": combined_text or "Analyze the attached image or images.",
        }
    )
    return content


def build_api_messages() -> list[dict[str, Any]]:
    config = MODE_SETTINGS[st.session_state.mode]
    history = st.session_state.messages[-config["history"] :]
    now = datetime.now().astimezone()
    system = (
        f"{st.session_state.system_prompt}\n\n"
        f"Current date and time: {now.isoformat(timespec='minutes')}.\n"
        f"Response mode: {st.session_state.mode}."
    )
    api_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

    last_user_indexes = [
        index for index, message in enumerate(history) if message.get("role") == "user"
    ][-2:]

    for index, message in enumerate(history):
        role = message.get("role")
        if role == "assistant":
            api_messages.append({"role": "assistant", "content": message.get("text", "")})
        elif role == "user":
            api_messages.append(
                {
                    "role": "user",
                    "content": user_content_for_api(
                        message,
                        include_images=index in last_user_indexes,
                    ),
                }
            )
    return api_messages


def parse_uploaded_files(files: list[Any]) -> tuple[list[ParsedUpload], list[str]]:
    parsed: list[ParsedUpload] = []
    warnings: list[str] = []
    for uploaded_file in files:
        try:
            parsed.append(parse_upload(uploaded_file))
        except Exception as exc:
            warnings.append(f"{uploaded_file.name}: {exc}")
    return parsed, warnings


def run_tools(user_text: str) -> ToolResult:
    result = ToolResult()

    calculation_match = CALC_PATTERN.match(user_text)
    if calculation_match:
        expression = calculation_match.group(1)
        try:
            value = calculate(expression)
            result.context = (
                "CALCULATOR TOOL RESULT\n"
                f"Expression: {expression}\nResult: {value}"
            )
            result.events.append(f"Calculated: {expression} = {value}")
        except Exception as exc:
            result.warnings.append(f"Calculator error: {exc}")
        return result

    config = MODE_SETTINGS[st.session_state.mode]
    depth = "Deep" if st.session_state.mode == "Deep" else st.session_state.browse_depth

    if should_browse(user_text, st.session_state.web_mode):
        return browse_web(
            query=user_text,
            mode=st.session_state.web_mode,
            depth=depth,
            max_results=st.session_state.web_results,
            max_context_chars=config["max_web_context"],
        )
    return result


def source_dicts(result: ToolResult) -> list[dict[str, Any]]:
    return [asdict(source) for source in result.sources]


def import_conversation(uploaded_json: Any) -> None:
    value = json.loads(uploaded_json.getvalue().decode("utf-8"))
    messages = value.get("messages") if isinstance(value, dict) else None
    if not isinstance(messages, list):
        raise ValueError("The JSON file does not contain a messages list.")

    imported: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        imported.append(
            {
                "role": item["role"],
                "text": str(item.get("text", "")),
                "attachments": item.get("attachments", []),
                "sources": item.get("sources", []),
                "created_at": item.get("created_at") or utc_now(),
            }
        )
    st.session_state.messages = imported


initialize_state()
api_key = get_secret("TOKENROUTER_API_KEY")

st.markdown(
    """
    <div class="workspace-header">
        <div class="workspace-logo">K</div>
        <div>
            <div class="workspace-title">Kimi Workspace</div>
            <div class="workspace-subtitle">
                Chat, web research, link reading, documents, spreadsheets and images
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not api_key:
    st.error(
        "TOKENROUTER_API_KEY was not found. Add it to `.env` locally or to "
        "Streamlit App settings → Secrets."
    )
    st.code('TOKENROUTER_API_KEY = "sk-..."', language="toml")
    st.stop()

client = create_client(api_key)

with st.sidebar:
    st.subheader("Workspace")
    if st.button("＋ New chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.selectbox(
        "Response mode",
        options=list(MODE_SETTINGS),
        key="mode",
        format_func=lambda value: f"{value} — {MODE_SETTINGS[value]['label']}",
    )

    st.text_input(
        "TokenRouter model ID",
        key="model_id",
        help="Use any model ID available in your TokenRouter account.",
    )

    st.divider()
    st.subheader("Tools")
    st.selectbox(
        "Web browsing",
        options=["Automatic", "Always", "Off"],
        key="web_mode",
        help="Automatic searches for current-information requests and opens pasted links.",
    )
    st.radio(
        "Browse depth",
        options=["Fast", "Deep"],
        key="browse_depth",
        horizontal=True,
        disabled=st.session_state.mode == "Deep",
    )
    st.slider("Search results", min_value=3, max_value=8, key="web_results")
    st.toggle("Show tool details", key="show_tool_details")
    st.caption(
        "The browser can search and read public pages. It does not log in, type PINs, "
        "submit forms, or control your personal browser."
    )

    st.divider()
    with st.expander("Assistant instructions", expanded=False):
        st.text_area(
            "System prompt",
            key="system_prompt",
            height=220,
            label_visibility="collapsed",
        )

    with st.expander("Import or export", expanded=False):
        imported_file = st.file_uploader(
            "Import conversation JSON",
            type=["json"],
            accept_multiple_files=False,
        )
        if imported_file and st.button("Import conversation", use_container_width=True):
            try:
                import_conversation(imported_file)
                st.success("Conversation imported.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        markdown_export = conversation_to_markdown(st.session_state.messages)
        json_export = conversation_to_json(st.session_state.messages)
        st.download_button(
            "Download Markdown",
            markdown_export,
            file_name="kimi-conversation.md",
            mime="text/markdown",
            use_container_width=True,
            disabled=not st.session_state.messages,
        )
        st.download_button(
            "Download JSON",
            json_export,
            file_name="kimi-conversation.json",
            mime="application/json",
            use_container_width=True,
            disabled=not st.session_state.messages,
        )

for message in st.session_state.messages:
    avatar = "🧑" if message.get("role") == "user" else "🌙"
    with st.chat_message(message.get("role", "assistant"), avatar=avatar):
        render_attachments(message.get("attachments", []))
        if message.get("text"):
            st.markdown(message["text"])
        render_tool_events(message.get("tool_events", []))
        render_sources(message.get("sources", []), message.get("warnings", []))

if not st.session_state.messages:
    st.info(
        "Ask a question, paste a public link, attach files, or use `/calc 25*4`. "
        "Web browsing runs automatically when the request needs current information."
    )
    columns = st.columns(3)
    cards = [
        (
            "🌐 Web research",
            "Search recent information, open public links and return cited sources.",
        ),
        (
            "📄 File analysis",
            "Read PDF, Word, PowerPoint, text, CSV and Excel files in the chat box.",
        ),
        (
            "🖼️ Image understanding",
            "Attach one or more images and ask for analysis, extraction or comparison.",
        ),
    ]
    for column, (title, copy) in zip(columns, cards):
        with column:
            st.markdown(
                f'<div class="capability-card"><div class="capability-title">{title}</div>'
                f'<div class="capability-copy">{copy}</div></div>',
                unsafe_allow_html=True,
            )

submission = st.chat_input(
    "Message Kimi, paste a link, or attach files…",
    accept_file="multiple",
    file_type=SUPPORTED_FILE_TYPES,
    max_upload_size=25,
    submit_mode="disable",
)

pending_prompt = st.session_state.pop("pending_prompt", "")
if submission or pending_prompt:
    user_text = pending_prompt or submission.text.strip()
    uploaded_files = [] if pending_prompt else list(submission.files)

    if not user_text and not uploaded_files:
        st.stop()

    parsed_uploads: list[ParsedUpload] = []
    file_warnings: list[str] = []
    if uploaded_files:
        with st.status("Reading attachments…", expanded=False) as status:
            parsed_uploads, file_warnings = parse_uploaded_files(uploaded_files)
            status.update(
                label=f"Read {len(parsed_uploads)} attachment(s)",
                state="complete" if parsed_uploads else "error",
            )

    attachments = [safe_attachment_dict(upload) for upload in parsed_uploads]
    attachment_context = build_attachment_context(parsed_uploads)

    with st.chat_message("user", avatar="🧑"):
        render_attachments(attachments)
        if user_text:
            st.markdown(user_text)
        for warning in file_warnings:
            st.warning(warning)

    tool_result = ToolResult()
    if user_text:
        with st.status("Selecting and running tools…", expanded=False) as status:
            tool_result = run_tools(user_text)
            if tool_result.events:
                status.update(
                    label=f"Completed {len(tool_result.events)} tool action(s)",
                    state="complete",
                )
            elif tool_result.warnings:
                status.update(label="Tool finished with warnings", state="error")
            else:
                status.update(label="No external tool needed", state="complete")

    user_message = {
        "role": "user",
        "text": user_text,
        "attachments": attachments,
        "attachment_context": attachment_context,
        "tool_context": tool_result.context,
        "created_at": utc_now(),
    }
    st.session_state.messages.append(user_message)

    sources = source_dicts(tool_result)
    warnings = [*file_warnings, *tool_result.warnings]
    start_time = time.perf_counter()
    first_token_time: float | None = None
    usage: dict[str, Any] = {}

    with st.chat_message("assistant", avatar="🌙"):
        response_box = st.empty()
        full_response = ""
        try:
            stream = client.chat.completions.create(
                model=st.session_state.model_id.strip() or DEFAULT_MODEL,
                messages=build_api_messages(),
                stream=True,
                stream_options={"include_usage": True},
                max_tokens=MODE_SETTINGS[st.session_state.mode]["max_tokens"],
                temperature=0.3,
            )

            for chunk in stream:
                if getattr(chunk, "usage", None):
                    chunk_usage = chunk.usage
                    usage = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", None),
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", None),
                        "total_tokens": getattr(chunk_usage, "total_tokens", None),
                    }

                if not chunk.choices:
                    continue
                content = getattr(chunk.choices[0].delta, "content", None)
                if content:
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    full_response += content
                    response_box.markdown(full_response + "▌")

            if not full_response:
                full_response = (
                    "The model returned no visible response. Try again, switch the model "
                    "ID, or use Fast mode with a shorter conversation."
                )
            response_box.markdown(full_response)

        except Exception as exc:
            full_response = (
                "I could not complete the request.\n\n"
                f"**API error:** `{exc}`\n\n"
                "Try Fast mode, start a new chat, or verify the model ID in TokenRouter."
            )
            response_box.error(full_response)

        elapsed = time.perf_counter() - start_time
        latency_text = f"{elapsed:.1f}s total"
        if first_token_time is not None:
            latency_text += f" · {first_token_time - start_time:.1f}s to first text"
        if usage.get("total_tokens"):
            latency_text += f" · {usage['total_tokens']:,} tokens"
        st.caption(latency_text)

        render_tool_events(tool_result.events)
        render_sources(sources, warnings)

        if full_response and not full_response.startswith("I could not complete"):
            st.download_button(
                "Download this response as Word",
                data=answer_to_docx(full_response),
                file_name="kimi-response.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "text": full_response,
            "attachments": [],
            "sources": sources,
            "warnings": warnings,
            "tool_events": tool_result.events,
            "usage": usage,
            "elapsed_seconds": round(elapsed, 2),
            "created_at": utc_now(),
        }
    )
