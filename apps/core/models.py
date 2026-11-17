from django.core.cache import cache
from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# Essential platform settings only: key -> (default, Uzbek label, help text).
SETTING_DEFINITIONS = {
    "registration_open": (True, "Ro'yxatdan o'tish ochiq", "O'chirilsa, yangi foydalanuvchilar ro'yxatdan o'ta olmaydi."),
    "require_email_verification": (False, "Email tasdiqlash majburiy", "Yoqilsa, email tasdiqlanmaguncha o'qishga ruxsat berilmaydi."),
    "ai_answer_evaluation": (True, "AI javob baholashi", "Erkin javoblarni va muqobil javoblarni AI yordamida baholash."),
    "ai_dynamic_generation": (True, "AI shaxsiy savollar", "Savollar tugaganda o'quvchi xatolariga mos savollar yaratish."),
    "ai_auto_approve": (False, "AI savollarni avtomatik tasdiqlash", "Barcha tekshiruvlardan o'tgan AI savollari admin tasdig'isiz e'lon qilinadi."),
}


class PlatformSetting(models.Model):
    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

    @staticmethod
    def get(key):
        cached = cache.get(f"setting:{key}")
        if cached is not None:
            return cached["v"]
        row = PlatformSetting.objects.filter(key=key).first()
        value = row.value if row else SETTING_DEFINITIONS[key][0]
        cache.set(f"setting:{key}", {"v": value}, 300)
        return value

    @staticmethod
    def put(key, value):
        if key not in SETTING_DEFINITIONS:
            raise KeyError(key)
        PlatformSetting.objects.update_or_create(key=key, defaults={"value": value})
        cache.delete(f"setting:{key}")
