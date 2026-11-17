from pathlib import Path

from django.conf import settings

_ASSETS = ("css/app.css", "js/app.js", "js/studio.js")


def _asset_version():
    """In development, bust the browser cache whenever a built asset changes.
    In production the hashed filenames of ManifestStaticFilesStorage do this."""
    if not settings.DEBUG:
        return ""
    root = Path(settings.BASE_DIR) / "static"
    return str(max(int((root / name).stat().st_mtime) for name in _ASSETS if (root / name).exists()))


def ui(request):
    theme = "auto"
    if request.user.is_authenticated:
        theme = request.user.theme
    return {"ui_theme": theme, "asset_v": _asset_version()}
