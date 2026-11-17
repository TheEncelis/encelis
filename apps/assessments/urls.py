from django.urls import path

from . import views

app_name = "assessments"
urlpatterns = [
    path("", views.start, name="start"),
    path("begin/", views.begin, name="begin"),
    path("question/", views.question, name="question"),
    path("answer/", views.answer, name="answer"),
    path("skip/", views.skip, name="skip"),
    path("result/", views.result, name="result"),
]
