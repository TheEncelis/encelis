class HtmxMiddleware:
    """Sets request.htmx for requests issued by htmx."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.htmx = request.headers.get("HX-Request") == "true"
        return self.get_response(request)
