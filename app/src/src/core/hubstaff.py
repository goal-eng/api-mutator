from dataclasses import dataclass
import requests
from typing import Optional
from functools import partialmethod
from django.conf import settings


@dataclass
class HubstaffV1:

    email: str
    password: str
    app_token: str
    auth_token: Optional[str] = None

    base_url = 'https://api.hubstaff.com/v1'

    def __post_init__(self):
        self.session = requests.Session()

        if not self.auth_token:
            response = self.session.post(self.base_url + '/auth', headers={
                'App-Token': self.app_token,
            }, data={
                'email': self.email,
                'password': self.password,
            })
            response.raise_for_status()
            self.auth_token = response.json()['user']['auth_token']

        self.session.headers = {
            'App-Token': self.app_token,
            'Auth-Token': self.auth_token,
        }

    def request(self, verb: str, path: str, *args, **kwargs) -> dict:
        response = self.session.request(verb, self.base_url + path, *args, **kwargs)
        response.raise_for_status()
        return response.json()

    get = partialmethod(request, 'get')
    post = partialmethod(request, 'post')


hubstaff = HubstaffV1(
    email=settings.HUBSTAFF_USERNAME,
    password=settings.HUBSTAFF_PASSWORD,
    app_token=settings.HUBSTAFF_APP_TOKEN,
    auth_token=settings.HUBSTAFF_AUTH_TOKEN,
)
