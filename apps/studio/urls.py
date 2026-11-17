from django.urls import path

from . import views

app_name = "studio"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("learners/", views.users, name="users"),
    path("learners/<int:pk>/", views.user_detail, name="user"),
    path("curriculum/", views.curriculum_view, name="curriculum"),
    path("curriculum/reorder/", views.reorder, name="reorder"),
    path("curriculum/units/new/", views.unit_form, name="unit_new"),
    path("curriculum/units/<int:pk>/", views.unit_form, name="unit_edit"),
    path("curriculum/units/<int:pk>/visibility/", views.unit_toggle, name="unit_toggle"),
    path("curriculum/topics/new/", views.topic_form, name="topic_new"),
    path("curriculum/topics/<int:pk>/", views.topic_form, name="topic_edit"),
    path("curriculum/topics/<int:pk>/status/", views.topic_status, name="topic_status"),
    path("curriculum/topics/<int:pk>/theory/", views.topic_theory, name="topic_theory"),
    path("curriculum/topics/<int:pk>/theory/new/", views.block_new, name="block_new"),
    path("curriculum/topics/<int:pk>/theory/<int:block_id>/", views.block_edit, name="block_edit"),
    path("curriculum/topics/<int:pk>/theory/<int:block_id>/delete/", views.block_delete, name="block_delete"),
    path("curriculum/topics/<int:pk>/theory/drafts/<int:job_id>/apply/", views.theory_apply, name="theory_apply"),
    path("questions/", views.questions, name="questions"),
    path("questions/new/", views.question_form, name="question_new"),
    path("questions/bulk/", views.questions_bulk, name="questions_bulk"),
    path("questions/<int:pk>/", views.question_form, name="question_edit"),
    path("questions/<int:pk>/status/", views.question_status, name="question_status"),
    path("questions/<int:pk>/verdicts/<int:verdict_id>/", views.verdict_override, name="verdict_override"),
    path("reports/", views.reports, name="reports"),
    path("reports/<int:pk>/", views.report_detail, name="report"),
    path("ai/", views.ai_studio, name="ai"),
    path("ai/jobs/<int:pk>/", views.job_detail, name="job"),
    path("settings/", views.settings_view, name="settings"),
]
