import re

from .iconsets import IconSets
from .keywords import PHOSPHOR_KEYWORDS, english_fragments
from .textutil import normalize_text

MIN_WORD_LENGTH = 3
WORD = re.compile(r"[^\W\d_]+")
CAMEL_CASE = re.compile(r"(?<=[a-z])(?=[A-Z])")
QUOTAS = (("phosphor", 4), ("brands", 2), ("emoji", 3))
EMOJI_WORST_RANK = 3
EXACT_RANK = 0
PREFIX_RANK = 1
PHOSPHOR_PER_WORD = 3


def folder_words(folder_name: str) -> list[str]:
    words = []
    for word in WORD.findall(normalize_text(CAMEL_CASE.sub(" ", folder_name))):
        if len(word) >= MIN_WORD_LENGTH and word not in words:
            words.append(word)
    return words


def candidates(word: str) -> list[str]:
    found = [word]
    if len(word) > MIN_WORD_LENGTH and word.endswith(("s", "x")):
        found.append(word[:-1])
    return found


def round_robin(lists: list[list[str]]) -> list[str]:
    merged = []
    for position in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if position < len(items) and items[position] not in merged:
                merged.append(items[position])
    return merged


def phosphor_matches(sets: IconSets, word: str) -> list[str]:
    icon_set = sets["phosphor"]
    found = []
    for candidate in candidates(word):
        for fragment in english_fragments(candidate, PHOSPHOR_KEYWORDS):
            found.extend(name for name in icon_set.symbolic_names(fragment, limit=1) if name not in found)
    if not found:
        for candidate in candidates(word):
            found.extend(name for name in icon_set.symbolic_names(candidate, limit=2, max_rank=PREFIX_RANK) if name not in found)
    return found[:PHOSPHOR_PER_WORD]


def emoji_matches(sets: IconSets, word: str) -> list[str]:
    for candidate in candidates(word):
        found = sets["emoji"].symbolic_names(candidate, limit=2, max_rank=EMOJI_WORST_RANK)
        if found:
            return found
    return []


def brand_matches(sets: IconSets, word: str) -> list[str]:
    return [name for candidate in candidates(word) for name in sets["brands"].symbolic_names(candidate, limit=1, max_rank=EXACT_RANK)]


def suggest(folder_name: str, sets: IconSets, limit: int = 8) -> list[str]:
    words = folder_words(folder_name)
    pools = {
        "phosphor": round_robin([phosphor_matches(sets, word) for word in words]),
        "emoji": round_robin([emoji_matches(sets, word) for word in words]),
        "brands": round_robin([brand_matches(sets, word) for word in words]),
    }
    chosen = []
    for key, quota in QUOTAS:
        chosen.extend(name for name in pools[key][:quota] if name not in chosen)
    for key, _quota in QUOTAS:
        chosen.extend(name for name in pools[key] if name not in chosen)
    return chosen[:limit]
