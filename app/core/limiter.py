from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# Global rate-limiter instance.
# key_func: use the client's real IP address as the rate-limit key.
# storage_uri: backed by Redis so limits are shared across multiple workers.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
)
