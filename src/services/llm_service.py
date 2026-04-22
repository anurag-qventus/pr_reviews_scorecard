import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tiktoken
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import PromptTemplate
from langfuse.decorators import observe, langfuse_context

from config import AZURE_OPENAI_API_BASE, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION

# Tokens reserved for the prompt template, system message, and model output.
# Remaining budget is split equally between current and previous PR text.
_PROMPT_OVERHEAD_TOKENS = 5_000
_MODEL_MAX_TOKENS       = 128_000
_TOKEN_BUDGET_PER_PERIOD = (_MODEL_MAX_TOKENS - _PROMPT_OVERHEAD_TOKENS) // 2  # ~61 500

# Each chunk sent to the summarizer is at most this many tokens.
_SUMMARY_CHUNK_TOKENS = 40_000


class LLMService:

    def __init__(self):
        self.llm_client = AzureChatOpenAI(
            azure_endpoint=AZURE_OPENAI_API_BASE,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            deployment_name="gpt_4o",
            temperature=0,
            model_kwargs={"top_p": 0.01, "frequency_penalty": 0, "presence_penalty": 0}
        )
        self._enc = tiktoken.get_encoding("o200k_base")  # GPT-4o encoding

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    def _count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))

    # ------------------------------------------------------------------
    # Summarization (called only when a period exceeds its token budget)
    # ------------------------------------------------------------------

    @observe(name="summarize-chunk")
    def _summarize_chunk(self, chunk: str, period_label: str) -> str:
        """
        Condenses a chunk of PR review comments into a compact summary that
        preserves all key themes, issue types, and quality signals.
        """
        messages = [
            SystemMessage(content=(
                "You are a code review analyst. Summarize the following PR review "
                "comments concisely. Preserve every distinct issue type, recurring "
                "pattern, and quality signal. Keep PR numbers if mentioned."
                "Provide brief summarization on below following ask:"
                "1) Types pf issues identified ?"
                "2) Were comments repetitive ?"
                "3) Was PR quality improving over time ?"
                "4) What is the conclusion ?"
                "5) Compile the issues identified in the comments which have been asked to fix in code?"
                "6) Tell me the total number of PRs for this person?"
                "7) Rate the person on a scale of 10 based on the number of PRs and its quality?"
                )),
            HumanMessage(content=(
                f"Summarize these {period_label} period PR review comments:\n\n{chunk}"
            )),
        ]
        response = self.llm_client.invoke(messages)
        usage = response.response_metadata.get("token_usage", {})
        langfuse_context.update_current_observation(
            input=chunk,
            output=response.content,
            usage={
                "input":  usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "total":  usage.get("total_tokens", 0),
                "unit":   "TOKENS",
            },
            metadata={
                "period_label":      period_label,
                "prompt_tokens":     usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens":      usage.get("total_tokens", 0),
                "finish_reason":     response.response_metadata.get("finish_reason", ""),
            },
        )
        return response.content

    @observe(name="maybe-summarize")
    def _maybe_summarize(self, text: str, period_label: str) -> str:
        """
        If `text` fits within the per-period token budget, return it unchanged.
        Otherwise split it into chunks of up to _SUMMARY_CHUNK_TOKENS, summarize
        each chunk with a separate LLM call, then join the summaries.

        Chunks are split on token boundaries (not character boundaries) so no
        PR comment is silently truncated mid-sentence.
        """
        token_count = self._count_tokens(text)
        langfuse_context.update_current_observation(
            metadata={
                "period_label":        period_label,
                "input_tokens":        token_count,
                "token_budget":        _TOKEN_BUDGET_PER_PERIOD,
                "summarization_needed": token_count > _TOKEN_BUDGET_PER_PERIOD,
            },
        )
        if token_count <= _TOKEN_BUDGET_PER_PERIOD:
            return text

        print(
            f"  [{period_label}] {token_count} tokens exceeds budget "
            f"({_TOKEN_BUDGET_PER_PERIOD}). Summarizing..."
        )

        # Split into token-sized chunks
        tokens = self._enc.encode(text)
        chunks = [
            self._enc.decode(tokens[i : i + _SUMMARY_CHUNK_TOKENS])
            for i in range(0, len(tokens), _SUMMARY_CHUNK_TOKENS)
        ]
        print(f"  [{period_label}] Summarizing {len(chunks)} chunk(s)...")

        summaries = [self._summarize_chunk(chunk, period_label) for chunk in chunks]
        summarized = "\n\n".join(summaries)
        print(
            f"  [{period_label}] Summarized: "
            f"{token_count} → {self._count_tokens(summarized)} tokens"
        )
        return summarized

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    @observe(name="pr-scorecard-analysis")
    def generate_comparative_response(self, user_login, current_text, previous_text, duration_label):
        """
        Generates a comparative quality analysis from two periods of PR comment text.
        Provide brief summarization on below following ask:
            1) Types pf issues identified ?
            2) Were comments repetitive ?
            3) Was PR quality improving over time ?
            4) What is the conclusion ?
            5) Compile the issues identified in the comments which have been asked to fix in code?
            6) Tell me the total number of PRs for this person?
            7) Rate the person on a scale of 10 based on the number of PRs and its quality?

        If either period's text exceeds its token budget, it is first condensed via
        one or more summarization LLM calls before the final analysis prompt is sent.
        """
        langfuse_context.update_current_trace(
            user_id=user_login,
            tags=[duration_label],
            metadata={
                "duration_label":      duration_label,
                "current_tokens_raw":  self._count_tokens(current_text),
                "previous_tokens_raw": self._count_tokens(previous_text),
            },
        )

        current_text  = self._maybe_summarize(current_text,  "current")
        previous_text = self._maybe_summarize(previous_text, "previous")

        prompt = PromptTemplate(
            template="""You are a code review quality analyst. You will be given two sets of PR review comment threads for the same developer — one from the PREVIOUS period and one from the CURRENT period (each covering {duration_label}).

PREVIOUS PERIOD PR Review Comments:
{previous_text}

CURRENT PERIOD PR Review Comments:
{current_text}

Provide the following analysis:

**Current Period Summary**
Provide brief summarization on below following ask:
1) Types of issues identified in the current period?
2) Were review comments repetitive in the current period?
3) Was PR quality improving over time in the current period?
4) What is the conclusion in the current period?
5) Compile the issues identified in the comments which have been asked to fix in code in the current period?
6) Total number of PRs in the current period?
7) Quality rating for the current period (1–10)?

**Previous Period Summary**
1) Types of issues identified in the current period?
2) Were review comments repetitive in the current period?
3) Was PR quality improving over time in the current period?
4) What is the conclusion in the current period?
5) Compile the issues identified in the comments which have been asked to fix in code in the current period?
6) Total number of PRs in the current period?
7) Quality rating for the current period (1–10)?

**Comparative Analysis**
1) How has PR quality changed from the previous to the current period? Mention specific improvements or regressions.
2) Are there recurring issues that persist across both periods?
3) Overall improvement score: how much has the developer improved? (e.g. +2 points means improved by 2 on a 10-point scale)
4) Key recommendation for the developer going forward.
""",
            input_variables=['duration_label', 'previous_text', 'current_text']
        )

        prompt = prompt.format(
            duration_label=duration_label,
            previous_text=previous_text,
            current_text=current_text
        )
        message = [
            SystemMessage(content="You are a code review quality analyst comparing a developer's PR quality across two time periods."),
            HumanMessage(content=prompt)
        ]
        response = self.llm_client.invoke(message)
        usage = response.response_metadata.get("token_usage", {})
        langfuse_context.update_current_observation(
            input=prompt[-2000:],  # last 2000 chars — enough context without bloating the trace
            output=response.content,
            usage={
                "input":  usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "total":  usage.get("total_tokens", 0),
                "unit":   "TOKENS",
            },
            metadata={
                "current_tokens_final":  self._count_tokens(current_text),
                "previous_tokens_final": self._count_tokens(previous_text),
                "prompt_tokens":         usage.get("prompt_tokens", 0),
                "completion_tokens":     usage.get("completion_tokens", 0),
                "total_tokens":          usage.get("total_tokens", 0),
                "finish_reason":         response.response_metadata.get("finish_reason", ""),
            },
        )
        return response.content
