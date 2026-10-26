"""Curated skill list. Each skill has aliases and a weight per cluster:
how strongly seeing that skill suggests the cluster. Matching is
case-insensitive and whole-word, so 'java' does not match 'javascript' and
'sql' does not match 'mysql'. Extend this list as you inspect real postings."""
import re

from .clusters import DATA_AI as A, DEVOPS as D, NETWORKING as N, SECURITY as C, SOFTWARE as S

# (canonical name, [aliases], {cluster: weight})
_SKILLS = [
    # --- languages, frameworks, software ---
    ("Python", ["python"], {S: .5, A: .6, D: .3}),
    ("Java", ["java"], {S: 1}),
    ("JavaScript", ["javascript", "js", "ecmascript"], {S: 1}),
    ("TypeScript", ["typescript"], {S: 1}),
    ("C#", ["c#"], {S: 1}),
    ("C++", ["c++"], {S: .8}),
    ("PHP", ["php"], {S: 1}),
    ("Go", ["golang"], {S: .5, D: .5}),
    ("Kotlin", ["kotlin"], {S: 1}),
    ("Swift", ["swiftui", "swift programming"], {S: 1}),
    ("Flutter", ["flutter", "dart"], {S: 1}),
    ("React", ["react", "reactjs", "react.js"], {S: 1}),
    ("React Native", ["react native"], {S: 1}),
    ("Angular", ["angular", "angularjs"], {S: 1}),
    ("Vue.js", ["vue", "vue.js", "vuejs"], {S: 1}),
    ("Node.js", ["node.js", "nodejs", "node js"], {S: 1}),
    ("Spring Boot", ["spring boot", "spring framework"], {S: 1}),
    (".NET", [".net", "dotnet", "asp.net", "asp.net core"], {S: 1}),
    ("Laravel", ["laravel"], {S: 1}),
    ("Django", ["django"], {S: 1}),
    ("Flask", ["flask"], {S: .8}),
    ("FastAPI", ["fastapi"], {S: .8}),
    ("Android", ["android"], {S: 1}),
    ("iOS", ["ios"], {S: 1}),
    ("HTML/CSS", ["html", "css", "html5", "css3"], {S: .6}),
    ("REST API", ["rest api", "rest apis", "restful"], {S: .7}),
    ("GraphQL", ["graphql"], {S: .7}),
    ("Microservices", ["microservices", "microservice"], {S: .6, D: .3}),
    ("SQL", ["sql"], {S: .5, A: 1}),
    ("MySQL", ["mysql"], {S: .5, A: .3}),
    ("PostgreSQL", ["postgresql", "postgres"], {S: .5, A: .4}),
    ("MongoDB", ["mongodb"], {S: .5, A: .3}),
    ("Redis", ["redis"], {S: .4, D: .3}),
    ("Git", ["git", "github", "gitlab", "bitbucket"], {S: .3, D: .3}),
    # --- testing / QA ---
    ("Selenium", ["selenium"], {S: 1}),
    ("Cypress", ["cypress"], {S: 1}),
    ("Playwright", ["playwright"], {S: 1}),
    ("JUnit", ["junit", "testng"], {S: 1}),
    ("Postman", ["postman"], {S: .8}),
    ("JMeter", ["jmeter"], {S: 1}),
    ("Appium", ["appium"], {S: 1}),
    ("Test Automation", ["test automation", "automation testing", "automated testing"], {S: 1}),
    ("Manual Testing", ["manual testing", "test cases", "test case"], {S: 1}),
    # --- devops / cloud ---
    ("Docker", ["docker"], {D: 1.2, S: .3}),
    ("Kubernetes", ["kubernetes", "k8s"], {D: 1.5}),
    ("Terraform", ["terraform"], {D: 1.5}),
    ("Ansible", ["ansible"], {D: 1.2}),
    ("Jenkins", ["jenkins"], {D: 1.2}),
    ("CI/CD", ["ci/cd", "cicd", "ci-cd", "github actions", "gitlab ci", "continuous integration"], {D: 1.2, S: .2}),
    ("AWS", ["aws", "amazon web services"], {D: 1.2}),
    ("Azure", ["azure"], {D: 1.2}),
    ("Google Cloud", ["gcp", "google cloud"], {D: 1.2}),
    ("Linux", ["linux", "ubuntu", "centos", "red hat"], {D: .6, N: .6}),
    ("Helm", ["helm chart", "helm charts"], {D: 1}),
    ("Prometheus", ["prometheus", "grafana"], {D: 1}),
    ("Infrastructure as Code", ["infrastructure as code", "iac", "cloudformation"], {D: 1.3}),
    ("Bash", ["bash", "shell scripting"], {D: .6, N: .2}),
    # --- security ---
    ("SIEM", ["siem", "splunk", "qradar"], {C: 1.5}),
    ("Penetration Testing", ["penetration testing", "pentest", "pen testing", "ethical hacking"], {C: 2}),
    ("OWASP", ["owasp"], {C: 1.5, S: .2}),
    ("Vulnerability Assessment", ["vulnerability assessment", "vulnerability management", "nessus", "qualys"], {C: 1.5}),
    ("Metasploit", ["metasploit", "burp suite", "kali linux"], {C: 1.5}),
    ("ISO 27001", ["iso 27001", "iso27001", "nist", "soc 2"], {C: 1.2}),
    ("Incident Response", ["incident response", "threat hunting", "threat detection", "malware analysis"], {C: 1.5}),
    ("Firewall", ["firewall", "firewalls"], {C: .6, N: .6}),
    ("Security Certifications", ["ceh", "cissp", "security+", "comptia security"], {C: 1.2}),
    ("IAM", ["iam", "identity and access management"], {C: .8, D: .3}),
    ("Cybersecurity", ["cybersecurity", "cyber security", "information security", "infosec"], {C: 1.5}),
    # --- networking / infrastructure ---
    ("CCNA", ["ccna", "ccnp", "ccie", "cisco certified"], {N: 1.5}),
    ("Cisco", ["cisco"], {N: 1}),
    ("TCP/IP", ["tcp/ip", "tcp ip", "ospf", "bgp"], {N: 1}),
    ("Routing & Switching", ["routing and switching", "switching and routing", "routing & switching", "vlan"], {N: 1}),
    ("Active Directory", ["active directory", "group policy"], {N: 1}),
    ("DNS/DHCP", ["dns", "dhcp"], {N: .8}),
    ("VPN", ["vpn"], {N: .6, C: .3}),
    ("Windows Server", ["windows server"], {N: 1}),
    ("Network Monitoring", ["network monitoring", "nagios", "zabbix", "solarwinds", "prtg"], {N: 1}),
    ("MikroTik", ["mikrotik", "fortinet", "fortigate", "juniper"], {N: 1, C: .3}),
    ("VMware", ["vmware", "hyper-v", "virtualization", "virtualisation"], {N: .8, D: .4}),
    ("Hardware Support", ["hardware troubleshooting", "helpdesk", "help desk", "desktop support", "technical support"], {N: 1}),
    ("Microsoft 365", ["office 365", "microsoft 365", "m365"], {N: .5}),
    # --- data / AI ---
    ("Pandas", ["pandas"], {A: 1}),
    ("NumPy", ["numpy"], {A: 1}),
    ("scikit-learn", ["scikit-learn", "sklearn"], {A: 1.2}),
    ("TensorFlow", ["tensorflow"], {A: 1.5}),
    ("PyTorch", ["pytorch"], {A: 1.5}),
    ("Keras", ["keras"], {A: 1.2}),
    ("Machine Learning", ["machine learning"], {A: 2}),
    ("Deep Learning", ["deep learning", "neural network", "neural networks"], {A: 1.5}),
    ("NLP", ["nlp", "natural language processing"], {A: 1.5}),
    ("Computer Vision", ["computer vision", "opencv"], {A: 1.5}),
    ("LLM", ["llm", "llms", "large language model", "generative ai", "langchain"], {A: 1.5}),
    ("RAG", ["rag", "retrieval augmented generation", "vector database"], {A: 1.2}),
    ("Power BI", ["power bi", "powerbi"], {A: 1.5}),
    ("Tableau", ["tableau"], {A: 1.5}),
    ("Spark", ["apache spark", "pyspark", "hadoop"], {A: 1.5}),
    ("Airflow", ["airflow", "dbt"], {A: 1.2}),
    ("ETL", ["etl", "data pipeline", "data pipelines", "data warehouse", "data warehousing"], {A: 1.5}),
    ("Snowflake", ["snowflake", "bigquery", "redshift", "databricks"], {A: 1.5}),
    ("Kafka", ["kafka"], {A: .7, S: .3}),
    ("Data Analysis", ["data analysis", "data analytics", "statistics", "data visualization", "data visualisation"], {A: 1}),
]


def _compile(aliases):
    alt = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    # not preceded/followed by letters, digits, + # (so C++, C# and .NET work)
    return re.compile(rf"(?<![A-Za-z0-9_+#.])(?:{alt})(?![A-Za-z0-9_+#])", re.IGNORECASE)


SKILL_PATTERNS = [(name, _compile(aliases), weights) for name, aliases, weights in _SKILLS]
