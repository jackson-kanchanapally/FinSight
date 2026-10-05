"""Page/section/paragraph and speaker-aware chunking of already parsed text."""
import re
from dataclasses import dataclass

VERSION = "investment_sections_v4"
SPEAKER = re.compile(r"^(?!(?:Sub|Date|Source|Note|Notes|Revenue|Total|Ref|Disclaimer|Particulars|Others):)((?:Management|CEO|CFO|Chairman|Moderator|Operator|Analyst|Managing Director|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})(?:\s*\([^)]{1,60}\))?)\s*:\s+")
TOPICS = {
    "GUIDANCE": r"\bguid(?:e|ed|ance|ing)\b|\boutlook\b|\bexpect\w*\b|\btarget\w*\b",
    "MARGINS": r"\bmargin\w*\b|\bprofitability\b|\bcost\w*\b",
    "RISKS": r"\brisk\w*\b|\buncertain\w*\b|\bconcentration\b|\blitigation\b",
    "STRATEGY": r"\bstrateg\w*\b|\bexpan\w*\b|\bcapacity\b|\bcapex\b",
    "EXECUTION": r"\bdeliver\w*\b|\bachiev\w*\b|\bmiss\w*\b|\bmilestone\w*\b",
    "REVENUE": r"\brevenue\b|\bsales\b|\bdemand\b",
    "SEGMENTS": r"\bsegment\w*\b|\bdivision\w*\b",
    "CASH_FLOW": r"\bcash flow\b|\bworking capital\b|\bliquidity\b",
}


def topics(text: str) -> list[str]:
    return [name for name, pattern in TOPICS.items() if re.search(pattern, text, re.I)]


def normalize(text: str) -> str:
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r", "").split("\n")).strip()


@dataclass(frozen=True)
class Chunk:
    text: str
    section: str | None
    speaker: str | None
    topics: list[str]
    scope: str | None


def heading(line: str) -> bool:
    return bool(re.match(r"^Q\d+[.)]\s", line) and len(line) <= 240) or (3 <= len(line) <= 100 and not SPEAKER.match(line)
            and not re.search(r"[.!?;]$|\d{2,}", line)
            and (line.isupper() or (len(line.split()) <= 9 and line.istitle())))


def chunk_page(text: str, max_chars: int = 1600, overlap: int = 160) -> list[Chunk]:
    """Never cross a page or speaker boundary. Split oversized units at natural breaks.

    Overlap is a trailing complete line/sentence, only within an oversized unit.
    Tables retain rows and an explicit section; no invented table interpretations.
    """
    text = normalize(text)
    table_header = "\n".join(line for line in text.splitlines() if re.search(
        r"(?:quarter|year|period)\s+ended|particulars|(?:rs\.?|inr|₹).{0,35}(?:crore|million|lakh)|"
        r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", line, re.I))[:250]
    units: list[tuple[str, str | None, str | None]] = []
    lines: list[str] = []
    section = speaker = None
    question_lines = []

    def flush():
        if lines:
            units.append(("\n".join(lines), section, speaker))
            lines.clear()

    for line in text.splitlines():
        match = SPEAKER.match(line)
        if re.match(r"^Q\d+[.)]\s", line):
            flush()
            question_lines = [line]
            speaker = None
        elif question_lines and not match:
            if line:
                question_lines.append(line)
        elif match:
            flush()
            if question_lines:
                section = " ".join(question_lines)[:700]
                question_lines = []
            speaker = match.group(1)
            lines.append(line)
        elif not line:
            flush()
        elif heading(line):
            flush()
            section, speaker = line, None
        else:
            lines.append(line)
    flush()
    packed = []
    for unit, section, speaker in units:
        if re.search(r"forward.looking statements|(?:any investment|investing) in securities|digitally signed|"
                     r"nothing has come to our attention|responsibility of the.*management.*approved|"
                     r"responsibility is to express|review of interim financial information performed",
                     unit, re.I | re.S):
            # Keep explicit adverse/qualified-audit evidence; omit standard assurances,
            # signatures and generic disclaimers from investment-research memory.
            if not re.search(r"qualified opinion|adverse opinion|material uncertainty|going concern|except for|draw attention", unit, re.I):
                continue
        if (packed and packed[-1][1:] == (section, speaker)
                and len(packed[-1][0]) + len(unit) + 2 <= max_chars):
            packed[-1] = (packed[-1][0] + "\n\n" + unit, section, speaker)
        else:
            packed.append((unit, section, speaker))
    result = []
    for unit, section, speaker in packed:
        remaining = unit
        carry = ""
        table_context = table_header if sum(len(re.findall(r"\d+(?:\.\d+)?", line)) >= 2 for line in unit.splitlines()) >= 3 else ""
        while remaining:
            budget = max_chars - len(carry) - (len(table_context) + 1 if table_context else 0)
            if len(remaining) <= budget:
                piece, remaining = remaining, ""
            else:
                # Prefer a row/line, then a sentence, then a word boundary.
                cut = remaining.rfind("\n", budget // 2, budget)
                if cut < 0:
                    breaks = list(re.finditer(r"[.!?]\s+", remaining[:budget]))
                    cut = breaks[-1].end() if breaks and breaks[-1].end() >= budget // 2 else -1
                if cut < 0:
                    cut = remaining.rfind(" ", budget // 2, budget)
                cut = cut if cut > 0 else budget
                piece, remaining = remaining[:cut].strip(), remaining[cut:].lstrip()
            body = (carry + piece).strip()
            if table_context and table_context not in body:
                body = table_context + "\n" + body
            scopes = re.findall(r"\b(standalone|consolidated)\b", (section or "") + " " + body, re.I)
            distinct = {v.upper() for v in scopes}
            if len(body) >= 35 and re.search(r"[A-Za-z]{3}", body):
                result.append(Chunk(body, section, speaker, topics((section or "") + " " + body),
                                    next(iter(distinct)) if len(distinct) == 1 else None))
            carry = ""
            if remaining and overlap:
                tail = re.split(r"\n|(?<=[.!?])\s+", piece)[-1]
                if 20 <= len(tail) <= min(overlap, max_chars // 4):
                    carry = tail + "\n"
    return result
