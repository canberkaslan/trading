from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)


def get_language_instruction() -> str:
    """Return the per-agent trailer: compliance framing, then output language.

    Applied to every agent whose output reaches the saved report — analysts,
    researchers, debaters, research manager, trader, and portfolio manager.
    The compliance clause rides here because this is the one string all twelve
    prompt sites already append, so a single edit reaches every agent; the
    text itself lives in `tradingagents_us.compliance` (fork delta kept to one
    import, so a vendor resync has one line to reapply).
    """
    from tradingagents.dataflows.config import get_config

    try:
        from tradingagents_us.compliance import AGENT_INSTRUCTION
    except ImportError:  # vendored tree used standalone — degrade, never break
        AGENT_INSTRUCTION = ""

    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return AGENT_INSTRUCTION
    return f"{AGENT_INSTRUCTION} Write your entire response in {lang}."


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
