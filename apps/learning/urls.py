from django.urls import path

from . import views

app_name = "learning"
urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("learn/start/", views.start, name="start"),
    path("learn/session/<int:pk>/", views.session_view, name="session"),
    path("learn/session/<int:pk>/next/", views.next_item, name="next"),
    path("learn/session/<int:pk>/finish/", views.finish, name="finish"),
    path("learn/session/<int:pk>/summary/", views.summary, name="summary"),
    path("learn/session/<int:pk>/theory/<int:item_id>/", views.theory_done, name="theory_done"),
    path("learn/session/<int:pk>/answer/<int:item_id>/", views.answer, name="answer"),
    path("learn/explain/<int:attempt_id>/", views.explain, name="explain"),
]
