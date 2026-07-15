# app/prompt_engine.py

import random

from nanogpt_service import NanoGptService

COMEDIC_KEY = "comedic"
FLIRTY_KEY = "flirty"
STRAIGHTFORWARD_KEY = "straightforward"

COMEDIC_TEMPLATE = (
    "The profile mentions '{keyword}'. That's hilarious! "
    "Please create a short, witty comment referencing that."
)
FLIRTY_TEMPLATE = (
    "This person loves '{keyword}'. Write a playful invitation "
    "asking them about it in a flirty, friendly way."
)
STRAIGHTFORWARD_TEMPLATE = (
    "They mentioned '{keyword}'. Generate a direct, polite invitation "
    "to discuss that topic over coffee."
)

# Global weights for each style
TEMPLATE_WEIGHTS = {COMEDIC_KEY: 1.0, FLIRTY_KEY: 1.0, STRAIGHTFORWARD_KEY: 1.0}


def update_template_weights(success_rates: dict):
    """
    If comedic style yields a higher success rate, automatically adjust
    to favor comedic, etc.
    """
    if not success_rates:
        return

    best_template = max(success_rates, key=success_rates.get)
    # Reset all weights to a baseline
    baseline = 1.0
    for key in TEMPLATE_WEIGHTS:
        TEMPLATE_WEIGHTS[key] = baseline

    # Increase the weight of whichever template is best
    # (for example, we identify comedic by checking if "hilarious" is in the template string)
    if "hilarious" in best_template:
        TEMPLATE_WEIGHTS[COMEDIC_KEY] = baseline + 0.5
    elif "flirty" in best_template:
        TEMPLATE_WEIGHTS[FLIRTY_KEY] = baseline + 0.5
    elif "coffee" in best_template:
        TEMPLATE_WEIGHTS[STRAIGHTFORWARD_KEY] = baseline + 0.5


def weighted_choice(templates_with_weights):
    total = sum(templates_with_weights.values())
    r = random.uniform(0, total)
    cum = 0.0
    for tmpl_key, wt in templates_with_weights.items():
        cum += wt
        if r < cum:
            return tmpl_key
    # fallback
    return random.choice(list(templates_with_weights.keys()))


def choose_template(sentiment: str, keywords: list) -> str:
    # If no keywords, fallback
    if not keywords:
        return "Write a short, friendly greeting without referencing specific keywords."

    style = weighted_choice(TEMPLATE_WEIGHTS)
    if style == COMEDIC_KEY:
        return COMEDIC_TEMPLATE
    elif style == FLIRTY_KEY:
        return FLIRTY_TEMPLATE
    else:
        return STRAIGHTFORWARD_TEMPLATE


def generate_prompt(style_template: str, keywords: list, sentiment: str) -> str:
    chosen_keyword = random.choice(keywords) if keywords else "something interesting"
    base_text = style_template.format(keyword=chosen_keyword)
    system_prompt = f"""
    You are a friendly and likable person who is witty and humorous.
    The user's sentiment is: {sentiment}.
    {base_text}
    """
    return system_prompt


def call_nanogpt(
    prompt: str, temperature: float = 0.7, max_tokens: int = 150
) -> str:
    return NanoGptService().chat(
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )


def generate_comment(profile_text: str) -> str:
    """
    1. Clean & analyze text
    2. Choose a template
    3. Call NanoGPT
    Return the final comment string.
    """
    from text_analyzer import clean_text, extract_keywords, analyze_sentiment

    cleaned = clean_text(profile_text)
    keywords = extract_keywords(cleaned)
    sentiment = analyze_sentiment(cleaned)

    style_template = choose_template(sentiment, keywords)
    final_prompt = generate_prompt(style_template, keywords, sentiment)
    generated_text = call_nanogpt(final_prompt)
    return generated_text
