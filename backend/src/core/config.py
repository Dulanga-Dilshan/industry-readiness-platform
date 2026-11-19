import os


SCRAPER_URL = os.getenv('SCRAPPER_URL','http://localhost:9000/scraper')
ADVISOR_URL = os.getenv('ADVISOR_URL','http://localhost:8001/advisor')
REDIS_URL = os.getenv('REDIS_URL','redis://127.0.0.1:6379/0')
