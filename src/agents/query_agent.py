"""Query Agent: LangGraph ReAct agent over the procurement comparison DB.

Uses the 6 query tools to answer natural-language procurement questions
such as price comparisons, vendor rankings, and flag summaries.
"""

import os

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from src.agents.tools import ALL_TOOLS

_SYSTEM_PROMPT = """You are a procurement analysis assistant for an RFx (Request for Quotation) process.

You have access to tools that query a vendor comparison database containing:
- Normalized INR prices per line item for each vendor
- Freight and discount terms per vendor
- Qualification questionnaire answers (ISO cert, lead time, rejection rate)
- Data quality flags (confidence scores, extraction issues)

Always base your answers on database results from the tools. When comparing prices,
prefer vendors with higher confidence scores (≥ 0.7) unless the user explicitly
asks for all data. State confidence caveats when relevant.

CRITICAL — formatting rules you must follow exactly:
- ALWAYS use the exact vendor IDs as stored: vendor_a, vendor_b, vendor_c, vendor_d, vendor_e (lowercase, underscore). Never write "Vendor A", "Vendor B" or any other form.
- Express prices in ₹ (INR)
- Round prices to 2 decimal places
- Flag data quality issues when confidence < 0.5
- Indian number formatting: ₹2,00,000 = 200,000 rupees (two lakhs). ₹1,00,000 = 100,000 rupees. The comma placement follows the Indian system (after first 3 digits, then every 2 digits from the right). Always parse these correctly before comparing.
- When a discount has a threshold condition (e.g. "orders above ₹2,00,000" = orders above 200,000 rupees), ALWAYS compare the actual numeric order total against the numeric threshold. If a vendor's total is ₹1,500 or ₹2,000 or ₹3,000, that is far below ₹2,00,000 (200,000). Only apply the discount if the total genuinely exceeds the threshold. If not met, state "discount does not apply" and use the original price.
"""


def build_query_agent():
    """Return a compiled LangGraph ReAct agent with the 6 query tools."""
    workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
    model_kwargs: dict = {}
    if workspace_id:
        model_kwargs["default_headers"] = {"anthropic-workspace-id": workspace_id}

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        **model_kwargs,
    )

    return create_react_agent(
        llm,
        tools=ALL_TOOLS,
        prompt=_SYSTEM_PROMPT,
    )


async def run_query(question: str, rfx_id: str = "RFX-001") -> str:
    """Run a natural-language procurement question through the Query Agent.

    Args:
        question: Free-text procurement question.
        rfx_id: RFx identifier to scope queries (default "RFX-001").

    Returns:
        Agent's final answer as a string.
    """
    agent = build_query_agent()
    full_question = f"[RFx: {rfx_id}] {question}"
    result = await agent.ainvoke({"messages": [("user", full_question)]})
    messages = result.get("messages", [])
    if messages:
        last = messages[-1]
        return last.content if hasattr(last, "content") else str(last)
    return "No response."
