"""
Korean-English Translator - General Purpose Translation
Translates Korean text to English using Claude Bedrock
"""

import streamlit as st
import boto3
import json
import os
from dotenv import load_dotenv
from datetime import datetime
import hashlib

# Optional imports for file handling
try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

load_dotenv()

# Page configuration
st.set_page_config(
    page_title="Korean-English Translator",
    page_icon="📚",
    layout="wide"
)

# Initialize session state for history (load from files on first run)
if "translation_history" not in st.session_state:
    st.session_state.translation_history = []
    st.session_state.history_loaded = False

# Custom CSS
st.markdown("""
<style>
    .translated-tag {
        background-color: #e8f5e9;
        border-left: 3px solid #4caf50;
        padding: 10px;
        margin: 5px 0;
    }
    .info-box {
        background-color: #e3f2fd;
        border-radius: 5px;
        padding: 15px;
        margin: 10px 0;
    }
    .history-item {
        background-color: #f5f5f5;
        border-radius: 5px;
        padding: 10px;
        margin: 5px 0;
        border-left: 3px solid #2196f3;
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# File Handling Utilities
# =============================================================================

HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")

# Ensure history directory exists
os.makedirs(HISTORY_DIR, exist_ok=True)


def read_uploaded_file(uploaded_file) -> str:
    """Read content from uploaded file (txt or docx)"""
    if uploaded_file.name.endswith('.txt'):
        return uploaded_file.read().decode('utf-8')
    elif uploaded_file.name.endswith('.docx'):
        if not DOCX_AVAILABLE:
            raise Exception("python-docx not installed. Run: pip install python-docx")
        doc = Document(uploaded_file)
        return '\n'.join([para.text for para in doc.paragraphs])
    else:
        raise Exception(f"Unsupported file type: {uploaded_file.name}")


def save_to_history(original: str, result: dict):
    """Save translation to session history and file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    entry_id = hashlib.md5(original[:100].encode()).hexdigest()[:8]

    # Create entry for session state
    history_entry = {
        "id": entry_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "original_preview": original[:100] + "..." if len(original) > 100 else original,
        "original": original,
        "result": result,
    }
    st.session_state.translation_history.insert(0, history_entry)
    # Keep only last 20 entries in session
    st.session_state.translation_history = st.session_state.translation_history[:20]

    # Save to files
    base_filename = f"{timestamp}_{entry_id}"

    # Save original (Korean)
    original_path = os.path.join(HISTORY_DIR, f"{base_filename}_original.txt")
    with open(original_path, 'w', encoding='utf-8') as f:
        f.write(original)

    # Save translation (English)
    translation_path = os.path.join(HISTORY_DIR, f"{base_filename}_translated.txt")
    with open(translation_path, 'w', encoding='utf-8') as f:
        f.write(result.get('translation', ''))

    # Save full result as JSON
    json_path = os.path.join(HISTORY_DIR, f"{base_filename}_full.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def load_history_from_files():
    """Load translation history from files"""
    history = []

    if not os.path.exists(HISTORY_DIR):
        return history

    # Find all original files and pair with translations
    files = os.listdir(HISTORY_DIR)
    original_files = sorted([f for f in files if f.endswith('_original.txt')], reverse=True)

    for orig_file in original_files[:20]:  # Load last 20
        base_name = orig_file.replace('_original.txt', '')
        trans_file = f"{base_name}_translated.txt"
        json_file = f"{base_name}_full.json"

        try:
            # Read original
            with open(os.path.join(HISTORY_DIR, orig_file), 'r', encoding='utf-8') as f:
                original = f.read()

            # Read translation
            trans_path = os.path.join(HISTORY_DIR, trans_file)
            if os.path.exists(trans_path):
                with open(trans_path, 'r', encoding='utf-8') as f:
                    translation = f.read()
            else:
                translation = ""

            # Read full result if exists
            json_path = os.path.join(HISTORY_DIR, json_file)
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    result = json.load(f)
            else:
                result = {"translation": translation}

            # Parse timestamp from filename
            parts = base_name.split('_')
            if len(parts) >= 3:
                date_str = parts[0]
                time_str = parts[1]
                entry_id = parts[2]
                timestamp = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
            else:
                timestamp = base_name
                entry_id = hashlib.md5(original[:100].encode()).hexdigest()[:8]

            history.append({
                "id": entry_id,
                "timestamp": timestamp,
                "original_preview": original[:100] + "..." if len(original) > 100 else original,
                "original": original,
                "result": result,
            })
        except Exception:
            continue

    return history


def export_history_to_file():
    """Export all history to a JSON file"""
    return json.dumps(st.session_state.translation_history, ensure_ascii=False, indent=2)


# =============================================================================
# Claude Bedrock Translator
# =============================================================================

class ClaudeBedrockTranslator:
    """Handles translation using Claude via AWS Bedrock with Cross-Region Inference"""

    AVAILABLE_MODELS = {
        "Claude Opus 4.6": "us.anthropic.claude-opus-4-6-20250924-v1:0",
        "Claude Sonnet 4.6": "us.anthropic.claude-sonnet-4-6-20250924-v1:0",
        "Claude 4.5 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "Claude 4.5 Haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "Claude 3.5 Sonnet v2": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "Claude 3.5 Haiku": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
    }

    def __init__(self, region_name: str = "us-east-1", model_name: str = "Claude 4.5 Sonnet"):
        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=region_name
        )
        self.model_id = self.AVAILABLE_MODELS.get(model_name, self.AVAILABLE_MODELS["Claude 4.5 Sonnet"])
        self.model_name = model_name

    def translate(self, text: str) -> dict:
        """Translate Korean text to English."""
        system_prompt = """You are a professional Korean to English translator.

Your task:
1. Translate the given Korean text to natural, fluent English
2. Maintain the original style and tone
3. Preserve paragraph structure
4. Ensure accuracy while keeping readability

Output format: Just the translated text."""

        user_message = f"Translate the following Korean text to English:\n\n{text}"

        message = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message}
            ]
        }

        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(message)
        )

        response_body = json.loads(response["body"].read())
        translated_text = response_body["content"][0]["text"]

        return {
            "original": text,
            "translation": translated_text,
            "model": self.model_name,
        }

    def translate_batch(self, texts: list, progress_callback=None) -> list:
        """Translate multiple texts"""
        results = []
        for i, text in enumerate(texts):
            if progress_callback:
                progress_callback(f"Translating document {i+1}/{len(texts)}...")
            result = self.translate(text)
            results.append(result)
        return results


# =============================================================================
# Streamlit UI
# =============================================================================

def render_sidebar():
    """Render sidebar configuration"""
    with st.sidebar:
        st.header("Configuration")

        st.subheader("Claude Bedrock")
        bedrock_region = st.selectbox(
            "AWS Region",
            ["us-east-1", "us-west-2", "ap-northeast-1", "eu-west-1"],
            index=0
        )
        claude_model = st.selectbox(
            "Claude Model",
            list(ClaudeBedrockTranslator.AVAILABLE_MODELS.keys()),
            index=0,
            help="Cross-Region Inference Profile"
        )

        st.divider()

        # History section
        st.subheader("Translation History")
        st.caption(f"{len(st.session_state.translation_history)} translations saved")

        if st.session_state.translation_history:
            if st.button("Export History (JSON)"):
                st.download_button(
                    "Download",
                    export_history_to_file(),
                    file_name=f"translation_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )
            if st.button("Clear History"):
                st.session_state.translation_history = []
                st.rerun()

    return bedrock_region, claude_model


def render_single_translation(bedrock_region, claude_model):
    """Render single text translation tab"""
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Input")

        # File upload option
        uploaded_file = st.file_uploader(
            "Upload file (optional)",
            type=["txt", "docx"] if DOCX_AVAILABLE else ["txt"],
            help="Upload a .txt or .docx file"
        )

        if uploaded_file:
            try:
                file_content = read_uploaded_file(uploaded_file)
                korean_text = st.text_area(
                    "Korean Text",
                    value=file_content,
                    height=350,
                    label_visibility="collapsed"
                )
                st.success(f"Loaded: {uploaded_file.name}")
            except Exception as e:
                st.error(f"Error reading file: {e}")
                korean_text = st.text_area(
                    "Korean Text",
                    height=350,
                    placeholder="Enter Korean text to translate...",
                    label_visibility="collapsed"
                )
        else:
            korean_text = st.text_area(
                "Korean Text",
                height=350,
                placeholder="Enter Korean text to translate...",
                label_visibility="collapsed"
            )

    with col2:
        st.subheader("Output")
        output_container = st.container()

    # Translation button
    if st.button("Translate", type="primary", use_container_width=True):
        if not korean_text.strip():
            st.error("Please enter some Korean text to translate.")
            return

        try:
            translator = ClaudeBedrockTranslator(
                region_name=bedrock_region,
                model_name=claude_model
            )

            progress_bar = st.progress(0)
            status_text = st.empty()

            progress_bar.progress(10)
            status_text.text("Translating with Claude Bedrock...")

            result = translator.translate(korean_text)

            progress_bar.progress(100)
            status_text.text("Translation complete!")

            # Save to history
            save_to_history(korean_text, result)

            # Display translation in Output column
            with output_container:
                st.text_area(
                    "Translation",
                    value=result["translation"],
                    height=350,
                    label_visibility="collapsed",
                    key="output_final"
                )

            # Display results
            st.divider()

            st.success(f"**Translation Complete** — Model: Claude Bedrock ({claude_model})")

            col_a, col_b = st.columns(2)
            with col_a:
                st.download_button(
                    "Download as TXT",
                    result["translation"],
                    file_name="translation.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            with col_b:
                st.download_button(
                    "Download as JSON",
                    json.dumps(result, ensure_ascii=False, indent=2),
                    file_name="translation_full.json",
                    mime="application/json",
                    use_container_width=True
                )

        except Exception as e:
            st.error(f"Translation error: {str(e)}")
            with st.expander("Error Details"):
                st.exception(e)


def render_batch_translation(bedrock_region, claude_model):
    """Render batch translation tab"""
    st.subheader("Batch Translation")
    st.markdown("Upload multiple files for batch processing.")

    uploaded_files = st.file_uploader(
        "Upload files",
        type=["txt", "docx"] if DOCX_AVAILABLE else ["txt"],
        accept_multiple_files=True,
        help="Select multiple files to translate"
    )

    if uploaded_files:
        st.info(f"{len(uploaded_files)} files selected")

        # Show file list
        for f in uploaded_files:
            st.caption(f"- {f.name}")

        if st.button("Start Batch Translation", type="primary", use_container_width=True):
            try:
                translator = ClaudeBedrockTranslator(
                    region_name=bedrock_region,
                    model_name=claude_model
                )

                progress_bar = st.progress(0)
                status_text = st.empty()

                results = []
                for i, uploaded_file in enumerate(uploaded_files):
                    status_text.text(f"Processing {uploaded_file.name} ({i+1}/{len(uploaded_files)})...")
                    progress_bar.progress((i + 1) / len(uploaded_files))

                    try:
                        content = read_uploaded_file(uploaded_file)
                        result = translator.translate(content)
                        result["filename"] = uploaded_file.name
                        results.append(result)
                        save_to_history(content, result)
                    except Exception as e:
                        results.append({
                            "filename": uploaded_file.name,
                            "error": str(e)
                        })

                status_text.text("Batch translation complete!")
                progress_bar.progress(100)

                # Display results
                st.divider()
                st.success(f"Processed {len(results)} files")

                for result in results:
                    with st.expander(result.get("filename", "Unknown"), expanded=False):
                        if "error" in result:
                            st.error(f"Error: {result['error']}")
                        else:
                            st.text_area(
                                "Translation",
                                value=result["translation"],
                                height=200,
                                label_visibility="collapsed"
                            )
                            st.download_button(
                                f"Download {result['filename'].replace('.txt', '_translated.txt')}",
                                result["translation"],
                                file_name=result["filename"].replace('.txt', '_translated.txt').replace('.docx', '_translated.txt'),
                                mime="text/plain"
                            )

            except Exception as e:
                st.error(f"Batch translation error: {str(e)}")


def render_history():
    """Render translation history tab"""
    st.subheader("Translation History")

    if not st.session_state.translation_history:
        st.info("No translation history yet. Translations will appear here after you translate text.")
        return

    for idx, entry in enumerate(st.session_state.translation_history):
        with st.expander(f"[{entry['timestamp']}] {entry['original_preview']}", expanded=False):
            st.markdown(f"**ID:** {entry['id']}")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Original:**")
                st.text_area("orig", value=entry['original'], height=150, label_visibility="collapsed", disabled=True, key=f"hist_orig_{idx}_{entry['id']}")
            with col2:
                st.markdown("**Translation:**")
                st.text_area("trans", value=entry['result'].get('translation', entry['result'].get('final', '')), height=150, label_visibility="collapsed", disabled=True, key=f"hist_trans_{idx}_{entry['id']}")

            st.download_button(
                "Download Translation",
                entry['result'].get('translation', entry['result'].get('final', '')),
                file_name=f"translation_{entry['id']}.txt",
                mime="text/plain",
                key=f"hist_download_{idx}_{entry['id']}"
            )


def main():
    # Load history from files on first run
    if not st.session_state.get("history_loaded", False):
        st.session_state.translation_history = load_history_from_files()
        st.session_state.history_loaded = True

    st.title("Korean-English Translator")
    st.markdown("Translate Korean text to English using Claude Bedrock.")

    st.divider()

    # Render sidebar and get configuration
    bedrock_region, claude_model = render_sidebar()

    # Main tabs
    tab1, tab2, tab3 = st.tabs(["Single Translation", "Batch Translation", "History"])

    with tab1:
        render_single_translation(bedrock_region, claude_model)

    with tab2:
        render_batch_translation(bedrock_region, claude_model)

    with tab3:
        render_history()


if __name__ == "__main__":
    main()
