"""Compliance framing for everything the LLM agents produce.

This system is not a consumer advice product: the agents exist to run one
operator's own paper account, so muzzling them out of directional calls would
remove the product. What the guardrail does instead is constrain HOW they
reach a call — no invented figures, no certainty about the future, no
guaranteed-return language — and label the output so that nothing downstream
can present it as personalized advice to a third party.

`AGENT_INSTRUCTION` is appended to every agent's system prompt. `DISCLAIMER`
and `ADVICE_STATUS` ride on every decision the API serves, so a client cannot
render a rating without also having the caveat in hand.
"""

from __future__ import annotations

# Length target: >=760 chars. Anthropic caches only prefixes >=1024 tokens; at
# ~966 tokens (tools + system), we need ~60 more tokens (~240 chars) to cross
# the threshold and enable cache writes. Below that, every call pays full price.
AGENT_INSTRUCTION = (
    " Ground every figure you cite in the data provided to you; if a number is "
    "not in your inputs, say it is unavailable rather than estimating one. "
    "Express forecasts as uncertain — never state or imply that a price target, "
    "return, or outcome is guaranteed, riskless, or assured. Where your case "
    "depends on an assumption, name the assumption. Your output is a mechanical "
    "signal for one operator's own account, not personalized advice for anyone "
    "else, so do not address a reader's personal circumstances, tax position, or "
    "suitability."
    " Consider market regime and cycle phase when weighing risk factors: "
    "expansion vs. contraction, rising vs. falling volatility, trending vs. "
    "range-bound price action, and sector rotation patterns all shape what "
    "signals carry conviction and what risks dominate. Distinguish catalysts by "
    "time horizon — a quarterly earnings beat is not the same as a multi-year "
    "secular tailwind, and your analysis must name which horizon each factor "
    "addresses. Treat liquidity and market structure as risk dimensions: thinly "
    "traded names, wide spreads, and concentration in passive flows all constrain "
    "execution and amplify drawdowns in ways fundamentals alone do not capture."
)

ADVICE_STATUS = "not_personalized_advice"

DISCLAIMER = (
    "Automated model output for the operator's own account. Not investment "
    "advice, not a solicitation, and not personalized to anyone's circumstances. "
    "Past performance does not guarantee future results."
)

DISCLAIMER_TR = (
    "Operatörün kendi hesabı için üretilmiş otomatik model çıktısıdır. Yatırım "
    "tavsiyesi değildir, kişiye özel değildir. Geçmiş performans gelecekteki "
    "getirileri garanti etmez."
)
