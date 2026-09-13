from __future__ import annotations

from openai import OpenAI

from app.config import settings

def llm_status() -> dict:
    primary = settings.fireworks_fast_model if settings.prefer_fast_model else settings.fireworks_model
    return {
        "provider": "fireworks" if settings.fireworks_api_key else (
            "anthropic" if settings.anthropic_api_key else "none"
        ),
        "fireworks_configured": bool(settings.fireworks_api_key),
        "anthropic_configured": bool(settings.anthropic_api_key),
        "model": primary if settings.fireworks_api_key else (
            settings.anthropic_model if settings.anthropic_api_key else None
        ),
        "prefer_fast_model": settings.prefer_fast_model,
    }


def generate_answer(system: str, user: str, max_tokens: int | None = None) -> str:
    """Generate grounded answer. Prefers Fireworks, falls back to Anthropic."""
    tokens = settings.answer_max_tokens if max_tokens is None else max_tokens
    if settings.fireworks_api_key:
        return _fireworks(system, user, tokens)
    if settings.anthropic_api_key:
        return _anthropic(system, user, tokens)
    raise RuntimeError("No LLM API key configured (set FIREWORKS_API_KEY)")


def _fireworks(system: str, user: str, max_tokens: int) -> str:
    client = OpenAI(
        base_url=settings.fireworks_base_url,
        api_key=settings.fireworks_api_key,
    )
    # Prefer fast model when configured for low latency.
    models = []
    if settings.prefer_fast_model and settings.fireworks_fast_model:
        models.append(settings.fireworks_fast_model)
    models.extend(
        [
            settings.fireworks_model,
            settings.fireworks_fast_model,
            "accounts/fireworks/models/deepseek-v4-pro-0813",
            "accounts/fireworks/models/deepseek-v4p1-flash",
            "accounts/fireworks/models/deepseek-v4-pro",
            "accounts/fireworks/models/glm-5p3",
            "accounts/fireworks/models/kimi-k3",
            "accounts/fireworks/models/qwen3p8-max",
            "accounts/fireworks/models/gpt-oss-120b",
        ]
    )
    # de-dupe preserving order
    seen = set()
    ordered = []
    for m in models:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)

    last_err: Exception | None = None
    for model in ordered:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                temperature=0.2,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            # Try next model on not-found / inaccessible
            if "404" in msg or "not found" in msg or "not deployed" in msg:
                continue
            # Auth / balance errors should stop immediately
            if "401" in msg or "unauthorized" in msg or "invalid" in msg and "api key" in msg:
                raise
            continue
    raise RuntimeError(f"Fireworks LLM failed: {last_err}")

def _anthropic(system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()
