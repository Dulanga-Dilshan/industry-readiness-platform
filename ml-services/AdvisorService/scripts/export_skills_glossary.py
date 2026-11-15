"""Export canonical skill names (and aliases) from the scraper's classifier/skills.py.

Run from the AdvisorService folder:
    uv run python scripts/export_skills_glossary.py

First run: leave SKILLS_ATTRIBUTE empty. The script lists what skills.py contains.
Then set SKILLS_ATTRIBUTE (and ALIASES_ATTRIBUTE) below and run again.
It writes data/skills_glossary.txt and data/skill_aliases.txt.
"""
import importlib.util
import pathlib

SKILLS_PY = "../Scraper/src/classifier/skills.py"
SKILLS_ATTRIBUTE = ""       # e.g. "SKILLS": a dict keyed by skill name, a list of names, or a list of dicts with "name"
ALIASES_ATTRIBUTE = ""      # e.g. "ALIASES": a dict {alias: canonical_name}; leave empty if none

ROOT = pathlib.Path(__file__).resolve().parents[1]
path = (ROOT / SKILLS_PY).resolve()
spec = importlib.util.spec_from_file_location("skills_module", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def names_from(obj):
    if isinstance(obj, dict):
        return [str(k) for k in obj]
    out = []
    for item in obj:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and "name" in item:
            out.append(str(item["name"]))
        elif isinstance(item, (tuple, list)) and item:
            out.append(str(item[0]))
    return out


if not SKILLS_ATTRIBUTE:
    print(f"Module-level names in {path}:")
    for n in dir(mod):
        v = getattr(mod, n)
        if not n.startswith("_") and isinstance(v, (dict, list, tuple, set)):
            print(f"  {n}: {type(v).__name__} with {len(v)} items")
    print("\nSet SKILLS_ATTRIBUTE (and ALIASES_ATTRIBUTE) at the top of this script, then run it again.")
else:
    skills = sorted(set(names_from(getattr(mod, SKILLS_ATTRIBUTE))), key=str.lower)
    (ROOT / "data" / "skills_glossary.txt").write_text("\n".join(skills) + "\n", encoding="utf-8")
    print(f"wrote {len(skills)} skills")
    if ALIASES_ATTRIBUTE:
        aliases = sorted(set(names_from(getattr(mod, ALIASES_ATTRIBUTE))), key=str.lower)
        (ROOT / "data" / "skill_aliases.txt").write_text("\n".join(aliases) + "\n", encoding="utf-8")
        print(f"wrote {len(aliases)} aliases")
