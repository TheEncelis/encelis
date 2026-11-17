from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.urls")),  # /login/, /register/, /settings/, ...
    path("", include("apps.learning.urls")),  # /dashboard/, /learn/...
    path("placement/", include("apps.assessments.urls")),
    path("curriculum/", include("apps.curriculum.urls")),
    path("questions/", include("apps.questions.urls")),
    path("progress/", include("apps.analytics.urls")),
    path("studio/", include("apps.studio.urls")),
]

handler404 = "apps.core.views.not_found"
handler403 = "apps.core.views.forbidden"
