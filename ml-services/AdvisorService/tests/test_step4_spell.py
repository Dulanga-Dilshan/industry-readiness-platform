import os

from advisor.curriculum.validator import load_glossary
from advisor.text.spellcheck import SpellCorrector, normalize

GLOSS = load_glossary(os.path.join(os.path.dirname(__file__), "..", "data", "skills_glossary.txt"))


def sc():
    s = SpellCorrector(GLOSS)
    s.add_terms(["Software Engineering", "DevOps/Cloud", "Cybersecurity", "Networking & Infrastructure", "Data & AI",
                 "Kubernetes", "Terraform", "Postman", "Nginx", "Redis"])
    return s


def test_typos_fixed():
    r = sc().correct("wat shoud I lern to become a developer")
    assert r.changed
    assert "learn" in r.corrected and "should" in r.corrected


def test_common_typos_with_one_edit():
    r = sc().correct("I want to become a develper and write good softwre")
    assert "developer" in r.corrected and "software" in r.corrected


def test_glossary_term_typo_corrected_to_glossary():
    r = sc().correct("how do I learn Pyhton for backend work")
    assert "Python" in r.corrected
    assert ("Pyhton", "Python") in r.changes


def test_glossary_terms_untouched():
    q = "Do I need Kubernetes, Terraform, Postman, Nginx and Redis for DevOps/Cloud work in Cybersecurity?"
    assert sc().correct(q).corrected == q


def test_code_like_tokens_untouched():
    q = "why does node_modules fail with getUserById and v2.3.1 or https://exmaple.com/pagee?"
    r = sc().correct(q)
    assert "node_modules" in r.corrected and "getUserById" in r.corrected
    assert "v2.3.1" in r.corrected and "https://exmaple.com/pagee" in r.corrected


def test_acronyms_untouched():
    q = "Is CCNA or CISSP better than AWS SAA for a NOC role?"
    assert sc().correct(q).corrected == q


def test_ambiguous_or_unknown_words_left_alone():
    q = "what is a flibbertigibbet in Kubernetes?"
    assert sc().correct(q).corrected == q


def test_low_confidence_leaves_text_unchanged_and_flags_rewrite():
    q = "xqzv plorf wrnk dvsq zzqx lmnop qrstv"
    r = sc().correct(q)
    assert r.corrected == q and r.needs_rewrite


def test_case_preserved_and_clean_text_unchanged():
    r = sc().correct("Wat is QA?")
    assert r.corrected.startswith("What") or r.corrected.startswith("Wat")
    clean = "What should I learn to get a job as a software engineer?"
    assert sc().correct(clean).corrected == clean and not sc().correct(clean).needs_rewrite


def test_normalize_whitespace():
    assert normalize("  hello \n\t world\u200b ") == "hello world"
