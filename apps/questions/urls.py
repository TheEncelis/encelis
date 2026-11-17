from django.urls import path

from . import views

app_name = "questions"
urlpatterns = [path("<int:pk>/report/", views.report, name="report")]
