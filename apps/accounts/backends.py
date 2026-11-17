from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None or password is None:
            return None
        ident = username.strip()
        lookup = {"email": ident.lower()} if "@" in ident else {"username__iexact": ident}
        user = User.objects.filter(**lookup).first()
        if user is None:
            User().set_password(password)  # equalise timing for unknown accounts
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
