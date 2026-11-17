import requests
from fastapi import APIRouter
from core import config
router = APIRouter()

@router.get("/health")
async def health_check():
    services_info ={
        'status' : "ok"
    }
    try:
        response = requests.get(f"{config.SCRAPER_URL}/health",timeout=2)
        if response.status_code != 200:
            services_info['scraper'] = 'offline'
        else:
            services_info['scraper'] = response.json()
    except:
        services_info['scraper'] = 'offline'

    try:
        response = requests.get(f"{config.ADVISOR_URL}/health",timeout=2)
        print(f"{config.ADVISOR_URL}/health")
        if response.status_code != 200:
            services_info['advisor'] = 'offline'
        else:
            services_info['advisor'] = response.json()
    except:
        services_info['advisor'] = 'offline'

    return services_info

    