"""Rule-based triage: intent, difficulty, cluster, window. An LLM is used only when rules cannot decide."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CLUSTER_ALIASES = {
    "Software Engineering": ["software", "backend", "back-end", "frontend", "front-end", "full stack", "fullstack",
                             "web developer", "developer", "programming", "qa", "tester", "testing"],
    "DevOps/Cloud": ["devops", "cloud", "aws", "azure", "gcp", "kubernetes", "docker", "sre", "ci/cd"],
    "Cybersecurity": ["security", "cybersecurity", "cyber", "pentest", "penetration", "soc analyst", "infosec"],
    "Networking & Infrastructure": ["network", "networking", "ccna", "infrastructure", "sysadmin", "system admin"],
    "Data & AI": ["data", "ai", "machine learning", "ml", "deep learning", "analytics", "data science"],
}
CHITCHAT = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|bye|ok|okay)\b[\s!.?]*$", re.I)
OFFTOPIC = re.compile(r"\b(recipe|cook|football|cricket score|movie|song lyrics|weather|horoscope|stock tips?|bitcoin|"
                      r"medical|diagnos|lawsuit|politic|election|dating|poem|joke|homework in (history|biology))\b", re.I)
CAREER_WORDS = re.compile(r"\b(career|job|jobs|skill|skills|learn|study|course|certif|salary|intern|resume|cv|interview|"
                          r"developer|engineer|analyst|admin|it|tech|software|code|coding|programming|module|semester|"
                          r"curriculum|degree|university|framework|tool|cloud|data|network|security|devops)\b", re.I)
DATA_WORDS = re.compile(r"\b(how many|number of|demand|trend|trending|top skills|most (demanded|wanted|popular)|"
                        r"job (postings|listings|openings)|openings|vacancies|hiring|posted|share of|percent|jobs? (for|in|near))\b", re.I)
CURRICULUM_WORDS = re.compile(r"\b(my (modules|courses|curriculum|semester)|which modules|skill gaps?|what am i missing|"
                              r"gaps? in my|am i (ready|covered)|my degree)\b", re.I)
ADVICE_WORDS = re.compile(r"\b(what should i (learn|study|do|focus)|roadmap|career plan|which (cluster|path|career)|"
                          r"should i (learn|choose|become|pick)|how (do|can) i (become|get into|start|prepare)|"
                          r"next steps?|plan for me|advice)\b", re.I)
KNOWLEDGE_WORDS = re.compile(r"\b(what is|what's|what are|explain|difference between|define|how does|how do|how to|"
                             r"tell me about|meaning of)\b", re.I)
HARD_PHRASES = ["compare", "plan", "explain why", "what should i learn", "roadmap", "step by step", "which is better"]
WINDOWS = [(re.compile(r"\b(last|past) (24 hours|day)|today\b", re.I), "24h"),
           (re.compile(r"\b(last|past) (30 days|month)\b|this month\b", re.I), "30d"),
           (re.compile(r"\b(last|past) (year|12 months)|this year\b", re.I), "1y"),
           (re.compile(r"\b(last|past) (6 months|half year)\b", re.I), "6m")]
SEMESTER = re.compile(r"\bsemester\s*(\d\.\d)\b|\b(\d\.\d)\s*semester\b", re.I)


@dataclass
class Triage:
    intent: str = "unclear"       # data | knowledge | curriculum | advice | chitchat | out_of_scope | unclear
    difficulty: str = "easy"
    cluster: str | None = None
    window: str = "6m"
    semester: str | None = None
    wants: set[str] = field(default_factory=set)      # data sub-intents: demand, top_skills, trend, jobs
    needs_llm: bool = False


def detect_cluster(text: str) -> str | None:
    low = text.lower()
    for cluster in CLUSTER_ALIASES:                    # exact cluster names first
        if cluster.lower() in low:
            return cluster
    best, best_len = None, 0
    for cluster, aliases in CLUSTER_ALIASES.items():
        for a in aliases:
            if re.search(rf"(?<![a-z]){re.escape(a)}(?![a-z])", low) and len(a) > best_len:
                best, best_len = cluster, len(a)
    return best


def detect_window(text: str) -> str:
    for pat, w in WINDOWS:
        if pat.search(text):
            return w
    return "6m"


def detect_semester(text: str) -> str | None:
    m = SEMESTER.search(text)
    return (m.group(1) or m.group(2)) if m else None


def classify(question: str, long_words: int = 25, hard_words: list[str] | None = None,
             known_terms: set[str] | None = None) -> Triage:
    q = question.strip()
    t = Triage(cluster=detect_cluster(q), window=detect_window(q), semester=detect_semester(q))
    low = q.lower()
    if CHITCHAT.match(q):
        t.intent = "chitchat"
        return t
    words = set(re.findall(r"[a-z0-9+#.]+", low))
    terms = known_terms or set()
    it_term = (bool(CAREER_WORDS.search(q)) or any(t in words for t in terms if " " not in t and len(t) >= 3)
               or any(t in low for t in terms if " " in t))
    if OFFTOPIC.search(q) and not it_term:
        t.intent = "out_of_scope"
        return t
    if ADVICE_WORDS.search(q):
        t.intent = "advice"
    elif CURRICULUM_WORDS.search(q):
        t.intent = "curriculum"
    elif DATA_WORDS.search(q):
        t.intent = "data"
        if re.search(r"\b(trend|trending|over time|growing|grown|change)\b", low):
            t.wants.add("trend")
        if re.search(r"\b(top|most|skills)\b", low):
            t.wants.add("top_skills")
        if re.search(r"\b(jobs? (for|in|near)|openings|vacancies|listings|postings for|hiring)\b", low):
            t.wants.add("jobs")
        if re.search(r"\b(how many|number of|share|percent|demand|compare|clusters?)\b", low) and not t.wants:
            t.wants.add("demand")
        if not t.wants:
            t.wants.add("demand")
    elif KNOWLEDGE_WORDS.search(q) or it_term:
        t.intent = "knowledge"
    else:
        t.intent = "unclear"
        t.needs_llm = True
        return t
    hard = [p for p in (hard_words or HARD_PHRASES) if p in low]
    words = len(q.split())
    if t.intent == "advice" or hard:
        t.difficulty = "hard"
    elif t.intent in ("data", "curriculum") or words >= long_words:
        t.difficulty = "medium"
    else:
        t.difficulty = "easy"
    return t
