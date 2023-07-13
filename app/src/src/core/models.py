import secrets
import string
from datetime import timedelta
from functools import partial

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import now

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
    password = models.CharField(max_length=255, default=password_default)
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


class HubstaffAccessInfo(models.Model):
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_type = models.CharField(max_length=32)
    expires_in = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        assert self.token_type == 'bearer', f'Unexpected token type: {self.token_type}'
        if self.expires_in:
            self.expires_at = now() + timedelta(seconds=self.expires_in)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.token_type} {self.access_token} (expires at {self.expires_at})'


class SubmitTaskAttempt(models.Model):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='submit_attempts')
    datetime = models.DateTimeField(auto_now_add=True)
