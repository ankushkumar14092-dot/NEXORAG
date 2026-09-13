"""Language-aware helpers — English + Hinglish first-class support."""

from __future__ import annotations

import re

from app.llm import generate_answer

_NON_LATIN = re.compile(
    r"[\u0900-\u097F\u0600-\u06FF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF"
    r"\u0400-\u04FF\u0E00-\u0E7F\u0980-\u09FF\u0A80-\u0AFF\u0B80-\u0BFF]"
)

# Common Hinglish (Roman Hindi) cue words
_HINGLISH_CUES = {
    "kya",
    "kyun",
    "kyu",
    "kaise",
    "kaisa",
    "kaisi",
    "hai",
    "hain",
    "tha",
    "thi",
    "the",
    "mein",
    "me",
    "mai",
    "main",
    "hum",
    "tum",
    "aap",
    "ye",
    "yeh",
    "woh",
    "wo",
    "ka",
    "ki",
    "ke",
    "se",
    "ko",
    "par",
    "pe",
    "aur",
    "ya",
    "nahi",
    "nahin",
    "mat",
    "bhi",
    "toh",
    "to",
    "bas",
    "abhi",
    "phir",
    "matlab",
    "samjhao",
    "samjha",
    "batao",
    "bataya",
    "bata",
    "dekho",
    "dekh",
    "suna",
    "karo",
    "karna",
    "hona",
    "wala",
    "wali",
    "wale",
    "acha",
    "accha",
    "theek",
    "thik",
    "bhai",
    "yaar",
    "please",
    "plz",
    "video",
    "document",
    "pdf",
    "lecture",
    "session",
    "topic",
}

# Lightweight roman → Devanagari for retrieval over Hindi transcripts
_ROMAN_TO_HI = {
    "kya": "क्या",
    "kyun": "क्यों",
    "kyu": "क्यों",
    "kaise": "कैसे",
    "hai": "है",
    "hain": "हैं",
    "mein": "में",
    "me": "में",
    "mai": "मैं",
    "main": "मैं",
    "ye": "ये",
    "yeh": "यह",
    "woh": "वह",
    "wo": "वो",
    "ka": "का",
    "ki": "की",
    "ke": "के",
    "se": "से",
    "ko": "को",
    "aur": "और",
    "nahi": "नहीं",
    "nahin": "नहीं",
    "bhi": "भी",
    "toh": "तो",
    "to": "तो",
    "matlab": "मतलब",
    "batao": "बताओ",
    "bataya": "बताया",
    "bata": "बता",
    "dekho": "देखो",
    "dekh": "देख",
    "samjhao": "समझाओ",
    "video": "वीडियो",
    "session": "सेशन",
    "lecture": "लेक्चर",
    "topic": "टॉपिक",
    "langgraph": "लंगग्राफ",
    "graph": "ग्राफ",
    "intro": "इंट्रो",
    "introduction": "इंट्रोडक्शन",
    "gaya": "गया",
    "gayi": "गई",
    "tha": "था",
    "thi": "थी",
    "is": "इस",
    "us": "उस",
    "kya": "क्या",
}


def detect_locale(text: str) -> str:
    """
    Return: english | hinglish | hindi | other
    """
    t = (text or "").strip()
    if not t:
        return "english"
    if _NON_LATIN.search(t):
        # Devanagari-heavy → hindi
        if re.search(r"[\u0900-\u097F]", t):
            return "hindi"
        return "other"
    tokens = re.findall(r"[A-Za-z0-9']+", t.lower())
    if not tokens:
        return "english"
    cues = sum(1 for w in tokens if w in _HINGLISH_CUES)
    # Hinglish if enough Roman-Hindi cues (or cue + English mix)
    if cues >= 2 or (cues >= 1 and any(w in {"video", "pdf", "document", "code"} for w in tokens)):
        # Pure English questions can contain "to"/"the" — require stronger signal
        strong = {
            "kya",
            "kaise",
            "kyun",
            "kyu",
            "mein",
            "hai",
            "batao",
            "bataya",
            "samjhao",
            "matlab",
            "nahi",
            "bhai",
            "yaar",
            "dekho",
        }
        if any(w in strong for w in tokens):
            return "hinglish"
    return "english"


def looks_non_english(text: str) -> bool:
    return detect_locale(text) != "english"


def _hinglish_to_devanagari_query(question: str) -> str | None:
    words = re.findall(r"[A-Za-z0-9']+", (question or "").lower())
    mapped = [_ROMAN_TO_HI.get(w, w) for w in words]
    # Only useful if at least one roman word mapped to Devanagari
    if not any(w in _ROMAN_TO_HI for w in words):
        return None
    out = " ".join(mapped)
    return out if out.strip() else None


def _english_keywords_from_llm(question: str) -> str | None:
    try:
        expanded = generate_answer(
            (
                "Convert the user question into English BM25 keywords.\n"
                "Rules: 3-12 ASCII words only, spaces only, no quotes, no commentary.\n"
                "If the question is Hinglish, translate meaning to English keywords.\n"
                "Example:\n"
                "Question: is video mein kya bataya gaya hai?\n"
                "Keywords: video explained topic discussion content\n"
            ),
            f"Question: {question}\nKeywords:",
            max_tokens=40,
        ).strip()
        if "keywords:" in expanded.lower():
            expanded = expanded.split(":")[-1].strip()
        expanded = expanded.splitlines()[0].strip().strip('"').strip("'")
        if expanded.lower().startswith("the user"):
            return None
        cleaned = re.sub(r"[^A-Za-z0-9\s]+", " ", expanded)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        words = [w for w in cleaned.split() if len(w) > 1][:12]
        if len(words) >= 2:
            return " ".join(words)
    except Exception:
        return None
    return None


def search_query_variants(question: str, *, allow_llm: bool = True) -> list[str]:
    """
    Retrieval variants for English / Hinglish / Hindi.
    Always includes the original question first.
    """
    q = (question or "").strip()
    if not q:
        return []
    variants: list[str] = [q]
    locale = detect_locale(q)

    if locale == "english":
        return variants

    if locale == "hinglish":
        hi = _hinglish_to_devanagari_query(q)
        if hi and hi not in variants:
            variants.append(hi)
        # Dictionary path is enough for speed; LLM expansion optional
        if allow_llm:
            en = _english_keywords_from_llm(q)
            if en and en.lower() not in {v.lower() for v in variants}:
                variants.append(en)
        return variants

    # Hindi / other — English keywords only when allow_llm (lazy callers)
    if allow_llm:
        en = _english_keywords_from_llm(q)
        if en and en.lower() not in {v.lower() for v in variants}:
            variants.append(en)
    return variants


def answer_language_rules(question: str) -> str:
    locale = detect_locale(question)
    if locale == "hinglish":
        style = (
            "- The question is HINGLISH (Hindi in Roman letters, often mixed with English).\n"
            "- Write the ENTIRE briefing in Hinglish: Roman Hindi + English words as natural.\n"
            "- Do NOT switch to pure Devanagari Hindi unless the user used Devanagari.\n"
            "- Do NOT rewrite everything into formal British/American English only.\n"
            "- Example tone: 'Is video mein LangGraph ka intro aur implementation cover hua hai [1].'\n"
        )
    elif locale == "hindi":
        style = (
            "- The question is in Devanagari Hindi.\n"
            "- Write the ENTIRE briefing in Devanagari Hindi.\n"
            "- Do not answer in English or Hinglish.\n"
        )
    elif locale == "english":
        style = (
            "- The question is in English.\n"
            "- Write the ENTIRE briefing in clear English.\n"
            "- Do not answer in Hindi/Hinglish.\n"
        )
    else:
        style = (
            "- Match the user's language exactly for the whole briefing.\n"
        )

    return (
        "LANGUAGE RULE (mandatory):\n"
        f"- Detected locale: {locale}\n"
        f"- User question: «{question[:200]}»\n"
        f"{style}"
        "- Keep evidence citations as [1], [2] unchanged.\n"
        "- Output ONLY the final markdown briefing. No English planning, "
        "no chain-of-thought, no preamble like 'The user question is...'.\n"
        "- First line must be the Finding heading (translated to match locale if needed).\n"
    )


def no_evidence_reply(question: str) -> str:
    locale = detect_locale(question)
    fallback = {
        "hinglish": (
            "Relevant evidence nahi mila. Pehle koi document/video upload karo, "
            "ya question thoda clear karke try karo."
        ),
        "hindi": (
            "कोई प्रासंगिक प्रमाण नहीं मिला। पहले कोई दस्तावेज़/वीडियो अपलोड करें, "
            "या प्रश्न दोबारा पूछें।"
        ),
        "english": (
            "No relevant evidence found. Upload a document or video first, "
            "or try another question."
        ),
    }.get(locale, "No relevant evidence found.")
    # Fast path — avoid an extra LLM round-trip on empty retrieval
    return fallback
