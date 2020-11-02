from django.contrib.auth.models import User
from django.db import models


class AccessAttemptFailure(models.Model):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='failed_attempts')
    datetime = models.DateTimeField(auto_now_add=True)
