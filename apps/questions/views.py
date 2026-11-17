from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.core.ratelimit import ratelimit
from apps.learning.models import Attempt

from .models import Question, QuestionReport
from .services import file_report


@login_required
@require_POST
@ratelimit("report", limit=20, window=3600, per="user")
def report(request, pk):
    question = get_object_or_404(Question, pk=pk)
    attempt = Attempt.objects.filter(pk=request.POST.get("attempt") or 0, user=request.user, question=question).first()
    if attempt is None and not Attempt.objects.filter(user=request.user, question=question).exists():
        # Learners can only report questions they have actually been shown.
        return render(request, "questions/report_done.html", {"ok": False}, status=403)
    reason = request.POST.get("reason")
    if reason not in QuestionReport.Reason.values:
        reason = QuestionReport.Reason.OTHER
    file_report(request.user, question, reason, request.POST.get("comment", "").strip(), attempt)
    return render(request, "questions/report_done.html", {"ok": True})
