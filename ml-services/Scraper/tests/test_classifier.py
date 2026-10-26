import pytest

from classifier import CLUSTERS, OTHER, classify, classify_many

CASES = [
    ("DevOps Engineer Intern", "Docker, Kubernetes, CI/CD pipelines on AWS", "devops_cloud"),
    ("QA Intern", "Write test cases, Selenium, Postman", "software_engineering"),
    ("SOC Analyst", "Monitor SIEM alerts, incident response", "cybersecurity"),
    ("Network Engineer", "CCNA, routing and switching, firewall", "networking_infrastructure"),
    ("Data Scientist Intern", "Python, pandas, machine learning, Power BI", "data_ai"),
    ("Backend Developer", "Java, Spring Boot, MySQL, REST APIs", "software_engineering"),
    ("Graphic Designer", "Photoshop and Illustrator", OTHER),
    ("HR Intern", "recruitment and MS Office", OTHER),
]


@pytest.mark.parametrize("title,description,expected", CASES)
def test_cluster(title, description, expected):
    assert classify(title, description).cluster == expected


def test_title_alone_is_enough():
    r = classify("Software Engineer Intern", "")
    assert r.cluster == "software_engineering" and r.confidence > 0.5


def test_skills_are_whole_words():
    skills = classify("Dev", "We use JavaScript, MySQL and C++ and C#.").skills
    assert "JavaScript" in skills and "MySQL" in skills and "C++" in skills and "C#" in skills
    assert "Java" not in skills and "SQL" not in skills      # not inside javascript / mysql


def test_empty_input_is_other():
    assert classify(None, None).cluster == OTHER
    assert classify("", "").confidence == 0.0


def test_classify_many_keeps_order():
    out = classify_many([(t, d) for t, d, _ in CASES])
    assert [r.cluster for r in out] == [c for _, _, c in CASES]
    assert all(r.cluster in CLUSTERS or r.cluster == OTHER for r in out)
