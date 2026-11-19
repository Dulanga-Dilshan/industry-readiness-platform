import requests
import json

from fastapi import APIRouter
from core import config
from api.deps import RedisDep


router = APIRouter()

@router.get("/health")
async def health_check(redis: RedisDep):
    services_info ={
        'status' : "ok"
    }
    cache_key = "health:status"

    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

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
    
    await redis.set(cache_key, json.dumps(services_info), ex=300) #cache for 5m 

    return services_info



    