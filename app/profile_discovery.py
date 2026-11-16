from __future__ import annotations

from .models import ProfileCandidate


def rank_candidates(candidates: list[ProfileCandidate]) -> list[ProfileCandidate]:
    if not candidates:
        return []
    unique: dict[str, ProfileCandidate] = {candidate.note_id: candidate for candidate in candidates}
    candidates = list(unique.values())
    # Explicitly pinned entries are excluded. Unknown pin status is retained,
    # because xhs-cli currently does not expose the marker; detail timestamps
    # remain the authoritative ordering field.
    non_pinned = [candidate for candidate in candidates if candidate.is_pinned is not True]
    pool = non_pinned or candidates

    def sort_key(candidate: ProfileCandidate) -> tuple[int, str]:
        return (1 if candidate.published_at else 0, candidate.published_at or "")

    return sorted(pool, key=sort_key, reverse=True)


def select_latest(candidates: list[ProfileCandidate]) -> ProfileCandidate | None:
    ranked = rank_candidates(candidates)
    return ranked[0] if ranked else None
