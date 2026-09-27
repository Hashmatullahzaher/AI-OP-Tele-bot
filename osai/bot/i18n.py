"""Language detection and answer rendering for Dari, Pashto and English.

Answers are rendered by the server from authoritative data, never written by
the model, so numbers cannot be hallucinated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Letters used in Pashto but not in Dari/Persian.
_PASHTO_ONLY = set("ټډړږښګڼېۍځڅ")
_ARABIC_BLOCK = range(0x0600, 0x0700)

LANGS = ("fa", "ps", "en")

MESSAGES: Mapping[str, Mapping[str, str]] = {
    "en": {
        "welcome": 'Hello! Ask me about the company\'s data, for example: "What were total expenses this month?"',
        "unauthorized": "You are not allowed to use this bot. Ask the administrator to add your Telegram ID: {user_id}",
        "busy": "The AI service is busy right now. Please try again in a moment.",
        "not_understood": "Sorry, I could not answer that from the available data. Please ask more specifically.",
        "denied": "You do not have access to that data.",
        "unavailable": "The data source is not available right now. Please try again later.",
        "ambiguous": "The data in that sheet is not in a shape I can calculate safely.",
        "voice_unsupported": "Voice messages are not enabled yet. Please type your question.",
        "text_only": "Please send your question as text or a voice note.",
        "error": "Something went wrong. Please try again.",
        "sum": "Total {column}: {amount}{currency} ({rows} rows)",
        "count": "Count: {count}",
        "table": "{rows} rows found:",
        "more_rows": "... and {extra} more rows",
        "source": "Source: {source} / {sheet} (updated {revision})",
    },
    "fa": {
        "welcome": "سلام! دربارهٔ داده‌های شرکت از من بپرسید، مثلاً: «مجموع مصارف این ماه چقدر است؟»",
        "unauthorized": "شما اجازهٔ استفاده از این ربات را ندارید. از مدیر بخواهید آی‌دی تلگرام شما را اضافه کند: {user_id}",
        "busy": "سرویس هوش مصنوعی فعلاً مصروف است. لطفاً کمی بعد دوباره امتحان کنید.",
        "not_understood": "متأسفانه نتوانستم این سؤال را از روی داده‌های موجود جواب بدهم. لطفاً دقیق‌تر بپرسید.",
        "denied": "شما به این داده‌ها دسترسی ندارید.",
        "unavailable": "منبع داده فعلاً در دسترس نیست. لطفاً بعداً دوباره امتحان کنید.",
        "ambiguous": "داده‌های این شیت به شکلی نیست که بتوانم با اطمینان حساب کنم.",
        "voice_unsupported": "پیام صوتی هنوز فعال نشده است. لطفاً سؤال خود را بنویسید.",
        "text_only": "لطفاً سؤال خود را به شکل متن یا پیام صوتی بفرستید.",
        "error": "مشکلی پیش آمد. لطفاً دوباره امتحان کنید.",
        "sum": "مجموع {column}: {amount}{currency} ({rows} ردیف)",
        "count": "تعداد: {count}",
        "table": "{rows} ردیف پیدا شد:",
        "more_rows": "... و {extra} ردیف دیگر",
        "source": "منبع: {source} / {sheet} (به‌روزرسانی {revision})",
    },
    "ps": {
        "welcome": "سلام! د شرکت د معلوماتو په اړه راڅخه وپوښتئ، د بېلګې په توګه: «د دې میاشتې ټول لګښتونه څومره دي؟»",
        "unauthorized": "تاسو د دې ربات د کارولو اجازه نه لرئ. له مدیر څخه وغواړئ چې ستاسو د ټیلیګرام آی‌ډي ور زیاته کړي: {user_id}",
        "busy": "د مصنوعي ځیرکتیا خدمت اوس بوخت دی. مهرباني وکړئ لږ وروسته بیا هڅه وکړئ.",
        "not_understood": "بښنه غواړم، دا پوښتنه د شته معلوماتو له مخې ځواب نه شوه. مهرباني وکړئ دقیقه یې وپوښتئ.",
        "denied": "تاسو دې معلوماتو ته لاسرسی نه لرئ.",
        "unavailable": "د معلوماتو سرچینه اوس د لاسرسي وړ نه ده. مهرباني وکړئ وروسته بیا هڅه وکړئ.",
        "ambiguous": "په دې شیټ کې معلومات داسې نه دي چې په ډاډ سره یې حساب کړم.",
        "voice_unsupported": "غږیز پیغامونه لا فعال شوي نه دي. مهرباني وکړئ خپله پوښتنه ولیکئ.",
        "text_only": "مهرباني وکړئ خپله پوښتنه د متن یا غږیز پیغام په بڼه راولېږئ.",
        "error": "یوه ستونزه رامنځته شوه. مهرباني وکړئ بیا هڅه وکړئ.",
        "sum": "د {column} ټولټال: {amount}{currency} ({rows} کتارونه)",
        "count": "شمېر: {count}",
        "table": "{rows} کتارونه وموندل شول:",
        "more_rows": "... او {extra} نور کتارونه",
        "source": "سرچینه: {source} / {sheet} (تازه شوی {revision})",
    },
}

MAX_TABLE_ROWS = 15


def detect_language(text: str) -> str:
    """Return ``ps`` (Pashto), ``fa`` (Dari) or ``en`` from the script used."""

    if any(ch in _PASHTO_ONLY for ch in text):
        return "ps"
    if any(ord(ch) in _ARABIC_BLOCK for ch in text):
        return "fa"
    return "en"


def message(lang: str, key: str, **values: Any) -> str:
    table = MESSAGES.get(lang, MESSAGES["en"])
    return table[key].format(**values)


def _group_digits(amount: str) -> str:
    sign = "-" if amount.startswith("-") else ""
    whole, dot, frac = amount.lstrip("-").partition(".")
    grouped = f"{int(whole):,}" if whole.isdigit() else whole
    return f"{sign}{grouped}{dot}{frac}"


def render_answer(
    lang: str,
    data: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
) -> str:
    kind = data.get("kind")
    lines: list[str] = []
    if kind == "sum":
        currency = data.get("currency")
        lines.append(
            message(
                lang,
                "sum",
                column=data.get("column"),
                amount=_group_digits(str(data.get("amount"))),
                currency=f" {currency}" if currency else "",
                rows=data.get("matched_rows"),
            )
        )
    elif kind == "count":
        lines.append(message(lang, "count", count=data.get("count")))
    else:
        columns = list(data.get("columns") or [])
        rows = list(data.get("rows") or [])
        lines.append(message(lang, "table", rows=len(rows)))
        for row in rows[:MAX_TABLE_ROWS]:
            lines.append(" | ".join(f"{col}: {val}" for col, val in zip(columns, row, strict=False) if val))
        if len(rows) > MAX_TABLE_ROWS:
            lines.append(message(lang, "more_rows", extra=len(rows) - MAX_TABLE_ROWS))
    for source in sources:
        lines.append(
            message(
                lang,
                "source",
                source=source.get("source_id"),
                sheet=source.get("sheet"),
                revision=str(source.get("revision") or "")[:10],
            )
        )
    text = "\n".join(lines)
    return text if len(text) <= 4_000 else text[:3_990] + "\n..."
