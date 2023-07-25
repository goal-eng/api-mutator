from __future__ import annotations

import datetime
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

    You also need to pass a project_key to manage issues when you create a JiraV3 object.
    """
    BASE_URL = settings.JIRA_API_URL
    AUTH_EMAIL = settings.JIRA_API_AUTH_EMAIL
    AUTH_TOKEN = settings.JIRA_API_AUTH_TOKEN

    def __init__(self, project_key):
        self.project_key = project_key
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

    def find_issue_by_custom_field(self, custom_fields: dict[int, str]) -> list[dict]:
        """

        :param custom_fields:
            custom field and value to search for - we assume all are text fields
        :return: matched issues
        """

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        custom_fields_jql = ' AND '.join([
            f'cf[{key}] ~ {text_exact_match(value)}'
            for key, value in custom_fields.items()
        ])

        params = {
            'jql': f'project = {self.project_key} AND {custom_fields_jql} ORDER BY createdDate DESC',
            'maxResults': 2,
        }
        return self.get(self.BASE_URL + '/search', headers=headers, params=params)['issues']

    def create_issue(self, summary: str, issue_type: str, custom_fields: dict[int, str]) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        payload = {
            "fields": {
                "project": {
                    "key": self.project_key
                },
                "summary": summary,
                "issuetype": {
                    "name": issue_type
                },
                **{
                    f'customfield_{key}': value
                    for key, value in custom_fields.items()
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
            headers=headers,
            files={'file': file}
        )


def text_exact_match(text: str) -> str:
    """
    Used to search for exact match of text in Jira with ~ operator
    """
    return _jql_escape_string(f'"{text}"')


def dt(jira_dt: str) -> datetime.datetime:
    """
    Jira datetime string to timezone aware datetime object
    """
    try:
        datetime_obj = datetime.datetime.strptime(jira_dt, '%Y-%m-%dT%H:%M:%S.%f%z')
    except ValueError:  # no microseconds
        datetime_obj = datetime.datetime.strptime(jira_dt, '%Y-%m-%dT%H:%M:%S%z')
    return datetime_obj


def _jql_escape_string(text: str) -> str:
    """

    https://jira.atlassian.com/browse/JRASERVER-27647
    """
    special_characters = '\'"\t\n\r\\ '
    text = text.translate(
        str.maketrans({
            c: fr'\{c}' for c in special_characters
        })
    )
    return f'"{text}"'
