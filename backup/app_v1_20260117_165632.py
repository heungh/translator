"""
Adult Novel Translator - Two-Track Translation System
Translates Korean adult novels to English using Claude Bedrock and various uncensored models
"""

import streamlit as st
import boto3
import json
import re
import os
import requests
from abc import ABC, abstractmethod
from dotenv import load_dotenv

load_dotenv()

# Page configuration
st.set_page_config(
    page_title="Korean Adult Novel Translator",
    page_icon="📚",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .explicit-tag {
        background-color: #ffebee;
        border-left: 3px solid #f44336;
        padding: 10px;
        margin: 5px 0;
    }
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
</style>
""", unsafe_allow_html=True)


# =============================================================================
# Track 1: Claude Bedrock Translator
# =============================================================================

class ClaudeBedrockTranslator:
    """Handles translation using Claude via AWS Bedrock with Cross-Region Inference"""

    # Cross-Region Inference Profile Model IDs
    AVAILABLE_MODELS = {
        "Claude 4.5 Sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
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

    def translate_with_tagging(self, text: str) -> str:
        """
        Translate Korean text to English, marking explicit content with tags.
        Non-explicit content is translated, explicit content is tagged but kept in Korean.
        """
        system_prompt = """You are a professional Korean to English translator specializing in literary translation.

Your task:
1. Translate the given Korean adult novel text to English
2. For sexually explicit or graphic content (detailed sexual acts, explicit body part descriptions in sexual context, graphic intimate scenes), wrap the ORIGINAL KOREAN text with [EXPLICIT][/EXPLICIT] tags and DO NOT translate those parts
3. Translate all other content (narrative, dialogue, emotions, non-explicit descriptions) naturally to English
4. Maintain the literary style and flow of the original

Important:
- Only tag truly explicit sexual content, not romantic scenes or mild intimacy
- Keep the narrative flow natural
- Preserve paragraph structure
- The tagged sections should contain the ORIGINAL KOREAN text, not translation

Output format: Just the translated text with [EXPLICIT]Korean original[/EXPLICIT] tags where needed."""

        user_message = f"Translate the following Korean text:\n\n{text}"

        # Cross-Region Inference Profile 방식
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
        return response_body["content"][0]["text"]


# =============================================================================
# Track 2: Explicit Content Translators (Abstract Base)
# =============================================================================

class ExplicitTranslator(ABC):
    """Abstract base class for explicit content translators"""

    @abstractmethod
    def translate(self, korean_text: str) -> str:
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass


# =============================================================================
# Ollama (Self-hosted) Translator
# =============================================================================

class OllamaTranslator(ExplicitTranslator):
    """Translates using local Ollama with uncensored models"""

    RECOMMENDED_MODELS = [
        "dolphin-mixtral:8x7b",
        "dolphin-llama3:8b",
        "nous-hermes2:10.7b",
        "mythomax:13b",
        "openhermes:7b",
    ]

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "dolphin-mixtral:8x7b"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def get_name(self) -> str:
        return f"Ollama ({self.model})"

    def translate(self, korean_text: str) -> str:
        """Translate explicit Korean content to English using Ollama"""

        prompt = f"""You are a professional literary translator specializing in adult fiction.
Translate the following Korean text to natural, fluent English.
Maintain the literary quality and all explicit details accurately.

Korean text:
{korean_text}

English translation:"""

        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 2048
                }
            },
            timeout=120
        )

        if response.status_code != 200:
            raise Exception(f"Ollama error: {response.text}")

        return response.json()["response"].strip()

    @classmethod
    def list_available_models(cls, base_url: str = "http://localhost:11434") -> list:
        """List models available in Ollama"""
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get("models", [])
                return [m["name"] for m in models]
        except:
            pass
        return []


# =============================================================================
# Venus AI Translator
# =============================================================================

class VenusAITranslator(ExplicitTranslator):
    """Translates using Venus AI API (NSFW-friendly)"""

    def __init__(self, api_key: str, api_url: str = "https://api.venusai.chat/v1"):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")

    def get_name(self) -> str:
        return "Venus AI"

    def translate(self, korean_text: str) -> str:
        """Translate explicit Korean content to English using Venus AI"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "venus-1",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional literary translator specializing in adult fiction. Translate Korean to English accurately, maintaining all explicit details and literary quality."
                },
                {
                    "role": "user",
                    "content": f"Translate this Korean text to English:\n\n{korean_text}"
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.7
        }

        response = requests.post(
            f"{self.api_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )

        if response.status_code != 200:
            raise Exception(f"Venus AI error: {response.text}")

        return response.json()["choices"][0]["message"]["content"].strip()


# =============================================================================
# Cohere Command R (Bedrock) Translator
# =============================================================================

class CohereBedrockTranslator(ExplicitTranslator):
    """Translates using Cohere Command R via AWS Bedrock"""

    AVAILABLE_MODELS = {
        "Command R+": "cohere.command-r-plus-v1:0",
        "Command R": "cohere.command-r-v1:0",
    }

    def __init__(self, region_name: str = "us-east-1", model_name: str = "Command R+"):
        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=region_name
        )
        self.model_id = self.AVAILABLE_MODELS.get(model_name, self.AVAILABLE_MODELS["Command R+"])
        self.model_name = model_name

    def get_name(self) -> str:
        return f"Cohere {self.model_name} (Bedrock)"

    def translate(self, korean_text: str) -> str:
        """Translate explicit Korean content to English using Cohere Command R"""

        preamble = """You are a professional Korean to English literary translator specializing in adult fiction.
Translate the given Korean text to natural, fluent English.
Maintain all explicit details accurately and preserve the literary style and nuance."""

        message = f"Translate this Korean text to English:\n\n{korean_text}"

        body = json.dumps({
            "message": message,
            "preamble": preamble,
            "temperature": 0.7,
            "max_tokens": 4000,
        })

        response = self.client.invoke_model(
            modelId=self.model_id,
            body=body,
            contentType='application/json',
            accept='application/json'
        )

        result = json.loads(response['body'].read())
        return result['text'].strip()


# =============================================================================
# KoboldAI Translator
# =============================================================================

class KoboldAITranslator(ExplicitTranslator):
    """Translates using KoboldAI API (self-hosted or Horde)"""

    def __init__(self, api_url: str = "http://localhost:5001", api_key: str = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key  # For Horde

    def get_name(self) -> str:
        return "KoboldAI"

    def translate(self, korean_text: str) -> str:
        """Translate explicit Korean content to English using KoboldAI"""

        prompt = f"""### Instruction:
You are a professional literary translator. Translate the following Korean adult novel text to fluent English.
Maintain all explicit details accurately and preserve the literary style.

### Input:
{korean_text}

### Response:
"""

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["apikey"] = self.api_key

        payload = {
            "prompt": prompt,
            "max_length": 2048,
            "temperature": 0.7,
            "top_p": 0.9
        }

        response = requests.post(
            f"{self.api_url}/api/v1/generate",
            headers=headers,
            json=payload,
            timeout=120
        )

        if response.status_code != 200:
            raise Exception(f"KoboldAI error: {response.text}")

        return response.json()["results"][0]["text"].strip()


# =============================================================================
# OpenRouter Translator (Access to various uncensored models)
# =============================================================================

class OpenRouterTranslator(ExplicitTranslator):
    """Translates using OpenRouter API with access to various models"""

    # Models known to be less restrictive (2025 updated)
    RECOMMENDED_MODELS = [
        "mistralai/mistral-small-3.1-24b-instruct",
        "meta-llama/llama-3.3-70b-instruct",
        "qwen/qwen-2.5-72b-instruct",
        "google/gemini-2.0-flash-001",
    ]

    def __init__(self, api_key: str, model: str = "mistralai/mistral-small-3.1-24b-instruct"):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://openrouter.ai/api/v1"

    def get_name(self) -> str:
        return f"OpenRouter ({self.model.split('/')[-1]})"

    def translate(self, korean_text: str) -> str:
        """Translate explicit Korean content to English using OpenRouter"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8501",
            "X-Title": "Adult Novel Translator"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional literary translator specializing in adult fiction. Translate Korean to English accurately, maintaining all explicit details and literary quality."
                },
                {
                    "role": "user",
                    "content": f"Translate this Korean text to English:\n\n{korean_text}"
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.7
        }

        response = requests.post(
            f"{self.api_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )

        if response.status_code != 200:
            raise Exception(f"OpenRouter error: {response.text}")

        return response.json()["choices"][0]["message"]["content"].strip()


# =============================================================================
# Two-Track Translator Orchestrator
# =============================================================================

class TwoTrackTranslator:
    """Orchestrates the two-track translation process"""

    def __init__(self, bedrock_region: str, claude_model: str, explicit_translator: ExplicitTranslator):
        self.claude = ClaudeBedrockTranslator(region_name=bedrock_region, model_name=claude_model)
        self.explicit_translator = explicit_translator

    def extract_explicit_sections(self, text: str) -> list:
        """Extract all [EXPLICIT]...[/EXPLICIT] sections"""
        pattern = r'\[EXPLICIT\](.*?)\[/EXPLICIT\]'
        return re.findall(pattern, text, re.DOTALL)

    def translate(self, korean_text: str, progress_callback=None) -> dict:
        """
        Execute the two-track translation process

        Returns:
            dict with 'draft', 'explicit_translations', and 'final' keys
        """
        result = {
            "original": korean_text,
            "draft": "",
            "explicit_sections": [],
            "explicit_translations": [],
            "final": "",
            "track2_engine": self.explicit_translator.get_name()
        }

        # Track 1: Claude Bedrock translation with tagging
        if progress_callback:
            progress_callback("Track 1: Translating with Claude Bedrock...")

        draft = self.claude.translate_with_tagging(korean_text)
        result["draft"] = draft

        # Extract explicit sections
        explicit_sections = self.extract_explicit_sections(draft)
        result["explicit_sections"] = explicit_sections

        if not explicit_sections:
            result["final"] = draft
            return result

        # Track 2: Translate explicit sections
        if progress_callback:
            progress_callback(f"Track 2: Translating explicit content with {self.explicit_translator.get_name()}...")

        explicit_translations = []
        for i, section in enumerate(explicit_sections):
            if progress_callback:
                progress_callback(f"Translating explicit section {i+1}/{len(explicit_sections)}...")
            translation = self.explicit_translator.translate(section)
            explicit_translations.append(translation)

        result["explicit_translations"] = explicit_translations

        # Combine: Replace tagged sections with translations
        final_text = draft
        for original, translated in zip(explicit_sections, explicit_translations):
            final_text = final_text.replace(
                f"[EXPLICIT]{original}[/EXPLICIT]",
                translated
            )

        result["final"] = final_text
        return result


# =============================================================================
# Streamlit UI
# =============================================================================

def main():
    st.title("Korean Adult Novel Translator")
    st.markdown("### Two-Track Translation System")
    st.markdown("""
    - **Track 1**: Claude Bedrock - translates general content, tags explicit sections
    - **Track 2**: Uncensored model - translates explicit sections
    """)

    st.divider()

    # Sidebar for configuration
    with st.sidebar:
        st.header("Configuration")

        # Track 1 Settings
        st.subheader("Track 1: Claude Bedrock")
        bedrock_region = st.selectbox(
            "AWS Region",
            ["us-east-1", "us-west-2", "ap-northeast-1", "eu-west-1"],
            index=0
        )
        claude_model = st.selectbox(
            "Claude Model",
            list(ClaudeBedrockTranslator.AVAILABLE_MODELS.keys()),
            index=0,
            help="Cross-Region Inference Profile 사용"
        )

        st.divider()

        # Track 2 Settings
        st.subheader("Track 2: Explicit Content")
        track2_engine = st.selectbox(
            "Translation Engine",
            ["Cohere Command R (Bedrock)", "Ollama (Self-hosted)", "OpenRouter", "Venus AI", "KoboldAI"],
            index=0
        )

        # Engine-specific settings
        if track2_engine == "Cohere Command R (Bedrock)":
            st.markdown("##### Cohere Bedrock Settings")
            cohere_model = st.selectbox(
                "Cohere Model",
                list(CohereBedrockTranslator.AVAILABLE_MODELS.keys()),
                index=0
            )
            st.info("Uses same AWS credentials as Track 1")

        elif track2_engine == "Ollama (Self-hosted)":
            st.markdown("##### Ollama Settings")
            ollama_url = st.text_input(
                "Ollama URL",
                value="http://localhost:11434",
                help="URL of your Ollama server"
            )

            # Try to get available models
            available_models = OllamaTranslator.list_available_models(ollama_url)

            if available_models:
                ollama_model = st.selectbox(
                    "Model",
                    available_models,
                    index=0
                )
            else:
                ollama_model = st.selectbox(
                    "Model (recommended)",
                    OllamaTranslator.RECOMMENDED_MODELS,
                    index=0
                )
                st.caption("Install: `ollama pull dolphin-mixtral:8x7b`")

            st.markdown("""
            <div class="info-box">
            <b>Recommended Models:</b><br>
            - dolphin-mixtral:8x7b<br>
            - dolphin-llama3:8b<br>
            - nous-hermes2:10.7b
            </div>
            """, unsafe_allow_html=True)

        elif track2_engine == "OpenRouter":
            st.markdown("##### OpenRouter Settings")
            openrouter_key = st.text_input(
                "API Key",
                type="password",
                value=os.getenv("OPENROUTER_API_KEY", ""),
                help="Get from openrouter.ai"
            )
            openrouter_model = st.selectbox(
                "Model",
                OpenRouterTranslator.RECOMMENDED_MODELS,
                index=0
            )
            st.caption("Less restrictive models available")

        elif track2_engine == "Venus AI":
            st.markdown("##### Venus AI Settings")
            venus_key = st.text_input(
                "API Key",
                type="password",
                value=os.getenv("VENUS_API_KEY", ""),
                help="Get from venusai.chat"
            )
            venus_url = st.text_input(
                "API URL",
                value="https://api.venusai.chat/v1"
            )

        elif track2_engine == "KoboldAI":
            st.markdown("##### KoboldAI Settings")
            kobold_url = st.text_input(
                "API URL",
                value="http://localhost:5001",
                help="KoboldAI server URL"
            )
            kobold_key = st.text_input(
                "Horde API Key (optional)",
                type="password",
                help="For AI Horde access"
            )

        st.divider()
        st.markdown("### Setup Guide")
        with st.expander("Ollama Setup"):
            st.code("""
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull uncensored model
ollama pull dolphin-mixtral:8x7b

# Start server (default port 11434)
ollama serve
            """, language="bash")

        with st.expander("OpenRouter Setup"):
            st.markdown("""
            1. Sign up at [openrouter.ai](https://openrouter.ai)
            2. Get API key from dashboard
            3. Add credits to your account
            4. Select uncensored model above
            """)

    # Main content area
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Korean Original")
        korean_text = st.text_area(
            "Paste Korean text here",
            height=400,
            placeholder="Enter Korean adult novel text to translate...",
            label_visibility="collapsed"
        )

    # Translation button
    translate_btn = st.button("Translate", type="primary", use_container_width=True)

    if translate_btn:
        if not korean_text.strip():
            st.error("Please enter some Korean text to translate.")
            return

        # Create appropriate translator
        try:
            if track2_engine == "Cohere Command R (Bedrock)":
                explicit_translator = CohereBedrockTranslator(
                    region_name=bedrock_region,
                    model_name=cohere_model
                )
            elif track2_engine == "Ollama (Self-hosted)":
                explicit_translator = OllamaTranslator(
                    base_url=ollama_url,
                    model=ollama_model
                )
            elif track2_engine == "OpenRouter":
                if not openrouter_key:
                    st.error("Please provide OpenRouter API key.")
                    return
                explicit_translator = OpenRouterTranslator(
                    api_key=openrouter_key,
                    model=openrouter_model
                )
            elif track2_engine == "Venus AI":
                if not venus_key:
                    st.error("Please provide Venus AI API key.")
                    return
                explicit_translator = VenusAITranslator(
                    api_key=venus_key,
                    api_url=venus_url
                )
            elif track2_engine == "KoboldAI":
                explicit_translator = KoboldAITranslator(
                    api_url=kobold_url,
                    api_key=kobold_key if kobold_key else None
                )

            # Initialize two-track translator
            translator = TwoTrackTranslator(
                bedrock_region=bedrock_region,
                claude_model=claude_model,
                explicit_translator=explicit_translator
            )

            # Progress tracking
            progress_bar = st.progress(0)
            status_text = st.empty()

            def update_progress(message):
                status_text.text(message)

            # Execute translation
            progress_bar.progress(10)
            update_progress("Starting translation...")

            result = translator.translate(korean_text, progress_callback=update_progress)

            progress_bar.progress(100)
            status_text.text("Translation complete!")

            # Display results
            st.divider()

            # Summary
            st.success(f"""
            **Translation Complete**
            - Track 1: Claude Bedrock
            - Track 2: {result['track2_engine']}
            - Explicit sections found: {len(result['explicit_sections'])}
            """)

            # Tabs for different views
            tab1, tab2, tab3 = st.tabs(["Final Translation", "Draft (with tags)", "Explicit Sections"])

            with tab1:
                st.subheader("Final English Translation")
                st.text_area(
                    "Final translation",
                    value=result["final"],
                    height=400,
                    label_visibility="collapsed"
                )
                st.download_button(
                    "Download Final Translation",
                    result["final"],
                    file_name="translation_final.txt",
                    mime="text/plain"
                )

            with tab2:
                st.subheader("Draft Translation (Claude Bedrock)")
                st.markdown("*Explicit sections marked with [EXPLICIT] tags*")
                st.text_area(
                    "Draft translation",
                    value=result["draft"],
                    height=400,
                    label_visibility="collapsed"
                )

            with tab3:
                st.subheader("Explicit Sections Detail")
                if result["explicit_sections"]:
                    for i, (original, translated) in enumerate(zip(
                        result["explicit_sections"],
                        result["explicit_translations"]
                    )):
                        with st.expander(f"Section {i+1}", expanded=True):
                            col_a, col_b = st.columns(2)
                            with col_a:
                                st.markdown("**Original (Korean)**")
                                st.markdown(f'<div class="explicit-tag">{original}</div>',
                                           unsafe_allow_html=True)
                            with col_b:
                                st.markdown(f"**Translated ({result['track2_engine']})**")
                                st.markdown(f'<div class="translated-tag">{translated}</div>',
                                           unsafe_allow_html=True)
                else:
                    st.info("No explicit sections were identified in the text.")

        except Exception as e:
            st.error(f"Translation error: {str(e)}")
            with st.expander("Error Details"):
                st.exception(e)


if __name__ == "__main__":
    main()
