import json
import requests
from dataclasses import dataclass
from django.conf import settings
from functools import partialmethod
from logging import getLogger


log = getLogger(__name__)


@dataclass
class JiraV3:
    BASE_URL = settings.JIRA_API_URL
    AUTH_EMAIL = settings.JIRA_API_AUTH_EMAIL
    AUTH_TOKEN = settings.JIRA_API_AUTH_TOKEN

    def __init__(self):
        self.session = requests.Session()

    def request(self, verb: str, path: str, *args, **kwargs) -> dict:
        url = (self.BASE_URL + path) if path.startswith('/') else path
        response = self.session.request(
            verb, url, *args, **kwargs,
            auth=requests.auth.HTTPBasicAuth(self.AUTH_EMAIL, self.AUTH_TOKEN),
        )
        if not response.ok:
            log.error(response.text)
            response.raise_for_status()
        return response.json()

    get = partialmethod(request, 'get')
    post = partialmethod(request, 'post')

    def create_issue(self, project_key: str, summary: str, issue_type: str) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        payload = {
            "fields": {
                "project": {
                    "key": project_key
                },
                "summary": summary,
                "issuetype": {
                    "name": issue_type
                }
            }
        }
        return self.post(self.BASE_URL + '/issue', headers=headers, data=json.dumps(payload))
    
    def add_issue_attachment(self, issue_id: int, file) -> dict:
        headers = {
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check"
        }
        return self.post(
            self.BASE_URL + f'/issue/{issue_id}/attachments',
            headers = headers,
            files = { 'file': file }
        )
