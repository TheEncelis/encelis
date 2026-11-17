from django.shortcuts import redirect, render


def landing(request):
    if request.user.is_authenticated:
        return redirect("studio:dashboard" if request.user.is_staff else "learning:dashboard")
    return render(request, "core/landing.html")


def not_found(request, exception=None):
    return render(request, "core/error.html", {"code": 404, "title": "Sahifa topilmadi", "text": "Bu manzilda hech narsa yo'q."}, status=404)


def forbidden(request, exception=None):
    return render(request, "core/error.html", {"code": 403, "title": "Ruxsat yo'q", "text": "Bu sahifani ko'rish uchun huquqingiz yetarli emas."}, status=403)
