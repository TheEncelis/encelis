from django.urls import path

from . import views

app_name = "curriculum"
urlpatterns = [
    path("", views.curriculum_map, name="map"),
    path("<slug:slug>/", views.topic_detail, name="topic"),
]
