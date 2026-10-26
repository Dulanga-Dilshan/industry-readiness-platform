"""Rule-based baseline classifier: job title patterns + a weighted skill list.

It is deliberately simple and explainable. Measure its accuracy on a hand-labelled
sample before trusting the dashboard numbers, and replace it later if needed
(keep classify()/classify_many() as the interface)."""
from dataclasses import dataclass, field

from .clusters import CLUSTERS, OTHER
from .rules import TITLE_PATTERNS
from .skills import SKILL_PATTERNS

TITLE_WEIGHT = 5.0     # score added when the title matches a cluster
MIN_SCORE = 2.0        # below this the job is "other"
STRONG_SCORE = 6.0     # score at which confidence is no longer reduced for weak evidence


@dataclass(frozen=True)
class Classification:
    cluster: str                                   # one of CLUSTERS or OTHER
    confidence: float                              # 0..1 heuristic, not a probability
    skills: tuple[str, ...] = ()                   # canonical skill names, A-Z
    scores: dict[str, float] = field(default_factory=dict)


def classify(title, description=None):
    title = title or ""
    text = f"{title}\n{description or ''}"

    scores = {c: 0.0 for c in CLUSTERS}
    title_hit = {c: False for c in CLUSTERS}
    for cluster, pattern in TITLE_PATTERNS.items():
        if pattern.search(title):
            scores[cluster] += TITLE_WEIGHT
            title_hit[cluster] = True

    skills = []
    for name, pattern, weights in SKILL_PATTERNS:
        if pattern.search(text):
            skills.append(name)
            for cluster, weight in weights.items():
                scores[cluster] += weight

    skills = tuple(sorted(skills))
    scores = {c: round(v, 2) for c, v in scores.items()}

    best = max(CLUSTERS, key=lambda c: (scores[c], title_hit[c]))
    top, total = scores[best], sum(scores.values())
    if top < MIN_SCORE:
        return Classification(OTHER, 0.0, skills, scores)

    confidence = (top / total) * min(1.0, top / STRONG_SCORE)
    return Classification(best, round(confidence, 2), skills, scores)


def classify_many(items):
    """items: iterable of (title, description). Returns a list in the same order."""
    return [classify(title, description) for title, description in items]
