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

# Kept deliberately short: it is prepended to a dozen prompts on every run, and
# a wall of policy text crowds out the actual analysis instructions.
AGENT_INSTRUCTION = (
    " Ground every figure you cite in the data provided to you; if a number is "
    "not in your inputs, say it is unavailable rather than estimating one. "
    "Express forecasts as uncertain — never state or imply that a price target, "
    "return, or outcome is guaranteed, riskless, or assured. Where your case "
    "depends on an assumption, name the assumption. Your output is a mechanical "
    "signal for one operator's own account, not personalized advice for anyone "
    "else, so do not address a reader's personal circumstances, tax position, or "
    "suitability."
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
