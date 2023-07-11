import json
import requests
from dataclasses import dataclass
from django.conf import settings
from functools import partialmethod
from logging import getLogger


log = getLogger(__name__)


@dataclass
class JiraV3:
    """
    Jira REST API Cient

    Jira REST API URL: https://<your-domain>.atlassian.net/rest/api/3

    We can use these 2 authentication methods provided by Jira for the REST API.
    - Non-Connect apps created in the developer console, see OAuth 2.0 (3LO) apps.
    - Simple scripts or to make REST API calls yourself, see Basic auth for REST APIs.
    Basic auth is not as secure as other methods, but the API token is long enough and more secure than normal password.
    So we can use Basic auth.

    To implement Basic auth, email and API token are needed.
    You can find more information on how to manage API tokens at:
        https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/
    Any member's credential can be used to create an issue and this member will be the reporter of those issues.

    Additionally, you also need a project key to create an issue.
    """
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
