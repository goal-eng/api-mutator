from django.contrib.auth.models import User
from django.db import models
from functools import partial
import secrets
import string
from django.db.models.signals import post_save
from django.dispatch import receiver


PASSWORD_LENGTH = 8


def get_random_string(length: int) -> str:
    return ''.join(
        secrets.choice(string.ascii_uppercase + string.digits)
        for _ in range(length)
    )


def password_default() -> str:
    return get_random_string(PASSWORD_LENGTH)


class ApiCredentials(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='api_credentials')
    password = models.CharField(max_length=PASSWORD_LENGTH, default=password_default)
    app_token = models.CharField(max_length=255, default=partial(get_random_string, length=16))
    auth_token = models.CharField(max_length=255, default=partial(get_random_string, length=16))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.user.set_password(self.password)
        self.user.save()


@receiver(post_save, sender=User)
def generate_api_credentials(sender, instance, created, **kwargs):
    if created:
        ApiCredentials.objects.create(user=instance)


class AccessAttemptFailure(models.Model):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='failed_attempts')
    datetime = models.DateTimeField(auto_now_add=True)
