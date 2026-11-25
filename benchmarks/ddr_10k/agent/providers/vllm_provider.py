#!/usr/bin/env python3
"""
VLLM Local Model API Provider
"""

import json
import re
import time
from typing import Any, Dict, List

import aiohttp

from .base import LLMProvider, log_llm_call, system_logger

if __package__ == "agent.providers":
    from config import openai_compatible_endpoint, override_url_port
else:
    from ...config import openai_compatible_endpoint, override_url_port


class VLLMProvider(LLMProvider):
    """VLLM local model API provider"""

    def __init__(self, base_url: str = None, model: str = "", api_key: str = "EMPTY", port: int = None):
        super().__init__()
        if not base_url:
            raise ValueError("A base URL must be provided through configuration or environment.")
        if port and port > 0 and "://" not in base_url:
            raise ValueError("A base URL with an explicit scheme is required when overriding the port.")

        self.base_url = override_url_port(base_url, port)
        self._models_url = openai_compatible_endpoint(self.base_url, "models")
        self._chat_url = openai_compatible_endpoint(self.base_url, "chat/completions")
        self.model = model
        self.api_key = api_key
        self.port = port
        self._model_info = None  # Cache for model information
        self._supports_function_calling = None  # Cache for function calling support

    async def _get_model_info(self) -> Dict[str, Any]:
        """Get model information from VLLM server"""
        if self._model_info is not None:
            return self._model_info

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._models_url) as response:
                    if response.status == 200:
                        models_data = await response.json()
                        # Find our model in the list
                        for model_data in models_data.get("data", []):
                            if model_data.get("id") == self.model:
                                self._model_info = model_data
                                return self._model_info

                    # Fallback: create basic model info
                    self._model_info = {
                        "id": self.model,
                        "object": "model",
                        "owned_by": "vllm"
                    }
                    return self._model_info
        except Exception as e:
            system_logger.warning(f"Failed to get model info from VLLM: {e}")
            # Fallback: create basic model info
            self._model_info = {
                "id": self.model,
                "object": "model",
                "owned_by": "vllm"
            }
            return self._model_info

    async def _check_function_calling_support(self) -> bool:
        """Check if the VLLM server supports OpenAI-compatible function calling"""
        if self._supports_function_calling is not None:
            return self._supports_function_calling

        try:
            # Test with a simple function calling request
            test_messages = [{"role": "user", "content": "Hello"}]
            test_tools = [{
                "type": "function",
                "function": {
                    "name": "test_function",
                    "description": "A test function",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            }]

            payload = {
                "model": self.model,
                "messages": test_messages,
                "max_tokens": 1,
                "tools": test_tools,
                "tool_choice": "auto"
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.post(
                    self._chat_url,
                    json=payload,
                    headers=headers
                ) as response:
                    # If request succeeds without error, function calling is supported
                    if response.status == 200:
                        self._supports_function_calling = True
                        system_logger.info("VLLM server supports OpenAI function calling")
                    else:
                        self._supports_function_calling = False
                        system_logger.info("VLLM server does not support OpenAI function calling")
                    return self._supports_function_calling

        except Exception as e:
            system_logger.info(f"Function calling support check failed, assuming not supported: {e}")
            self._supports_function_calling = False
            return self._supports_function_calling

    def _parse_generic_tool_calls(self, output_text: str) -> List[Dict[str, Any]]:
        """Parse tool calls from model output using common patterns"""
        tool_calls = []

        # Add debug log
        system_logger.info(f"🔧 VLLM DEBUG: Parsing tool calls from output (length: {len(output_text)})")
        system_logger.info(f"🔧 VLLM DEBUG: Output preview: {repr(output_text[:500])}")

        # Pattern 1: Qwen2.5 XML format <tool_call>{"name": "...", "arguments": {...}}</tool_call>
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        matches = re.findall(xml_pattern, output_text, re.DOTALL)
        system_logger.info(f"🔧 VLLM DEBUG: XML pattern matches: {matches}")

        for i, match in enumerate(matches):
            try:
                tool_data = json.loads(match)
                if "name" in tool_data:
                    tool_calls.append({
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tool_data.get("name", ""),
                            "arguments": json.dumps(tool_data.get("arguments", {}))
                        }
                    })
            except json.JSONDecodeError as e:
                system_logger.warning(f"Failed to parse XML tool call: {match}, error: {e}")

        # Pattern 2: Gemma-style JSON format without XML tags
        if not tool_calls:
            # Look for JSON objects that might be tool calls
            json_pattern = r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}'
            json_matches = re.findall(json_pattern, output_text, re.DOTALL)
            system_logger.info(f"🔧 VLLM DEBUG: JSON pattern matches: {json_matches}")

            for i, match in enumerate(json_matches):
                try:
                    tool_data = json.loads(match)
                    if "name" in tool_data and "arguments" in tool_data:
                        tool_calls.append({
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": tool_data.get("name", ""),
                                "arguments": json.dumps(tool_data.get("arguments", {}))
                            }
                        })
                except json.JSONDecodeError as e:
                    system_logger.warning(f"Failed to parse JSON tool call: {match}, error: {e}")

        # Pattern 2.5: Gemma-style Markdown code block format
        if not tool_calls:
            # Look for ```tool_call ... ``` format
            markdown_pattern = r'```(?:tool_call)?\s*\n\s*(\{.*?\})\s*\n\s*```'
            markdown_matches = re.findall(markdown_pattern, output_text, re.DOTALL)

            for i, match in enumerate(markdown_matches):
                try:
                    tool_data = json.loads(match)
                    if "name" in tool_data:
                        tool_calls.append({
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": tool_data.get("name", ""),
                                "arguments": json.dumps(tool_data.get("arguments", {}))
                            }
                        })
                except json.JSONDecodeError as e:
                    system_logger.warning(f"Failed to parse Markdown tool call: {match}, error: {e}")

        # Pattern 3: Simple function call format: function_name(arg1, arg2)
        if not tool_calls:
            simple_pattern = r'(\w+)\s*\(\s*([^)]*)\s*\)'
            simple_matches = re.findall(simple_pattern, output_text)
            system_logger.info(f"🔧 VLLM DEBUG: Simple pattern matches: {simple_matches}")
            if simple_matches:
                pass  # Simple function calls detected
                # Convert simple function calls to tool call format
                for i, (func_name, args_str) in enumerate(simple_matches):
                    # Try to parse arguments as JSON or create a simple dict
                    try:
                        # Try to parse as JSON first
                        args = json.loads(f"{{{args_str}}}")
                    except json.JSONDecodeError:
                        # Fall back to simple key-value parsing
                        args = {}
                        if args_str.strip():
                            # Parse "key=value, key2=value2" format
                            for pair in args_str.split(','):
                                if '=' in pair:
                                    key, value = pair.split('=', 1)
                                    args[key.strip()] = value.strip().strip('"\'')

                    tool_calls.append({
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": func_name.strip(),
                            "arguments": json.dumps(args)
                        }
                    })

        if not tool_calls:
            system_logger.info("No tool calls detected in model output")
        else:
            system_logger.info(f"🔧 VLLM DEBUG: Final parsed tool calls: {tool_calls}")

        return tool_calls

    async def _generate_response_impl(self, messages: List[Dict[str, str]], **kwargs) -> str:
        start_time = time.time()

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._get_max_tokens()),
            "temperature": kwargs.get("temperature", 0.0),
            "stream": False
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        input_data = {
            "url": self._chat_url,
            "payload": payload,
            "headers": {k: v for k, v in headers.items() if k != "Authorization"}  # Don't log API key
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._chat_url,
                    json=payload,
                    headers=headers
                ) as response:
                    end_time = time.time()

                    if response.status != 200:
                        error_text = await response.text()
                        error_metadata = {
                            "model": self.model,
                            "base_url": self.base_url,
                            "status_code": response.status,
                            "error": error_text,
                            "error_type": "HTTP_ERROR"
                        }

                        # Log the failed API call
                        log_llm_call(
                            provider_name=self.get_provider_name(),
                            call_type="chat_completion_error",
                            input_data=input_data,
                            output_data={"error": error_text, "status_code": response.status},
                            metadata=error_metadata,
                            start_time=start_time,
                            end_time=end_time
                        )

                        raise Exception(f"VLLM API error: {response.status} - {error_text}")

                    result = await response.json()
                    output_text = result["choices"][0]["message"]["content"]

                    # Extract metadata
                    metadata = {
                        "model": self.model,
                        "base_url": self.base_url,
                        "status_code": response.status,
                        "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result.get("usage", {}).get("total_tokens", 0),
                        "response_id": result.get("id", "unknown"),
                        "created": result.get("created", 0)
                    }

                    # Log the API call
                    log_llm_call(
                        provider_name=self.get_provider_name(),
                        call_type="chat_completion",
                        input_data=input_data,
                        output_data={"response": output_text},
                        metadata=metadata,
                        start_time=start_time,
                        end_time=end_time
                    )

                    return output_text

        except Exception as e:
            if 'end_time' not in locals():
                end_time = time.time()

            error_metadata = {
                "model": self.model,
                "base_url": self.base_url,
                "error": str(e),
                "error_type": type(e).__name__
            }

            # Log the failed API call
            log_llm_call(
                provider_name=self.get_provider_name(),
                call_type="chat_completion_error",
                input_data=input_data,
                output_data={"error": str(e)},
                metadata=error_metadata,
                start_time=start_time,
                end_time=end_time
            )

            system_logger.error(f"VLLM API error: {e}")
            raise

    async def _generate_response_with_tools_impl(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        start_time = time.time()

        # Check if this is a Gemma-like model that has strict chat template requirements
        is_gemma_like = self._is_gemma_like_model()

        if is_gemma_like:
            # Use simple chat completion for Gemma-like models
            return await self._generate_with_simple_chat(messages, tools, start_time, **kwargs)

        # Check if VLLM server supports OpenAI function calling
        supports_function_calling = await self._check_function_calling_support()

        system_logger.info(f"VLLM function calling support: {supports_function_calling}")

        if supports_function_calling:
            # Use OpenAI-compatible function calling - VLLM handles chat template automatically
            return await self._generate_with_openai_function_calling(messages, tools, start_time, **kwargs)
        else:
            # Fall back to enhanced system message - let VLLM apply its own chat template
            return await self._generate_with_enhanced_system_message(messages, tools, start_time, **kwargs)

    def _is_gemma_like_model(self) -> bool:
        """Check if the model is Gemma-like with strict chat template requirements"""
        model_lower = self.model.lower()
        gemma_keywords = ['gemma', 'phi', 'llama', 'mistral', 'codellama']
        return any(keyword in model_lower for keyword in gemma_keywords)

    def _get_max_tokens(self, default_max_tokens: int = 16384) -> int:
        """Get appropriate max_tokens based on model type"""
        # For Qwen2.5 models, use 2048 unless it's a 1M context model
        if 'qwen2.5' in self.model.lower():
            # Check if it's one of the 1M context models
            if 'Qwen/Qwen2.5-14B-Instruct-1M' in self.model or 'Qwen/Qwen2.5-7B-Instruct-1M' in self.model:
                return default_max_tokens  # Keep 16384 for 1M models
            else:
                return 2048  # Use 2048 for other Qwen2.5 models
        return default_max_tokens  # Use default for all other models

    def _extract_task_from_messages(self, messages: List[Dict[str, str]]) -> str:
        """Extract task information from messages with support for multiple formats"""
        original_task = ""

        # First try to find task in system messages - support multiple formats
        for msg in messages:
            if msg["role"] == "system":
                content = msg["content"]

                # Format 1: "YOUR TASK: ..."
                if "YOUR TASK:" in content:
                    task_start = content.find("YOUR TASK:") + 10
                    task_end = content.find("\n", task_start)
                    if task_end == -1:
                        task_end = len(content)
                    original_task = content[task_start:task_end].strip()
                    break

                # Format 2: "TASK: ..."
                elif "TASK:" in content:
                    task_start = content.find("TASK:") + 5
                    task_end = content.find("\n", task_start)
                    if task_end == -1:
                        task_end = len(content)
                    original_task = content[task_start:task_end].strip()
                    break

                # Format 3: "task: ..." (case insensitive)
                elif "task:" in content.lower():
                    task_start = content.lower().find("task:") + 5
                    task_end = content.find("\n", task_start)
                    if task_end == -1:
                        task_end = len(content)
                    original_task = content[task_start:task_end].strip()
                    break

        # If no task found in system messages, look for it in user messages
        if not original_task:
            for msg in messages:
                if msg["role"] == "user":
                    content = msg["content"]

                    # Look for task patterns in user messages
                    if "TASK:" in content:
                        task_start = content.find("TASK:") + 5
                        task_end = content.find("\n", task_start)
                        if task_end == -1:
                            task_end = len(content)
                        original_task = content[task_start:task_end].strip()
                        break

                    # Look for task-related keywords
                    elif any(keyword in content.lower() for keyword in ["analyze", "analysis", "patient", "database", "query", "search", "explore"]):
                        # Extract a meaningful task description from context
                        words = content.split()
                        if len(words) > 3:  # Only use messages with substantial content
                            # Take first few meaningful words as task
                            original_task = " ".join(words[:min(10, len(words))])
                            break

        return original_task

    async def _generate_with_simple_chat(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], start_time: float, **kwargs) -> Dict[str, Any]:
        """Generate response using simple chat completion for strict models like Gemma"""

        # Extract task information from system messages - support multiple formats
        original_task = self._extract_task_from_messages(messages)

        # Create a comprehensive system message that works well with Gemma
        system_content = "You are an autonomous data analysis agent that can use tools to help users.\n\n"

        if original_task:
            system_content += f"YOUR TASK: {original_task}\n\n"
            system_content += "INSTRUCTIONS:\n"
            system_content += "1. Start by understanding the task and planning your approach\n"
            system_content += "2. Use the available tools to gather information and perform analysis\n"
            system_content += "3. Make progress step by step, explaining what you're doing\n"
            system_content += "4. Only say 'FINISH' when you have completely accomplished the task\n\n"
        else:
            system_content += "INSTRUCTIONS:\n"
            system_content += "1. Listen carefully to the user's request\n"
            system_content += "2. Use available tools when needed to help the user\n"
            system_content += "3. Be proactive and helpful\n\n"

        if tools:
            system_content += "Available tools:\n"
            for tool in tools:
                system_content += f"- {tool['name']}: {tool.get('description', '')}\n"
            system_content += "\n"
            system_content += "TOOL USAGE FORMATS (choose one):\n"
            system_content += "1. ```tool_call\n{\"name\": \"tool_name\", \"arguments\": {\"param\": \"value\"}}\n```\n"
            system_content += "2. <tool_call>{\"name\": \"tool_name\", \"arguments\": {\"param\": \"value\"}}</tool_call>\n"
            system_content += "3. {\"name\": \"tool_name\", \"arguments\": {\"param\": \"value\"}}\n"
            system_content += "4. tool_name(param=value, param2=value2)\n\n"
            system_content += "CRITICAL: You MUST use tools when the user asks for information or analysis that requires them.\n"
            system_content += "Do NOT just say you're ready - ACTUALLY DO the work using the tools!\n"
            system_content += "If you have completed the task, include 'FINISH' in your response.\n"

        # Prepare messages with proper conversation history for Gemma
        # Gemma needs a simple alternating pattern: system -> user -> assistant -> user -> assistant...
        chat_messages = []
        chat_messages.append({"role": "system", "content": system_content})

        # Build conversation history maintaining proper alternation
        # Handle data_agent.py message format: system -> user -> agent -> environment -> user -> agent...
        conversation_pairs = []
        current_user_msg = None

        for msg in messages:
            if msg["role"] == "system":
                continue  # Skip original system messages
            elif msg["role"] == "user":
                if current_user_msg is not None:
                    # We have a user message waiting, this means we have a complete pair
                    conversation_pairs.append((current_user_msg, None))
                current_user_msg = msg["content"]
            elif msg["role"] == "assistant":
                if current_user_msg is not None:
                    # Complete the pair
                    conversation_pairs.append((current_user_msg, msg["content"]))
                    current_user_msg = None
                else:
                    # Assistant message without preceding user message, skip it
                    continue

        # Handle any remaining user message
        if current_user_msg is not None:
            conversation_pairs.append((current_user_msg, None))

        # Add conversation pairs to chat messages
        for user_msg, assistant_msg in conversation_pairs:
            chat_messages.append({"role": "user", "content": user_msg})
            if assistant_msg:
                chat_messages.append({"role": "assistant", "content": assistant_msg})

        # Ensure we end with a user message to prompt for response
        # Use a more specific prompt based on the task context
        if not chat_messages or chat_messages[-1]["role"] != "user":
            if original_task:
                chat_messages.append({"role": "user", "content": f"Please continue working on the task: {original_task}"})
            else:
                chat_messages.append({"role": "user", "content": "Please continue with the task."})

        payload = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": kwargs.get("max_tokens", self._get_max_tokens()),
            "temperature": kwargs.get("temperature", 0.0),
            "stream": False
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        input_data = {
            "url": self._chat_url,
            "payload": {k: v for k, v in payload.items() if k != "messages"},
            "messages": chat_messages,
            "method": "simple_chat_gemma",
            "headers": {k: v for k, v in headers.items() if k != "Authorization"}
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._chat_url,
                    json=payload,
                    headers=headers
                ) as response:
                    end_time = time.time()

                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"VLLM API error: {response.status} - {error_text}")

                    result_data = await response.json()

                    # Extract content and parse tool calls
                    message = result_data["choices"][0]["message"]
                    content = message.get("content", "") or ""

                    # Extract usage information first
                    usage_info = {
                        "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result_data.get("usage", {}).get("total_tokens", 0)
                    }

                    # Check for FINISH signal
                    if content and "FINISH" in content:
                        system_logger.info(f"FINISH signal detected in Gemma response: {content[:100]}...")
                        result = {
                            "content": content,
                            "tool_calls": [],
                            "finish": True,
                            "usage": usage_info
                        }
                    else:
                        # Parse tool calls from content
                        tool_calls = self._parse_generic_tool_calls(content)
                        result = {
                            "content": content,
                            "tool_calls": tool_calls,
                            "finish": False,
                            "usage": usage_info
                        }

                    # Extract metadata
                    metadata = {
                        "model": self.model,
                        "base_url": self.base_url,
                        "status_code": response.status,
                        "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result_data.get("usage", {}).get("total_tokens", 0),
                        "finish": result["finish"],
                        "response_id": result_data.get("id", "unknown"),
                        "created": result_data.get("created", 0),
                        "tool_calls_count": len(result["tool_calls"]),
                        "method": "simple_chat_gemma"
                    }

                    # Log the API call
                    log_llm_call(
                        provider_name=self.get_provider_name(),
                        call_type="chat_completion_with_tools_simple_chat_gemma",
                        input_data=input_data,
                        output_data=result,
                        metadata=metadata,
                        start_time=start_time,
                        end_time=end_time
                    )

                    return result

        except Exception as e:
            self._log_tool_call_error(input_data, str(e), start_time, time.time(), "simple_chat_gemma")
            raise

    async def _generate_with_openai_function_calling(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], start_time: float, **kwargs) -> Dict[str, Any]:
        """Generate response using OpenAI-compatible function calling"""
        # Convert tools to OpenAI format
        openai_tools = self.convert_mcp_tools_to_provider_format(tools)

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._get_max_tokens()),
            "temperature": kwargs.get("temperature", 0.0),
            "stream": False,
            "tools": openai_tools,
            "tool_choice": "auto"
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        input_data = {
            "url": self._chat_url,
            "payload": {k: v for k, v in payload.items() if k != "tools"},
            "messages": messages,  # Include messages for logging
            "tools": openai_tools,  # Include tools for logging
            "tools_count": len(openai_tools),
            "method": "openai_function_calling",
            "headers": {k: v for k, v in headers.items() if k != "Authorization"}
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._chat_url,
                    json=payload,
                    headers=headers
                ) as response:
                    end_time = time.time()

                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"VLLM API error: {response.status} - {error_text}")

                    result_data = await response.json()

                    # Extract message and tool calls
                    message = result_data["choices"][0]["message"]
                    content = message.get("content", "") or ""

                    # Extract usage information first
                    usage_info = {
                        "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result_data.get("usage", {}).get("total_tokens", 0)
                    }

                    # CRITICAL: Check for FINISH first, before any tool call processing
                    if content and "FINISH" in content:
                        system_logger.info(f"FINISH signal detected in VLLM OpenAI function calling response: {content[:100]}...")
                        # Return immediately with FINISH content, no tool call processing needed
                        result = {
                            "content": content,
                            "tool_calls": [],  # No tool calls when finishing
                            "finish": True,  # Simple boolean flag
                            "usage": usage_info
                        }

                        # Extract metadata with token info
                        metadata = {
                            "model": self.model,
                            "base_url": self.base_url,
                            "status_code": response.status,
                            "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                            "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                            "total_tokens": result_data.get("usage", {}).get("total_tokens", 0),
                            "finish": True,  # Use the finish field instead of finish_reason
                            "response_id": result_data.get("id", "unknown"),
                            "created": result_data.get("created", 0),
                            "tool_calls_count": 0,
                            "method": "openai_function_calling_finish"
                        }

                        # Log the API call
                        log_llm_call(
                            provider_name=self.get_provider_name(),
                            call_type="chat_completion_with_tools_openai_function_calling_finish",
                            input_data=input_data,
                            output_data=result,
                            metadata=metadata,
                            start_time=start_time,
                            end_time=end_time
                        )

                        return result

                    tool_calls = []

                    if message.get("tool_calls"):
                        for tool_call in message["tool_calls"]:
                            tool_calls.append({
                                "id": tool_call.get("id", f"call_{len(tool_calls)}"),
                                "type": tool_call.get("type", "function"),
                                "function": {
                                    "name": tool_call["function"]["name"],
                                    "arguments": tool_call["function"]["arguments"]
                                }
                            })

                    result = {
                        "content": content,
                        "tool_calls": tool_calls,
                        "finish": False,  # Simple boolean flag - not finishing
                        "usage": usage_info
                    }

                    # Extract metadata with token info
                    metadata = {
                        "model": self.model,
                        "base_url": self.base_url,
                        "status_code": response.status,
                        "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result_data.get("usage", {}).get("total_tokens", 0),
                        "finish": result["finish"],  # Use the finish field instead of finish_reason
                        "response_id": result_data.get("id", "unknown"),
                        "created": result_data.get("created", 0),
                        "tool_calls_count": len(tool_calls),
                        "method": "openai_function_calling"
                    }

                    # Log the API call
                    log_llm_call(
                        provider_name=self.get_provider_name(),
                        call_type="chat_completion_with_tools_openai_function_calling",
                        input_data=input_data,
                        output_data=result,
                        metadata=metadata,
                        start_time=start_time,
                        end_time=end_time
                    )

                    return result

        except Exception as e:
            self._log_tool_call_error(input_data, str(e), start_time, time.time(), "openai_function_calling")
            raise

    async def _generate_with_enhanced_system_message(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], start_time: float, **kwargs) -> Dict[str, Any]:
        """Generate response using enhanced system message - let VLLM handle chat template automatically"""

        # Prepare tool information for system message
        tool_descriptions = []
        for tool in tools:
            tool_desc = f"- {tool['name']}: {tool.get('description', '')}"
            if 'inputSchema' in tool and tool['inputSchema'].get('properties'):
                params = list(tool['inputSchema']['properties'].keys())[:3]
                param_str = ', '.join(params)
                if len(tool['inputSchema']['properties']) > 3:
                    param_str += '...'
                tool_desc += f" (params: {param_str})"
            tool_descriptions.append(tool_desc)

        # Enhanced system message with Qwen2.5-style tool format
        enhanced_messages = []
        system_added = False

        # Prepare tools in Qwen2.5 format
        tools_json_list = []
        for tool in tools:
            tool_json = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {
                    "type": "object",
                    "properties": {},
                    "required": []
                })
            }
            tools_json_list.append(json.dumps(tool_json, ensure_ascii=False))

        for msg in messages:
            if msg["role"] == "system" and not system_added:
                enhanced_content = msg["content"]
                enhanced_content += "\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
                enhanced_content += "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n"
                enhanced_content += "\n".join(tools_json_list)
                enhanced_content += "\n</tools>\n\n"
                enhanced_content += "For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
                enhanced_content += "<tool_call>\n{\"name\": <function-name>, \"arguments\": <args-json-object>}\n</tool_call>"

                enhanced_messages.append({"role": "system", "content": enhanced_content})
                system_added = True
            else:
                enhanced_messages.append(msg)

        # Add system message if none existed
        if not system_added:
            system_content = "You are a helpful assistant that can use tools to assist with user queries.\n\n"
            system_content += "# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
            system_content += "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n"
            system_content += "\n".join(tools_json_list)
            system_content += "\n</tools>\n\n"
            system_content += "For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
            system_content += "<tool_call>\n{\"name\": <function-name>, \"arguments\": <args-json-object>}\n</tool_call>"
            enhanced_messages.insert(0, {"role": "system", "content": system_content})

        payload = {
            "model": self.model,
            "messages": enhanced_messages,
            "max_tokens": kwargs.get("max_tokens", self._get_max_tokens()),
            "temperature": kwargs.get("temperature", 0.0),
            "stream": False
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        input_data = {
            "url": self._chat_url,
            "payload": {k: v for k, v in payload.items() if k != "messages"},
            "messages": enhanced_messages,  # Include messages for logging
            "message_count": len(enhanced_messages),
            "tools_count": len(tools),
            "method": "enhanced_system_message",
            "headers": {k: v for k, v in headers.items() if k != "Authorization"}
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._chat_url,
                    json=payload,
                    headers=headers
                ) as response:
                    end_time = time.time()

                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"VLLM API error: {response.status} - {error_text}")

                    result_data = await response.json()

                    # Extract content and parse tool calls using generic patterns
                    message = result_data["choices"][0]["message"]
                    content = message.get("content", "") or ""

                    # Extract usage information first
                    usage_info = {
                        "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result_data.get("usage", {}).get("total_tokens", 0)
                    }

                    # CRITICAL: Check for FINISH first, before any tool parsing
                    if content and "FINISH" in content:
                        system_logger.info(f"FINISH signal detected in VLLM response: {content[:100]}...")
                        # Return immediately with FINISH content, no tool parsing needed
                        result = {
                            "content": content,
                            "tool_calls": [],  # No tool calls when finishing
                            "finish": True,  # Simple boolean flag
                            "usage": usage_info
                        }

                        # Extract metadata
                        metadata = {
                            "model": self.model,
                            "base_url": self.base_url,
                            "status_code": response.status,
                            "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                            "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                            "total_tokens": result_data.get("usage", {}).get("total_tokens", 0),
                            "finish": True,  # Use the finish field instead of finish_reason
                            "response_id": result_data.get("id", "unknown"),
                            "created": result_data.get("created", 0),
                            "tool_calls_count": 0,
                            "method": "enhanced_system_message_finish"
                        }

                        # Log the API call
                        log_llm_call(
                            provider_name=self.get_provider_name(),
                            call_type="chat_completion_with_tools_enhanced_system_message_finish",
                            input_data=input_data,
                            output_data=result,
                            metadata=metadata,
                            start_time=start_time,
                            end_time=end_time
                        )

                        return result

                    # Parse tool calls using generic pattern matching
                    tool_calls = self._parse_generic_tool_calls(content)
                    if tool_calls:
                        system_logger.info(f"Parsed {len(tool_calls)} tool calls")

                    # Keep original content as-is, don't remove tool call markup
                    # This preserves the agent's original output format

                    result = {
                        "content": content,
                        "tool_calls": tool_calls,
                        "finish": False,  # Simple boolean flag - not finishing
                        "usage": usage_info
                    }

                    # Extract metadata with token info
                    metadata = {
                        "model": self.model,
                        "base_url": self.base_url,
                        "status_code": response.status,
                        "prompt_tokens": result_data.get("usage", {}).get("prompt_tokens", 0),
                        "completion_tokens": result_data.get("usage", {}).get("completion_tokens", 0),
                        "total_tokens": result_data.get("usage", {}).get("total_tokens", 0),
                        "finish": result["finish"],  # Use the finish field instead of finish_reason
                        "response_id": result_data.get("id", "unknown"),
                        "created": result_data.get("created", 0),
                        "tool_calls_count": len(tool_calls),
                        "method": "enhanced_system_message"
                    }

                    # Log the API call
                    log_llm_call(
                        provider_name=self.get_provider_name(),
                        call_type="chat_completion_with_tools_enhanced_system_message",
                        input_data=input_data,
                        output_data=result,
                        metadata=metadata,
                        start_time=start_time,
                        end_time=end_time
                    )

                    return result

        except Exception as e:
            self._log_tool_call_error(input_data, str(e), start_time, time.time(), "enhanced_system_message")
            raise

    def _log_tool_call_error(self, input_data: Dict, error: str, start_time: float, end_time: float, method: str):
        """Log failed tool call"""
        error_metadata = {
            "model": self.model,
            "base_url": self.base_url,
            "method": method,
            "error": error,
            "error_type": "VLLM_API_ERROR"
        }

        log_llm_call(
            provider_name=self.get_provider_name(),
            call_type=f"chat_completion_with_tools_{method}_error",
            input_data=input_data,
            output_data={"error": error},
            metadata=error_metadata,
            start_time=start_time,
            end_time=end_time
        )

        system_logger.error(f"VLLM API error: {error}")

    def convert_mcp_tools_to_provider_format(self, mcp_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert MCP tools to OpenAI function calling format (VLLM follows OpenAI API)"""
        openai_tools = []

        for tool in mcp_tools:
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                }
            }

            # Add parameters if available
            if "inputSchema" in tool and tool["inputSchema"]:
                openai_tool["function"]["parameters"] = tool["inputSchema"]
            else:
                # Default empty parameters
                openai_tool["function"]["parameters"] = {
                    "type": "object",
                    "properties": {},
                    "required": []
                }

            openai_tools.append(openai_tool)

        return openai_tools

    def get_provider_name(self) -> str:
        return f"VLLM-{self.model}"
