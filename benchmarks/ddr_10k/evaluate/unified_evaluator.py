#!/usr/bin/env python3
"""
Unified evaluator for all DDR_Bench scenarios.

Combines BaseEvaluator with scenario-specific prompts.
"""

import json
import threading
import time
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Any

from .base_evaluator import BaseEvaluator
from .prompts import get_prompts, get_entity_prefix

logger = logging.getLogger(__name__)


class UnifiedEvaluator(BaseEvaluator):
    """Unified evaluator supporting all scenarios (MIMIC, 10-K, GLOBEM)."""

    _CONTEXT_QUALITY_LABELS = ("CORRECT_INFO", "INCORRECT_INFO", "INSUFFICIENT_INFO")

    def __init__(self, scenario: str = "mimic", **kwargs):
        entity_prefix = get_entity_prefix(scenario)
        super().__init__(scenario=scenario, entity_prefix=entity_prefix, **kwargs)

        self.prompts = get_prompts(scenario)
        self.use_qa_accuracy = self.prompts.get("use_qa_accuracy", False)
        self._print_lock = threading.RLock()  # reentrant for thread-safe progress output
        self._completed_count = 0
        self._total_count = 0

    def _ts_print(self, *args, **kwargs):
        """Thread-safe print — prevents interleaved output from parallel workers."""
        with self._print_lock:
            print(*args, **kwargs)

    def evaluate_qa_with_message_list(self, qa_pair: Dict, insights_list: List[str], filename: str = "unknown") -> Dict[str, Any]:
        """Evaluate QA with individual insights (message-wise context)."""
        question = qa_pair.get("question", "")
        ground_truth = qa_pair.get("answer", "")
        source_text = qa_pair.get("source_text", "")
        target_concepts = qa_pair.get("target_concepts", [])

        if not question or not ground_truth:
            return {"error": "Invalid QA pair"}

        if not insights_list:
            if self.use_qa_accuracy:
                return {
                    "original_question": question,
                    "ground_truth": ground_truth,
                    "model_answer": "NO IDEA",
                    "answer_type": "NO_IDEA",
                    "context_type": "message_wise",
                    "source_text": source_text,
                    "target_concepts": target_concepts,
                    "supporting_message_indices": [],
                    "contradicting_message_indices": []
                }
            else:
                return {
                    "original_question": question,
                    "ground_truth": ground_truth,
                    "context_quality": "INSUFFICIENT_INFO",
                    "evidence_text": "",
                    "reasoning": "No insights available",
                    "context_type": "message_wise",
                    "source_text": source_text,
                    "target_concepts": target_concepts,
                    "supporting_message_indices": [],
                    "contradicting_message_indices": []
                }

        # Build numbered context
        numbered_context = ""
        for idx, insight in enumerate(insights_list):
            numbered_context += f"[Message {idx}]: {insight}\n\n"

        # Create prompt
        messages = [
            {"role": "system", "content": self.prompts["message_system"]},
            {"role": "user", "content": self.prompts["message_user"].format(
                numbered_context=numbered_context,
                question=question,
                ground_truth=ground_truth
            )}
        ]

        # Call LLM
        result = self.call_llm_api(messages, max_tokens=self.eval_max_tokens)

        if not result:
            logger.warning(f"Failed to get evaluation for question: {question[:100]}...")
            if self.use_qa_accuracy:
                return {
                    "original_question": question,
                    "ground_truth": ground_truth,
                    "model_answer": "",
                    "answer_type": "API_FAILED",
                    "is_correct": False,
                    "context_type": "message_wise",
                    "source_text": source_text,
                    "target_concepts": target_concepts,
                    "supporting_message_indices": [],
                    "contradicting_message_indices": [],
                    "error": "Failed to get evaluation"
                }
            else:
                return {
                    "original_question": question,
                    "ground_truth": ground_truth,
                    "context_quality": "API_FAILED",
                    "evidence_text": "",
                    "reasoning": "Failed to get evaluation from API",
                    "context_type": "message_wise",
                    "source_text": source_text,
                    "target_concepts": target_concepts,
                    "supporting_message_indices": [],
                    "contradicting_message_indices": [],
                    "error": "Failed to get evaluation"
                }

        # Parse result based on evaluation type
        if self.use_qa_accuracy:
            return self._parse_qa_accuracy_result(result, question, ground_truth, source_text, "message_wise", target_concepts)
        else:
            return self._parse_context_quality_result(result, question, ground_truth, source_text, "message_wise", target_concepts)

    def evaluate_qa_with_context(self, qa_pair: Dict, context: str, context_type: str, filename: str = "unknown") -> Dict[str, Any]:
        """Evaluate QA with full context (chat-wise)."""
        question = qa_pair.get("question", "")
        ground_truth = qa_pair.get("answer", "")
        source_text = qa_pair.get("source_text", "")
        target_concepts = qa_pair.get("target_concepts", [])

        if not question or not ground_truth:
            return {"error": "Invalid QA pair"}

        # Create prompt
        messages = [
            {"role": "system", "content": self.prompts["chat_system"]},
            {"role": "user", "content": self.prompts["chat_user"].format(
                context=context,
                question=question,
                ground_truth=ground_truth
            )}
        ]

        # Call LLM
        result = self.call_llm_api(messages, max_tokens=self.eval_max_tokens)

        if not result:
            logger.warning(f"Failed to get evaluation for question: {question[:100]}...")
            if self.use_qa_accuracy:
                return {
                    "original_question": question,
                    "ground_truth": ground_truth,
                    "model_answer": "",
                    "answer_type": "API_FAILED",
                    "is_correct": False,
                    "context_type": context_type,
                    "source_text": source_text,
                    "target_concepts": target_concepts,
                    "error": "Failed to get evaluation"
                }
            else:
                return {
                    "original_question": question,
                    "ground_truth": ground_truth,
                    "context_quality": "API_FAILED",
                    "evidence_text": "",
                    "reasoning": "Failed to get evaluation from API",
                    "context_type": context_type,
                    "source_text": source_text,
                    "target_concepts": target_concepts,
                    "error": "Failed to get evaluation"
                }

        # Parse result
        if self.use_qa_accuracy:
            return self._parse_qa_accuracy_result(result, question, ground_truth, source_text, context_type, target_concepts)
        else:
            return self._parse_context_quality_result(result, question, ground_truth, source_text, context_type, target_concepts)

    def _parse_context_quality_result(self, result: str, question: str, ground_truth: str, source_text: str, context_type: str, target_concepts: List[str] = None) -> Dict[str, Any]:
        """Parse context quality evaluation result (MIMIC/10-K)."""
        if target_concepts is None:
            target_concepts = []
        result = result.replace("```json", "").replace("```", "")

        try:
            data = json.loads(result)
            return {
                "original_question": question,
                "ground_truth": ground_truth,
                "context_quality": data.get("context_quality", "INSUFFICIENT_INFO"),
                "supporting_message_indices": data.get("supporting_message_indices", []),
                "contradicting_message_indices": data.get("contradicting_message_indices", []),
                "evidence_text": data.get("evidence_text", ""),
                "reasoning": data.get("reasoning", ""),
                "context_type": context_type,
                "source_text": source_text,
                "target_concepts": target_concepts
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse result: {e}")
            # Fallback
            # Prefer the context_quality field of a truncated or wrapped JSON
            # reply. Match labels on word boundaries: a plain substring check
            # finds "CORRECT_INFO" inside "INCORRECT_INFO".
            upper = result.upper()
            field = re.search(r'"CONTEXT_QUALITY"\s*:\s*"(\w+)"', upper)
            if field and field.group(1) in self._CONTEXT_QUALITY_LABELS:
                quality = field.group(1)
            else:
                quality = next(
                    (label for label in self._CONTEXT_QUALITY_LABELS
                     if re.search(rf"\b{label}\b", upper)),
                    "INSUFFICIENT_INFO",
                )

            return {
                "original_question": question,
                "ground_truth": ground_truth,
                "context_quality": quality,
                "supporting_message_indices": [],
                "contradicting_message_indices": [],
                "evidence_text": "",
                "reasoning": result,
                "context_type": context_type,
                "source_text": source_text,
                "target_concepts": target_concepts
            }

    def _parse_qa_accuracy_result(self, result: str, question: str, ground_truth: str, source_text: str, context_type: str, target_concepts: List[str] = None) -> Dict[str, Any]:
        """Parse QA accuracy evaluation result (GLOBEM)."""
        if target_concepts is None:
            target_concepts = []
        model_answer = result

        # Extract supporting indices
        supporting_indices = []
        indices_match = re.search(r'[Uu]sed messages?:?\s*([\d,\s]+)', result)
        if indices_match:
            indices_str = indices_match.group(1)
            supporting_indices = [int(x.strip()) for x in indices_str.split(',') if x.strip().isdigit()]
        else:
            message_refs = re.findall(r'\[Message (\d+)\]', result)
            if message_refs:
                supporting_indices = [int(x) for x in message_refs]

        # Check for NO IDEA
        if "NO IDEA" in result.upper():
            return {
                "original_question": question,
                "ground_truth": ground_truth,
                "model_answer": model_answer,
                "answer_type": "NO_IDEA",
                "context_type": context_type,
                "source_text": source_text,
                "target_concepts": target_concepts,
                "supporting_message_indices": supporting_indices,
                "contradicting_message_indices": []
            }

        # Check accuracy
        accuracy_messages = [
            {"role": "system", "content": self.prompts["accuracy_system"]},
            {"role": "user", "content": self.prompts["accuracy_user"].format(
                ground_truth=ground_truth,
                model_answer=model_answer
            )}
        ]

        accuracy_result = self.call_llm_api(accuracy_messages, max_tokens=self.eval_max_tokens)
        is_correct = accuracy_result.strip().upper() == "YES" if accuracy_result else False

        return {
            "original_question": question,
            "ground_truth": ground_truth,
            "model_answer": model_answer,
            "answer_type": "CORRECT" if is_correct else "INCORRECT",
            "is_correct": is_correct,
            "context_type": context_type,
            "source_text": source_text,
            "target_concepts": target_concepts,
            "supporting_message_indices": supporting_indices if is_correct else [],
            "contradicting_message_indices": supporting_indices if not is_correct else []
        }

    def evaluate_entity(self, entity_id: str, qa_pairs: List[Dict], message_wise_context: str, chat_wise_context: str, message_wise_insights_list: List[str], filename: str = "unknown") -> Dict[str, Any]:
        """Evaluate all QA pairs for an entity."""
        logger.info(f"Evaluating {self.entity_prefix} {entity_id} with {len(qa_pairs)} QA pairs")

        results = {
            "entity_id": entity_id,
            "total_qa_pairs": len(qa_pairs),
            "message_wise_context_results": [],
            "chat_wise_context_results": [],
            "summary": {}
        }

        # Message-wise evaluation
        if message_wise_insights_list:
            logger.info(f"Evaluating with message-wise context ({len(message_wise_insights_list)} messages)")
            for qa_pair in qa_pairs:
                result = self.evaluate_qa_with_message_list(qa_pair, message_wise_insights_list, filename)
                results["message_wise_context_results"].append(result)
                time.sleep(0.5)

        # Chat-wise evaluation
        if chat_wise_context:
            logger.info(f"Evaluating with chat-wise context")
            for qa_pair in qa_pairs:
                result = self.evaluate_qa_with_context(qa_pair, chat_wise_context, "chat_wise", filename)
                results["chat_wise_context_results"].append(result)
                time.sleep(0.5)

        results["summary"] = self.calculate_summary_stats(results, self.use_qa_accuracy)
        return results

    def _evaluate_one_entity(self, entity_id: str, qa_pairs: List[Dict],
                             entity_logs_data: Dict) -> Dict[str, Any]:
        """Evaluate a single entity (thread-safe). Returns result dict or None on skip."""
        insights_file = entity_logs_data.get("insights_file", "unknown")
        session_stats_file = entity_logs_data.get("session_stats_file", "unknown")
        filename_info = f"insights:{insights_file}, session_stats:{session_stats_file}"

        message_wise_context, chat_wise_context, message_wise_insights_list = \
            self.extract_contexts(entity_logs_data)

        if not message_wise_context and not chat_wise_context:
            logger.warning(f"Entity {entity_id} has no valid context")
            return None

        entity_results = self.evaluate_entity(
            entity_id, qa_pairs,
            message_wise_context, chat_wise_context,
            message_wise_insights_list, filename_info
        )

        # Log per-entity progress
        summary = entity_results.get("summary", {})
        msg_stats = summary.get("message_wise_context", {})
        chat_stats = summary.get("chat_wise_context", {})

        msg_summary = f"Message-wise: {msg_stats.get('correct_percentage', 0):.1f}% correct" if msg_stats else "Message-wise: N/A"
        chat_summary = f"Chat-wise: {chat_stats.get('correct_percentage', 0):.1f}% correct" if chat_stats else "Chat-wise: N/A"
        logger.info(f"Completed {entity_id} - {msg_summary} | {chat_summary}")

        return entity_results

    def _run_evaluation_serial(self, common_entities: List[str],
                                entity_qa_map: Dict, entity_logs_map: Dict,
                                all_results: List, total: int, test_mode: bool):
        """Original serial evaluation loop."""
        for i, entity_id in enumerate(common_entities, 1):
            if test_mode and i > 1:
                break

            entity_logs_data = entity_logs_map[entity_id]
            qa_pairs = entity_qa_map[entity_id]

            logger.info(f"Processing {entity_id} ({i}/{total})")

            entity_results = self._evaluate_one_entity(entity_id, qa_pairs, entity_logs_data)
            if entity_results is not None:
                all_results.append(entity_results)

    def _run_evaluation_parallel(self, common_entities: List[str],
                                  entity_qa_map: Dict, entity_logs_map: Dict,
                                  all_results: List, total: int, test_mode: bool,
                                  max_workers: int):
        """Parallel evaluation using ThreadPoolExecutor.

        Each entity's QA evaluation runs in its own thread. All API calls
        are I/O-bound (HTTP requests), so threads work well here.
        """
        self._completed_count = 0
        self._total_count = total

        # Build work items (respect test_mode)
        work_items = []
        for entity_id in common_entities:
            if test_mode and len(work_items) >= 1:
                break
            work_items.append(entity_id)
        self._total_count = len(work_items)

        self._ts_print(f"Parallel evaluation: {max_workers} workers, {self._total_count} entities")
        self._ts_print("=" * 50)

        def _worker(entity_id: str) -> Dict[str, Any]:
            """Thread worker — evaluates one entity."""
            qa_pairs = entity_qa_map[entity_id]
            entity_logs_data = entity_logs_map[entity_id]

            try:
                result = self._evaluate_one_entity(entity_id, qa_pairs, entity_logs_data)
            except Exception as e:
                logger.error(f"Entity {entity_id} evaluation crashed: {e}")
                result = None

            with self._print_lock:
                self._completed_count += 1
                if result is not None:
                    summary = result.get("summary", {})
                    msg_stats = summary.get("message_wise_context", {})
                    chat_stats = summary.get("chat_wise_context", {})
                    ms = f"{msg_stats.get('correct_percentage', 0):.1f}%" if msg_stats else "N/A"
                    cs = f"{chat_stats.get('correct_percentage', 0):.1f}%" if chat_stats else "N/A"
                    print(f"[{self._completed_count}/{self._total_count}] ✅ {entity_id}  |  msg: {ms}  chat: {cs}")
                else:
                    print(f"[{self._completed_count}/{self._total_count}] ⚠️ {entity_id} (skipped)")

            return result

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_worker, eid): eid for eid in work_items}
            for future in as_completed(futures):
                entity_id = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        all_results.append(result)
                except Exception as e:
                    self._ts_print(f"   ❌ {entity_id} worker thread crashed: {e}")

    def run_evaluation(self, qa_file: str, logs_dir: str, output_file: str,
                       test_mode: bool = False, parallel: int = 1):
        """Run the complete evaluation.

        Args:
            qa_file: Path to QA JSON file.
            logs_dir: Path to agent logs directory.
            output_file: Path for output results JSON.
            test_mode: If True, only evaluate the first entity.
            parallel: Number of entities to evaluate in parallel (default 1 = serial).
        """
        logger.info(f"Starting {self.scenario.upper()} evaluation")
        if test_mode:
            logger.info("TEST MODE: Only evaluating first entity")
        if parallel > 1:
            logger.info(f"Parallel mode: {parallel} workers")

        # Load data (serial — fast, I/O-bound reads)
        entity_qa_map = self.load_qa_data(qa_file)
        entity_logs_map = self.load_logs_data(logs_dir)

        if not entity_qa_map:
            logger.error("No QA data loaded")
            return

        if not entity_logs_map:
            logger.error("No logs data loaded")
            return

        # Find common entities
        common_entities = sorted(set(entity_qa_map.keys()) & set(entity_logs_map.keys()))
        logger.info(f"Found {len(common_entities)} common entities")

        # Run evaluation
        all_results: List[Dict[str, Any]] = []
        total = 1 if test_mode else len(common_entities)

        if parallel > 1:
            self._run_evaluation_parallel(
                common_entities, entity_qa_map, entity_logs_map,
                all_results, total, test_mode, parallel
            )
        else:
            self._run_evaluation_serial(
                common_entities, entity_qa_map, entity_logs_map,
                all_results, total, test_mode
            )

        # Sort results by entity_id for deterministic output
        all_results.sort(key=lambda r: str(r.get("entity_id", "")))

        # Calculate statistics (serial — pure computation, fast)
        overall_stats = self.calculate_overall_stats(all_results, self.use_qa_accuracy)
        cumulative_stats = self.calculate_cumulative_message_stats(all_results, self.use_qa_accuracy)

        # Save results
        output_data = {
            "evaluation_metadata": {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "scenario": self.scenario,
                "qa_file": qa_file,
                "logs_dir": logs_dir,
                "total_entities_evaluated": len(all_results),
                "test_mode": test_mode,
                "provider": self.provider,
                "model": self.openai_model,
                "temperature": self.eval_temperature,
                "parallel_workers": parallel,
            },
            "overall_statistics": overall_stats,
            "cumulative_message_statistics": cumulative_stats,
            "entity_results": all_results
        }

        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Saved evaluation to {output_path}")
        self.print_summary(overall_stats, cumulative_stats)
