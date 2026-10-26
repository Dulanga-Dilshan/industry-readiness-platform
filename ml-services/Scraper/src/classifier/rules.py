"""Job-title patterns per cluster. A title match is strong evidence."""
import re

from .clusters import DATA_AI, DEVOPS, NETWORKING, SECURITY, SOFTWARE

_RAW = {
    SOFTWARE: r"software|developer|programmer|full[\s-]?stack|front[\s-]?end|back[\s-]?end|"
              r"web (developer|engineer)|mobile|android|\bios\b|\bqa\b|quality assurance|"
              r"\btest(er|ing)?\b|\bsdet\b|\.net|\bjava\b|\breact\b",
    DEVOPS: r"devops|\bsre\b|site reliability|cloud (engineer|architect|specialist)|"
            r"platform engineer|release engineer",
    SECURITY: r"security|cyber|\bsoc\b|penetration|pentest|infosec|vulnerability|\bgrc\b",
    NETWORKING: r"network|system(s)? admin|sysadmin|it support|help\s?desk|infrastructure|"
                r"hardware|technical support|\bnoc\b|it technician|it operations",
    DATA_AI: r"data (scientist|analyst|engineer)|machine learning|\bml\b|\bai\b|"
             r"artificial intelligence|business intelligence|\bbi\b|analytics|\bnlp\b|"
             r"computer vision|deep learning|mlops",
}

TITLE_PATTERNS = {cluster: re.compile(p, re.IGNORECASE) for cluster, p in _RAW.items()}
