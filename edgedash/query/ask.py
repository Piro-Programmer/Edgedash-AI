"""
Query router for EdgeDash.

Provides a natural language interface over the deterministic query tools.
Uses a two-call LLM pipeline (Route -> Execute -> Phrase) per rules 42-45.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from edgedash import storage
from edgedash.config import load_config
from edgedash.llm import complete_json
from edgedash.query.tools import TOOLS


@dataclass
class Answer:
    text: str
    rows: list[dict]
    tool_used: str | None
    params: dict[str, Any]


ROUTE_PROMPT = """
You are a deterministic query router. The user will ask a question.
Your job is to select the exact tool that can answer the question from the provided list.

AVAILABLE TOOLS:
{tools_json}

RULES:
1. You may ONLY select a tool from the provided list.
2. If none of the tools exactly match the user's intent, you MUST set "tool" to null.
3. Do NOT attempt to pick the "closest" tool if it does not answer the question directly. Return null instead.
4. Extract any required or optional parameters mentioned in the user's question into the "params" dictionary. If a parameter is not mentioned, omit it to use the tool's default.

QUESTION:
{question}
"""

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {
            "type": ["string", "null"],
            "description": "The name of the tool to use, or null if no tool fits."
        },
        "params": {
            "type": "object",
            "description": "Extracted parameters for the tool."
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "low"]
        }
    },
    "required": ["tool", "params", "confidence"]
}

PHRASE_PROMPT = """
You are a data assistant summarising the results of a database query for the user.

USER'S QUESTION:
{question}

QUERY CONTEXT:
{summary}

ROWS RETURNED:
{rows_json}

RULES (CRITICAL):
1. Write 2-3 sentences max.
2. Use ONLY numbers and facts present in the rows above. Do NOT estimate, extrapolate, or add outside context.
3. If the rows are empty, state clearly that the data does not contain an answer to the question.
"""

PHRASE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "The 2-3 sentence answer to the user's question."
        }
    },
    "required": ["answer"]
}


def _build_route_prompt(question: str) -> str:
    tool_specs = []
    for name, meta in TOOLS.items():
        tool_specs.append({
            "name": name,
            "description": meta["description"],
            "parameters": meta["parameters"]
        })
        
    tools_json = json.dumps(tool_specs, indent=2)
    return ROUTE_PROMPT.format(tools_json=tools_json, question=question)


def ask(question: str) -> Answer:
    """Run the 2-call Route -> Execute -> Phrase pipeline to answer a question."""
    start_time = time.monotonic()
    config = load_config()
    db_path = config.db_path
    
    # --- 1. ROUTE ---
    route_prompt = _build_route_prompt(question)
    route_res = complete_json(route_prompt, ROUTE_SCHEMA, max_retries=1)
    
    tool_name = route_res.get("tool")
    params = route_res.get("params", {})
    
    if not tool_name:
        # If tool is null, return fixed message listing available tool descriptions. No phrasing call.
        lines = ["I cannot answer that question. Here is what I can do:\n"]
        for name, meta in TOOLS.items():
            lines.append(f"- {meta['description']}")
            
        answer_text = "\n".join(lines)
        return Answer(text=answer_text, rows=[], tool_used=None, params={})
        
    if tool_name not in TOOLS:
        # Validate tool is in TOOLS. Hard error if not.
        raise ValueError(f"Router returned unknown tool: {tool_name}")
        
    # --- 2. EXECUTE ---
    # Call the tool with validated, clamped params. Never eval/getattr outside registry.
    tool_meta = TOOLS[tool_name]
    tool_func = tool_meta["func"]
    
    # Execute the deterministic tool
    rows, summary = tool_func(db_path, **params)
    
    # --- 3. PHRASE ---
    phrase_prompt = PHRASE_PROMPT.format(
        question=question,
        summary=summary,
        rows_json=json.dumps(rows[:10], indent=2) # Only send top 10 rows to avoid context limits
    )
    
    phrase_res = complete_json(phrase_prompt, PHRASE_SCHEMA, max_retries=1)
    answer_text = phrase_res["answer"]
    
    # --- 4. RETURN ---
    return Answer(
        text=answer_text,
        rows=rows,
        tool_used=tool_name,
        params=params
    )
