"""Small cache-backed fixed-window rate limiter."""
import time
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse


def client_ip(request):
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def hit(scope, ident, limit, window):
    """Register a hit. Returns True while the caller is within the limit."""
    bucket = int(time.time() // window)
    key = f"rl:{scope}:{ident}:{bucket}"
    if cache.add(key, 1, window):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count <= limit


def ratelimit(scope, limit, window, methods=("POST",), per="ip"):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method in methods:
                ident = request.user.pk if per == "user" and request.user.is_authenticated else client_ip(request)
                if not hit(scope, ident, limit, window):
                    return HttpResponse(
                        "Juda ko'p urinish. Birozdan so'ng qayta urinib ko'ring.", status=429, content_type="text/plain; charset=utf-8"
                    )
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
