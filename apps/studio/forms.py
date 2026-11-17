"""Studio forms. Question editing uses type-specific plain fields that are
converted to the validated payload structure — admins never edit raw JSON."""
from django import forms

from apps.curriculum.models import LearningObjective, Level, Topic, TopicPrerequisite, Unit
from apps.questions.models import ErrorTag, QType


class Styled:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", "textarea")
            elif isinstance(widget, forms.Select) and not isinstance(widget, forms.SelectMultiple):
                widget.attrs.setdefault("class", "select")
            elif isinstance(widget, (forms.TextInput, forms.NumberInput, forms.EmailInput)):
                widget.attrs.setdefault("class", "input")


class UnitForm(Styled, forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["level", "title", "description", "is_published"]
        labels = {"level": "Daraja", "title": "Nomi", "description": "Tavsif", "is_published": "E'lon qilingan"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class TopicForm(Styled, forms.ModelForm):
    objectives_text = forms.CharField(
        label="O'quv maqsadlari", required=False, widget=forms.Textarea(attrs={"rows": 4}), help_text="Har bir qatorda bitta maqsad."
    )
    hard_prerequisites = forms.ModelMultipleChoiceField(
        label="Majburiy asos mavzular", queryset=Topic.objects.none(), required=False, widget=forms.SelectMultiple(attrs={"class": "select", "size": 8})
    )
    soft_prerequisites = forms.ModelMultipleChoiceField(
        label="Tavsiya etilgan asos mavzular", queryset=Topic.objects.none(), required=False, widget=forms.SelectMultiple(attrs={"class": "select", "size": 8})
    )

    class Meta:
        model = Topic
        fields = ["unit", "title", "title_en", "slug", "summary", "difficulty_offset", "mastery_threshold", "related"]
        labels = {
            "unit": "Bo'lim",
            "title": "Nomi (o'zbekcha)",
            "title_en": "Grammatik atama (inglizcha)",
            "slug": "URL nomi",
            "summary": "Qisqa tavsif",
            "difficulty_offset": "Daraja ichidagi qiyinlik (0–1)",
            "mastery_threshold": "O'zlashtirish chegarasi (0.5–0.98)",
            "related": "Bog'liq mavzular",
        }
        widgets = {"summary": forms.Textarea(attrs={"rows": 3}), "related": forms.SelectMultiple(attrs={"class": "select", "size": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        others = Topic.objects.select_related("unit__level").order_by("unit__level__order", "unit__order", "order")
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
            links = TopicPrerequisite.objects.filter(topic=self.instance)
            self.initial["hard_prerequisites"] = [l.prerequisite_id for l in links if l.strength == "hard"]
            self.initial["soft_prerequisites"] = [l.prerequisite_id for l in links if l.strength == "soft"]
            self.initial["objectives_text"] = "\n".join(o.text for o in self.instance.objectives.all())
        for name in ("hard_prerequisites", "soft_prerequisites", "related"):
            self.fields[name].queryset = others
            self.fields[name].label_from_instance = lambda t: f"{t.unit.level.code} · {t.title}"

    def clean(self):
        data = super().clean()
        if set(data.get("hard_prerequisites") or []) & set(data.get("soft_prerequisites") or []):
            raise forms.ValidationError("Bitta mavzu ham majburiy, ham tavsiya etilgan bo'la olmaydi.")
        return data

    def objectives(self):
        return [line.strip()[:240] for line in self.cleaned_data.get("objectives_text", "").splitlines() if line.strip()]


ERROR_CHOICES = ErrorTag.choices


class QuestionForm(Styled, forms.Form):
    topic = forms.ModelChoiceField(label="Mavzu", queryset=Topic.objects.select_related("unit__level").order_by("unit__level__order", "unit__order", "order"))
    type = forms.ChoiceField(label="Turi", choices=QType.choices)
    difficulty = forms.TypedChoiceField(label="Qiyinlik", coerce=int, choices=[(i, str(i)) for i in range(1, 6)], initial=3)
    error_tag = forms.ChoiceField(label="Asosiy xato toifasi", choices=ERROR_CHOICES, initial=ErrorTag.STRUCTURE)
    instruction = forms.CharField(label="Ko'rsatma (o'zbekcha)", required=False, max_length=240)
    prompt = forms.CharField(label="Savol matni", widget=forms.Textarea(attrs={"rows": 3}))
    explanation = forms.CharField(label="Izoh (o'zbekcha)", widget=forms.Textarea(attrs={"rows": 3}))
    # type-specific
    options = forms.CharField(label="Variantlar", required=False, widget=forms.Textarea(attrs={"rows": 4}), help_text="Har qatorda bitta variant. Noto'g'ri variantga xato toifasini qo'shish: `matn || tense`.")
    correct = forms.IntegerField(label="To'g'ri variant raqami (1 dan)", required=False, min_value=1, max_value=5)
    blanks = forms.CharField(label="Bo'sh joy javoblari", required=False, widget=forms.Textarea(attrs={"rows": 3}), help_text="Har bir bo'sh joy uchun bitta qator; muqobil javoblar `|` bilan.")
    context = forms.CharField(label="Kontekst matni", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    hint = forms.CharField(label="Yordam (ixtiyoriy)", required=False, max_length=120)
    answers = forms.CharField(label="To'g'ri javoblar", required=False, widget=forms.Textarea(attrs={"rows": 3}), help_text="Har qatorda bitta qabul qilinadigan javob.")
    tokens = forms.CharField(label="So'z bo'laklari", required=False, help_text="Bo'sh joy bilan ajrating. Ko'p so'zli bo'lak uchun `_`: `have_been`.")
    sample = forms.CharField(label="Namuna javob", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    criteria = forms.CharField(label="Baholash mezoni (inglizcha)", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    required_patterns = forms.CharField(label="Majburiy qoliplar (regex, ixtiyoriy)", required=False, widget=forms.Textarea(attrs={"rows": 2}), help_text="Har qatorda bitta regex.")
    pattern_hint = forms.CharField(label="Qolip mos kelmasa ko'rsatiladigan izoh", required=False, max_length=200)
    min_words = forms.IntegerField(label="Minimal so'zlar soni", required=False, min_value=2, max_value=40, initial=4)

    @staticmethod
    def _lines(text):
        return [line.strip() for line in (text or "").splitlines() if line.strip()]

    def to_fields(self):
        d = self.cleaned_data
        qtype = d["type"]
        if qtype == QType.MCQ:
            options = []
            for line in self._lines(d["options"]):
                text, _, error = line.partition("||")
                option = {"text": text.strip()}
                if error.strip() in ErrorTag.values:
                    option["error"] = error.strip()
                options.append(option)
            payload = {"options": options, "correct": (d["correct"] or 0) - 1}
        elif qtype in (QType.GAP, QType.CONTEXT):
            payload = {"blanks": [[a.strip() for a in line.split("|") if a.strip()] for line in self._lines(d["blanks"])]}
            if d["hint"]:
                payload["hint"] = d["hint"]
            if qtype == QType.CONTEXT:
                payload["context"] = d["context"]
        elif qtype == QType.CONSTRUCT:
            payload = {"tokens": [t.replace("_", " ") for t in (d["tokens"] or "").split()], "answers": self._lines(d["answers"])}
        elif qtype == QType.FREE:
            payload = {
                "sample": d["sample"],
                "criteria": d["criteria"],
                "required_patterns": self._lines(d["required_patterns"]),
                "min_words": d["min_words"] or 4,
            }
            if d["pattern_hint"]:
                payload["pattern_hint"] = d["pattern_hint"]
        else:
            payload = {"answers": self._lines(d["answers"])}
        return {
            "type": qtype,
            "difficulty": d["difficulty"],
            "error_tag": d["error_tag"],
            "instruction": d["instruction"],
            "prompt": d["prompt"],
            "explanation": d["explanation"],
            "payload": payload,
        }

    @classmethod
    def initial_for(cls, question):
        p = question.payload
        initial = {
            "topic": question.topic_id,
            "type": question.type,
            "difficulty": question.difficulty,
            "error_tag": question.error_tag,
            "instruction": question.instruction,
            "prompt": question.prompt,
            "explanation": question.explanation,
        }
        if question.type == QType.MCQ:
            initial["options"] = "\n".join(o["text"] + (f" || {o['error']}" if o.get("error") else "") for o in p.get("options", []))
            initial["correct"] = p.get("correct", 0) + 1
        elif question.type in (QType.GAP, QType.CONTEXT):
            initial["blanks"] = "\n".join(" | ".join(b) for b in p.get("blanks", []))
            initial["context"] = p.get("context", "")
            initial["hint"] = p.get("hint", "")
        elif question.type == QType.CONSTRUCT:
            initial["tokens"] = " ".join(t.replace(" ", "_") for t in p.get("tokens", []))
            initial["answers"] = "\n".join(p.get("answers", []))
        elif question.type == QType.FREE:
            initial.update(
                sample=p.get("sample", ""),
                criteria=p.get("criteria", ""),
                required_patterns="\n".join(p.get("required_patterns", [])),
                pattern_hint=p.get("pattern_hint", ""),
                min_words=p.get("min_words", 4),
            )
        else:
            initial["answers"] = "\n".join(p.get("answers", []))
        return initial


class GenerateForm(Styled, forms.Form):
    topic = forms.ModelChoiceField(label="Mavzu", queryset=Topic.objects.select_related("unit__level").order_by("unit__level__order", "unit__order", "order"))
    type = forms.ChoiceField(label="Savol turi", choices=QType.choices)
    count = forms.TypedChoiceField(label="Soni", coerce=int, choices=[(n, str(n)) for n in (3, 5, 8, 10)], initial=5)
    difficulty = forms.TypedChoiceField(label="Qiyinlik", coerce=int, choices=[(i, str(i)) for i in range(1, 6)], initial=3)


class LevelForm(Styled, forms.ModelForm):
    class Meta:
        model = Level
        fields = ["title", "description"]


__all__ = ["UnitForm", "TopicForm", "QuestionForm", "GenerateForm", "LearningObjective"]
