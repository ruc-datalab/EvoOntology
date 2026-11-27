#!/usr/bin/env python3
"""
Base evaluator module for DDR_Bench.

Provides shared LLM API calling, log loading, and statistics calculation
for all evaluation scenarios (MIMIC, 10-K, GLOBEM).
"""

import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import threading
import time
import logging
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import statistics
import pandas as pd
import os

try:
    from openai import OpenAI, AzureOpenAI
except Exception:
    OpenAI = None
    AzureOpenAI = None

logger = logging.getLogger(__name__)


class BaseEvaluator:
    """Base evaluator with shared LLM API and data loading logic."""

    def __init__(
        self,
        scenario: str = "mimic",
        entity_prefix: str = "patient",
        vllm_url: str = "",
        provider: str = "azure",
        openai_model: str = "gpt-5-mini",
        openai_api_key: str = "",
        azure_endpoint: str = "",
        azure_api_version: str = "2024-12-01-preview",
        azure_model: str = "gpt-5-mini",
        max_retries: int = 5,
        retry_delay: float = 2.0,
        eval_max_tokens: int = 4096,
        eval_temperature: float = 0.0,
    ):
        self.scenario = scenario
        self.entity_prefix = entity_prefix
        self.vllm_url = vllm_url
        self.provider = provider.lower()

        # API credentials
        self.openai_model = openai_model
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.azure_api_version = azure_api_version or os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        self.azure_model = azure_model or os.getenv("AZURE_OPENAI_MODEL", "gpt-5-mini")

        # VLLM / DeepSeek API key (for evaluator) — explicitly-passed key takes precedence
        self.vllm_api_key = (
            openai_api_key  # <-- explicit pass from run_evaluation (config-aware)
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("VLLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )

        # Build headers with Authorization if API key is available
        self.headers = {"Content-Type": "application/json"}
        if self.vllm_api_key:
            self.headers["Authorization"] = f"Bearer {self.vllm_api_key}"

        self._openai_client = None
        self._azure_client = None
        self._client_lock = threading.Lock()  # protects lazy client init in parallel mode
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.eval_max_tokens = eval_max_tokens
        self.eval_temperature = eval_temperature

        # Connection-level retry session for vLLM calls.
        # SSL handshake failures (SSLEOFError) happen at the transport layer
        # before requests ever sees them. urllib3 Retry with connect=3 retries
        # at the connection pool level with backoff, absorbing transient TLS
        # failures without wasting a full request-level retry slot.
        self._vllm_session = requests.Session()
        retry_strategy = Retry(
            total=0,                # 0 = no request-level retry (we handle that ourselves)
            connect=3,              # retry connection/TLS errors up to 3 times
            backoff_factor=0.5,     # 0.5s → 1s → 2s
            allowed_methods={"POST"},
            status_forcelist=[],    # don't retry on HTTP status — let our code handle it
        )
        adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=retry_strategy)
        for scheme in ("http", "https"):
            self._vllm_session.mount(f"{scheme}://", adapter)

    # =========================================================================
    # LLM API Methods
    # =========================================================================

    def call_vllm_api(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        """Call vLLM API with retry mechanism."""
        payload = {
            # "model": "Qwen/Qwen3-30B-A3B-Instruct-2507",
            "model": self.openai_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.eval_temperature
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._vllm_session.post(self.vllm_url, headers=self.headers, json=payload, timeout=120)
                response.raise_for_status()
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

                if content:
                    if attempt > 0:
                        logger.info(f"vLLM API call succeeded on attempt {attempt + 1}")
                    return content
                else:
                    logger.warning(f"vLLM API returned empty content on attempt {attempt + 1}")
                    last_error = "Empty response content"

            except requests.exceptions.Timeout as e:
                last_error = f"Timeout error: {e}"
                logger.warning(f"vLLM API timeout on attempt {attempt + 1}/{self.max_retries}: {e}")
            except requests.exceptions.RequestException as e:
                last_error = f"Request error: {e}"
                logger.warning(f"vLLM API request error on attempt {attempt + 1}/{self.max_retries}: {e}")
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.warning(f"vLLM API unexpected error on attempt {attempt + 1}/{self.max_retries}: {e}")

            if attempt < self.max_retries - 1:
                wait_time = self.retry_delay * (2 ** attempt)
                logger.info(f"Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)

        logger.error(f"vLLM API failed after {self.max_retries} attempts. Last error: {last_error}")
        return ""

    def _ensure_openai_client(self):
        if self._openai_client is None:
            with self._client_lock:
                if self._openai_client is None:
                    if OpenAI is None:
                        raise RuntimeError("OpenAI SDK not available. Please install openai>=1.0.0.")
                    api_key = self.openai_api_key or os.getenv("OPENAI_API_KEY", "")
                    if not api_key:
                        raise RuntimeError("OPENAI_API_KEY is required for provider 'openai'.")
                    self._openai_client = OpenAI(api_key=api_key)

    def _ensure_azure_client(self):
        if self._azure_client is None:
            with self._client_lock:
                if self._azure_client is None:
                    if AzureOpenAI is None:
                        raise RuntimeError("Azure OpenAI SDK not available. Please install openai>=1.0.0.")
                    endpoint = self.azure_endpoint
                    api_version = self.azure_api_version
                    api_key = self.openai_api_key or os.getenv("AZURE_OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
                    if not endpoint or not api_key:
                        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required for provider 'azure'.")
                    self._azure_client = AzureOpenAI(api_version=api_version, azure_endpoint=endpoint, api_key=api_key)

    def call_openai_api(self, messages: List[Dict[str, str]], max_tokens: int = 4096, temperature: float = 0.0) -> str:
        """Call OpenAI API with retry mechanism."""
        self._ensure_openai_client()

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._openai_client.chat.completions.create(
                    model=self.openai_model,
                    messages=messages,
                    max_completion_tokens=max_tokens,
                    temperature=temperature
                )
                content = (response.choices[0].message.content or "").strip()

                if content:
                    if attempt > 0:
                        logger.info(f"OpenAI API call succeeded on attempt {attempt + 1}")
                    return content
                else:
                    logger.warning(f"OpenAI API returned empty content on attempt {attempt + 1}")
                    last_error = "Empty response content"

            except Exception as e:
                last_error = str(e)
                logger.warning(f"OpenAI API error on attempt {attempt + 1}/{self.max_retries}: {e}")

            if attempt < self.max_retries - 1:
                wait_time = self.retry_delay * (2 ** attempt)
                logger.info(f"Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)

        logger.error(f"OpenAI API failed after {self.max_retries} attempts. Last error: {last_error}")
        return ""

    def call_azure_openai_api(self, messages: List[Dict[str, str]], max_tokens: int = 4096, temperature: float = 0.0) -> str:
        """Call Azure OpenAI API with retry mechanism."""
        self._ensure_azure_client()

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = self._azure_client.chat.completions.create(
                    model=self.azure_model,
                    messages=messages,
                    max_completion_tokens=max_tokens,
                    temperature=temperature
                )
                content = (response.choices[0].message.content or "").strip()

                if content:
                    if attempt > 0:
                        logger.info(f"Azure OpenAI API call succeeded on attempt {attempt + 1}")
                    return content
                else:
                    logger.warning(f"Azure OpenAI API returned empty content on attempt {attempt + 1}")
                    last_error = "Empty response content"

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Azure OpenAI API error on attempt {attempt + 1}/{self.max_retries}: {e}")

            if attempt < self.max_retries - 1:
                wait_time = self.retry_delay * (2 ** attempt)
                logger.info(f"Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)

        logger.error(f"Azure OpenAI API failed after {self.max_retries} attempts. Last error: {last_error}")
        return ""

    def call_llm_api(self, messages: List[Dict[str, str]], max_tokens: int = 4096, temperature: float | None = None) -> str:
        """Unified LLM call routing by provider."""
        temp = temperature if temperature is not None else self.eval_temperature
        if self.provider == "vllm":
            return self.call_vllm_api(messages, max_tokens=max_tokens)
        if self.provider == "azure":
            return self.call_azure_openai_api(messages, max_tokens=max_tokens, temperature=temp)
        return self.call_openai_api(messages, max_tokens=max_tokens, temperature=temp)

    # =========================================================================
    # Data Loading Methods
    # =========================================================================

    def load_qa_data(self, qa_file: str) -> Dict[str, List[Dict]]:
        """Load QA data from standardized qa.json file."""
        logger.info(f"Loading QA data from {qa_file}")
        with open(qa_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        entity_qa_map = {}
        for result in data.get("results", []):
            entity_id = result.get("entity_id")
            qa_pairs = result.get("qa_pairs", [])
            if entity_id and qa_pairs:
                entity_qa_map[str(entity_id)] = qa_pairs

        logger.info(f"Loaded QA data for {len(entity_qa_map)} entities")
        return entity_qa_map

    def load_logs_data(self, logs_dir: str) -> Dict[str, Dict]:
        """Load logs data from logs directory structure."""
        logs_path = Path(logs_dir)
        entity_logs_map = {}

        # Build glob pattern based on entity prefix
        patterns = [f"{self.entity_prefix}_*"]

        # GLOBEM has special patterns
        if self.scenario == "globem":
            patterns = ["user_*", "INS-W_*", "pair_*"]

        for pattern in patterns:
            for entity_dir in logs_path.glob(pattern):
                if not entity_dir.is_dir():
                    continue

                # Extract entity ID from directory name
                entity_id = entity_dir.name
                if entity_id.startswith(f"{self.entity_prefix}_"):
                    entity_id = entity_id[len(self.entity_prefix) + 1:]

                entity_data = {
                    "entity_id": entity_id,
                    "message_wise_context": "",
                    "message_wise_insights_list": [],
                    "chat_wise_context": "",
                    "insights_file": "",
                    "session_stats_file": ""
                }

                # Use the run recorded by the newest session summary. Both files
                # have the same timestamp, but a run can produce no insights.
                session_stats_file = max(entity_dir.glob("session_stats*.json"), default=None)
                if session_stats_file is not None:
                    attempt = session_stats_file.stem[len("session_stats"):]
                    insights_file = entity_dir / f"insights{attempt}.csv"
                else:
                    insights_file = max(entity_dir.glob("insights*.csv"), default=None)

                if insights_file is not None and insights_file.is_file():
                    try:
                        df = pd.read_csv(insights_file, dtype=str, keep_default_na=False)
                        if 'insight' in df.columns:
                            insights_series = df['insight'].fillna('').astype(str)
                            mask_valid = ~insights_series.str.contains('NO INSIGHT', case=False, na=False)
                            valid_insights = insights_series[mask_valid].tolist()
                            entity_data["message_wise_insights_list"] = [str(x).strip() for x in valid_insights if str(x).strip()]
                            entity_data["message_wise_context"] = "\n\n".join(entity_data["message_wise_insights_list"])
                            entity_data["insights_file"] = insights_file.name
                    except Exception as e:
                        logger.error(f"Error reading insights file {insights_file}: {e}")

                # Load session stats JSON
                if session_stats_file is not None:
                    try:
                        with open(session_stats_file, 'r', encoding='utf-8') as f:
                            session_data = json.load(f)
                            final_summary = session_data.get("final_summary", "")
                            entity_data["chat_wise_context"] = self.clean_final_summary(final_summary)
                            entity_data["session_stats_file"] = session_stats_file.name
                    except Exception as e:
                        logger.error(f"Error reading session stats file {session_stats_file}: {e}")

                if entity_data["message_wise_context"] or entity_data["chat_wise_context"]:
                    entity_logs_map[entity_id] = entity_data

        logger.info(f"Loaded logs data for {len(entity_logs_map)} entities")
        return entity_logs_map

    def clean_final_summary(self, final_summary: str) -> str:
        """Clean metadata from final_summary."""
        if not final_summary:
            return ""

        lines = final_summary.split('\n')
        cleaned_lines = []

        for line in lines:
            if "- INFO -" in line:
                break
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines).rstrip()

    def extract_contexts(self, entity_logs_data: Dict) -> Tuple[str, str, List[str]]:
        """Extract message-wise and chat-wise contexts from logs data."""
        message_wise_context = entity_logs_data.get("message_wise_context", "")
        message_wise_insights_list = entity_logs_data.get("message_wise_insights_list", [])

        chat_wise_context = ""
        final_summary = entity_logs_data.get("chat_wise_context", "")
        if final_summary:
            chat_wise_context = self.clean_final_summary(final_summary)

        return message_wise_context, chat_wise_context, message_wise_insights_list

    # =========================================================================
    # Statistics Methods
    # =========================================================================

    def calculate_summary_stats(self, results: Dict, use_qa_accuracy: bool = False) -> Dict[str, Any]:
        """Calculate summary statistics for evaluation results."""
        summary = {}

        # Determine metric names based on evaluation type
        if use_qa_accuracy:
            # GLOBEM uses QA accuracy metrics
            correct_key = "CORRECT"
            incorrect_key = "INCORRECT"
            insufficient_key = "NO_IDEA"
        else:
            # MIMIC/10-K use context quality metrics
            correct_key = "CORRECT_INFO"
            incorrect_key = "INCORRECT_INFO"
            insufficient_key = "INSUFFICIENT_INFO"

        for context_type in ["message_wise_context_results", "chat_wise_context_results"]:
            if not results.get(context_type):
                continue

            result_list = results[context_type]
            total = len(result_list)

            if use_qa_accuracy:
                correct = sum(1 for r in result_list if r.get("answer_type") == correct_key)
                incorrect = sum(1 for r in result_list if r.get("answer_type") == incorrect_key)
                insufficient = sum(1 for r in result_list if r.get("answer_type") == insufficient_key)
            else:
                correct = sum(1 for r in result_list if r.get("context_quality") == correct_key)
                incorrect = sum(1 for r in result_list if r.get("context_quality") == incorrect_key)
                insufficient = sum(1 for r in result_list if r.get("context_quality") == insufficient_key)

            api_failed = sum(1 for r in result_list if r.get("context_quality") == "API_FAILED" or r.get("answer_type") == "API_FAILED")
            errors = sum(1 for r in result_list if r.get("error"))

            key = context_type.replace("_results", "")
            summary[key] = {
                "total_questions": total,
                "correct": correct,
                "incorrect": incorrect,
                "insufficient": insufficient,
                "api_failed": api_failed,
                "errors": errors,
                "correct_percentage": (correct / total * 100) if total > 0 else 0,
                "incorrect_percentage": (incorrect / total * 100) if total > 0 else 0,
                "insufficient_percentage": (insufficient / total * 100) if total > 0 else 0,
                "api_failed_percentage": (api_failed / total * 100) if total > 0 else 0
            }

        return summary

    def calculate_overall_stats(self, all_results: List[Dict], use_qa_accuracy: bool = False) -> Dict[str, Any]:
        """Calculate overall statistics across all entities."""
        overall = {
            "total_entities": len(all_results),
            "message_wise_context": {
                "correct_percentages": [],
                "incorrect_percentages": [],
                "insufficient_percentages": [],
                "api_failed_percentages": [],
                "total_questions": 0,
                "total_correct": 0,
                "total_incorrect": 0,
                "total_insufficient": 0,
                "total_api_failed": 0,
                "total_expected_questions": 0,
            },
            "chat_wise_context": {
                "correct_percentages": [],
                "incorrect_percentages": [],
                "insufficient_percentages": [],
                "api_failed_percentages": [],
                "total_questions": 0,
                "total_correct": 0,
                "total_incorrect": 0,
                "total_insufficient": 0,
                "total_api_failed": 0,
            }
        }

        for result in all_results:
            overall["message_wise_context"]["total_expected_questions"] += result.get("total_qa_pairs", 0)

            for context_type in ["message_wise_context", "chat_wise_context"]:
                if context_type not in result.get("summary", {}):
                    continue

                stats = result["summary"][context_type]
                overall[context_type]["correct_percentages"].append(stats.get("correct_percentage", 0))
                overall[context_type]["incorrect_percentages"].append(stats.get("incorrect_percentage", 0))
                overall[context_type]["insufficient_percentages"].append(stats.get("insufficient_percentage", 0))
                overall[context_type]["api_failed_percentages"].append(stats.get("api_failed_percentage", 0))
                overall[context_type]["total_questions"] += stats.get("total_questions", 0)
                overall[context_type]["total_correct"] += stats.get("correct", 0)
                overall[context_type]["total_incorrect"] += stats.get("incorrect", 0)
                overall[context_type]["total_insufficient"] += stats.get("insufficient", 0)
                overall[context_type]["total_api_failed"] += stats.get("api_failed", 0)

        # Calculate averages
        for context_type in ["message_wise_context", "chat_wise_context"]:
            ctx = overall[context_type]
            if ctx["correct_percentages"]:
                ctx["average_correct_percentage"] = statistics.mean(ctx["correct_percentages"])
                ctx["average_incorrect_percentage"] = statistics.mean(ctx["incorrect_percentages"])
                ctx["average_insufficient_percentage"] = statistics.mean(ctx["insufficient_percentages"])
                ctx["average_api_failed_percentage"] = statistics.mean(ctx["api_failed_percentages"])
                ctx["median_correct_percentage"] = statistics.median(ctx["correct_percentages"])

                total_q = ctx["total_questions"]
                if total_q > 0:
                    ctx["overall_correct_percentage"] = ctx["total_correct"] / total_q * 100
                    ctx["overall_incorrect_percentage"] = ctx["total_incorrect"] / total_q * 100
                    ctx["overall_insufficient_percentage"] = ctx["total_insufficient"] / total_q * 100
                    ctx["overall_api_failed_percentage"] = ctx["total_api_failed"] / total_q * 100

        return overall

    def calculate_cumulative_message_stats(self, all_results: List[Dict], use_qa_accuracy: bool = False) -> Dict[str, Any]:
        """Calculate performance metrics at different message thresholds (top-k analysis)."""
        thresholds = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]

        cumulative_stats = {
            "thresholds": thresholds,
            "stats_by_threshold": {}
        }

        for threshold in thresholds:
            threshold_results = {
                "threshold": threshold,
                "total_qa_pairs": 0,
                "correct": 0,
                "incorrect": 0,
                "insufficient": 0,
                "correct_percentage": 0.0,
                "incorrect_percentage": 0.0,
                "insufficient_percentage": 0.0
            }

            for entity_result in all_results:
                for qa_result in entity_result.get("message_wise_context_results", []):
                    threshold_results["total_qa_pairs"] += 1

                    supporting_indices = qa_result.get("supporting_message_indices", [])
                    contradicting_indices = qa_result.get("contradicting_message_indices", [])

                    if use_qa_accuracy:
                        original_quality = qa_result.get("answer_type", "NO_IDEA")
                        is_correct = original_quality == "CORRECT"
                        is_incorrect = original_quality == "INCORRECT"
                    else:
                        original_quality = qa_result.get("context_quality", "INSUFFICIENT_INFO")
                        is_correct = original_quality == "CORRECT_INFO"
                        is_incorrect = original_quality == "INCORRECT_INFO"

                    if is_correct:
                        if supporting_indices and max(supporting_indices) < threshold:
                            threshold_results["correct"] += 1
                        else:
                            threshold_results["insufficient"] += 1
                    elif is_incorrect:
                        if contradicting_indices and max(contradicting_indices) < threshold:
                            threshold_results["incorrect"] += 1
                        else:
                            threshold_results["insufficient"] += 1
                    else:
                        threshold_results["insufficient"] += 1

            total = threshold_results["total_qa_pairs"]
            if total > 0:
                threshold_results["correct_percentage"] = threshold_results["correct"] / total * 100
                threshold_results["incorrect_percentage"] = threshold_results["incorrect"] / total * 100
                threshold_results["insufficient_percentage"] = threshold_results["insufficient"] / total * 100

            cumulative_stats["stats_by_threshold"][f"top_{threshold}"] = threshold_results

        return cumulative_stats

    def print_summary(self, overall_stats: Dict, cumulative_message_stats: Dict = None) -> str:
        """Print and return evaluation summary."""
        lines = []
        lines.append("=" * 80)
        lines.append(f"{self.scenario.upper()} CONTEXT QUALITY EVALUATION SUMMARY")
        lines.append("=" * 80)
        lines.append(f"Total entities evaluated: {overall_stats.get('total_entities', 0)}")

        for context_type in ["message_wise_context", "chat_wise_context"]:
            ctx = overall_stats.get(context_type, {})
            if not ctx.get("correct_percentages"):
                continue

            label = "Message-wise" if "message" in context_type else "Chat-wise"
            lines.append(f"\n{label} Context Results:")
            lines.append(f"  Total questions: {ctx.get('total_questions', 0)}")
            lines.append(f"  Correct: {ctx.get('total_correct', 0)} ({ctx.get('overall_correct_percentage', 0):.2f}%)")
            lines.append(f"  Incorrect: {ctx.get('total_incorrect', 0)} ({ctx.get('overall_incorrect_percentage', 0):.2f}%)")
            lines.append(f"  Insufficient: {ctx.get('total_insufficient', 0)} ({ctx.get('overall_insufficient_percentage', 0):.2f}%)")
            lines.append(f"  API Failed: {ctx.get('total_api_failed', 0)} ({ctx.get('overall_api_failed_percentage', 0):.2f}%)")

        if cumulative_message_stats:
            lines.append("\nCumulative Message-wise Statistics (Top-K Analysis):")
            lines.append(f"  {'Threshold':<12} {'Correct %':<12} {'Incorrect %':<15} {'Insufficient %':<15}")
            lines.append(f"  {'-'*12} {'-'*12} {'-'*15} {'-'*15}")

            for threshold in cumulative_message_stats.get("thresholds", []):
                key = f"top_{threshold}"
                if key in cumulative_message_stats.get("stats_by_threshold", {}):
                    stats = cumulative_message_stats["stats_by_threshold"][key]
                    lines.append(f"  {'Top-' + str(threshold):<12} "
                               f"{stats['correct_percentage']:>10.2f}%  "
                               f"{stats['incorrect_percentage']:>13.2f}%  "
                               f"{stats['insufficient_percentage']:>13.2f}%")

        lines.append("=" * 80)

        summary_text = "\n".join(lines)
        logger.info(summary_text)
        return summary_text
