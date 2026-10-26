"""The career clusters used across the whole system (database, API, dashboard)."""

SOFTWARE = "software_engineering"
DEVOPS = "devops_cloud"
SECURITY = "cybersecurity"
NETWORKING = "networking_infrastructure"
DATA_AI = "data_ai"
OTHER = "other"          # not a technical IT role, or too little evidence

CLUSTERS = (SOFTWARE, DEVOPS, SECURITY, NETWORKING, DATA_AI)

CLUSTER_LABELS = {
    SOFTWARE: "Software Engineering",
    DEVOPS: "DevOps / Cloud Engineering",
    SECURITY: "Cybersecurity",
    NETWORKING: "Networking & Infrastructure",
    DATA_AI: "Data & AI Engineering",
    OTHER: "Other",
}
