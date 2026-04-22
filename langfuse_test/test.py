"""
Langfuse observability test
============================
Validates that Langfuse traces, token counts, latency, and input/output
are captured correctly before wiring the same pattern into the main repo.

Run from project root:
    uv run python langfuse_test/test.py
"""

import os
import sys

# Load .env from project root (one level up from this file)
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from langfuse import Langfuse
from langfuse.decorators import langfuse_context, observe
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


# ---------------------------------------------------------------------------
# Langfuse client — used only for flush() at the end
# ---------------------------------------------------------------------------
langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)

# ---------------------------------------------------------------------------
# Azure OpenAI client
# ---------------------------------------------------------------------------
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_API_BASE"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    deployment_name="gpt_4o",
    temperature=0,
)


# ---------------------------------------------------------------------------
# Inner span — the actual LLM call, logged as a "generation"
# ---------------------------------------------------------------------------
@observe(name="llm-call")
def call_llm(prompt: str) -> str:
    """
    Wraps a single LLM invoke call.
    Logs input prompt, output completion, and token usage to Langfuse.
    """
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)

    # Extract token usage from LangChain response metadata
    usage = response.response_metadata.get("token_usage", {})
    prompt_tokens     = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens      = usage.get("total_tokens", 0)

    # Log input, output, token usage, and model metadata to the Langfuse span
    langfuse_context.update_current_observation(
        input=prompt,
        output=response.content,
        usage={
            "input":  prompt_tokens,
            "output": completion_tokens,
            "total":  total_tokens,
            "unit":   "TOKENS",
        },
        metadata={
            "model":              response.response_metadata.get("model_name", "gpt_4o"),
            "finish_reason":      response.response_metadata.get("finish_reason", ""),
            "prompt_tokens":      prompt_tokens,
            "completion_tokens":  completion_tokens,
            "total_tokens":       total_tokens,
        },
    )

    # Print locally for quick verification
    print(f"\n--- LLM Response ---")
    print(response.content)
    print(f"\n--- Token Usage ---")
    print(f"  Prompt tokens:     {prompt_tokens}")
    print(f"  Completion tokens: {completion_tokens}")
    print(f"  Total tokens:      {total_tokens}")

    return response.content


# ---------------------------------------------------------------------------
# Outer span — the root trace, sets user/session/tag metadata
# ---------------------------------------------------------------------------
@observe(name="test-run")
def run_test(user_id: str, prompt: str) -> str:
    """
    Root trace for the test. Sets trace-level metadata visible in Langfuse.
    Calls call_llm() as a nested child span.
    """
    langfuse_context.update_current_trace(
        user_id=user_id,
        tags=["langfuse-test", "azure-openai"],
        metadata={
            "environment": "test",
            "prompt_length": len(prompt),
        },
    )

    result = call_llm(prompt)

    langfuse_context.update_current_observation(
        input=prompt,
        output=result,
        metadata={"status": "success"},
    )

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_prompt = (
        "Summarize the following in two sentences: "
        "Pull request reviews are a critical part of the software development lifecycle. "
        "They help catch bugs early, enforce coding standards, and share knowledge across the team. "
        "However, the quality of reviews varies significantly across developers and over time."
    )

    print("Starting Langfuse test...")
    print(f"Langfuse host: {os.getenv('LANGFUSE_HOST')}")
    print(f"Prompt: {test_prompt[:80]}...")

    result = run_test(user_id="test-user", prompt=test_prompt)

    # Flush ensures all traces are sent before the script exits
    langfuse.flush()
    print("\nTrace flushed to Langfuse. Check your Langfuse dashboard.")
