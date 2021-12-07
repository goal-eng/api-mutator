from dataclasses import dataclass
from functools import partialmethod
from logging import getLogger
from typing import Iterator, Optional

import requests
from django.utils.timezone import now
from src.core.models import HubstaffAccessInfo

log = getLogger(__name__)


class TokenAuth(requests.auth.AuthBase):
    def __init__(self, token):
        self.token = token

    def __call__(self, request):
        request.headers['Authorization'] = f'Bearer {self.token}'
        return request


class HubstaffV2Error(Exception):
    pass


class TooManyPagesError(HubstaffV2Error):
    pass


@dataclass
class HubstaffV2:
    refresh_token: str

    OPENID_CONFIG_URL = 'https://account.hubstaff.com/.well-known/openid-configuration'
    BASE_URL = 'https://api.hubstaff.com'
    TIMEOUT = 10
    PAGES_LIMIT = 10
    _access_info = None

    def __post_init__(self):
        self.session = requests.Session()

    @property
    def token_endpoint(self) -> str:
        response = self.session.get(self.OPENID_CONFIG_URL, timeout=self.TIMEOUT)
        if not response.ok:
            log.error(response.text)
            response.raise_for_status()
        return response.json()['token_endpoint']

    @property
    def access_info(self) -> HubstaffAccessInfo():
        if not self._access_info:
            self._access_info = HubstaffAccessInfo.objects.order_by('id').last()

        is_expired = bool(self._access_info and now() >= self._access_info.expires_at)
        if is_expired:
            response = self.session.post(self.token_endpoint, data={
                'grant_type': 'refresh_token',
                'refresh_token': self._access_info.refresh_token,
            }, timeout=self.TIMEOUT)
            if not response.ok:
                log.error(f'Tried to refresh access using last credentials and failed: {response.text}')
            else:
                self._access_info = HubstaffAccessInfo.objects.create(**response.json())
                log.info(f'Created new internal access info: {self._access_info}')

        if not self._access_info:
            response = self.session.post(self.token_endpoint, data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
            }, timeout=self.TIMEOUT)
            if not response.ok:
                log.error(f'Tried to refresh access using base refresh token and failed: {response.text}')
                response.raise_for_status()

            self._access_info = HubstaffAccessInfo.objects.create(**response.json())
            log.info(f'Created new internal access info: {self._access_info}')

        return self._access_info

    def request(self, verb: str, path: str, *args, **kwargs) -> dict:
        url = (self.BASE_URL + path) if path.startswith('/') else path
        response = self.session.request(
            verb, url, *args, **{'timeout': self.TIMEOUT, **kwargs},
            auth=TokenAuth(self.access_info.access_token),
        )
        if not response.ok:
            log.error(response.text)
            response.raise_for_status()
        return response.json()

    def send(self, request) -> requests.Response:
        request.auth = TokenAuth(self.access_info.access_token)
        prepared_request = self.session.prepare_request(request)
        response = self.session.send(prepared_request, timeout=self.TIMEOUT)
        return response

    get = partialmethod(request, 'get')
    post = partialmethod(request, 'post')

    def iter(self, endpoint: str, results_field: str, params: Optional[dict] = None) -> Iterator[dict]:
        params = params or {}
        page_start_id = None
        for _ in range(self.PAGES_LIMIT):
            result = self.get(endpoint, params=({**params, 'page_start_id': page_start_id} if page_start_id else params))
            if not (payload := result[results_field]):
                return
            yield from payload
            page_start_id = result.get('pagination', {}).get('next_page_start_id')
            if not page_start_id:
                return
        else:
            raise TooManyPagesError(f'Iterating through more than {self.PAGES_LIMIT} pages in {endpoint=}')

    iter_organizations = partialmethod(iter, endpoint='/v2/organizations', results_field='organizations')

    def iter_organization_users(self, organization_id: int) -> Iterator[dict]:
        yield from self.iter(f'/v2/organizations/{organization_id}/members', results_field='users', params={'include': 'users'})

    def iter_organization_projects(self, organization_id: int) -> Iterator[dict]:
        yield from self.iter(f'/v2/organizations/{organization_id}/projects', results_field='projects')
