import random
import re
from logging import getLogger
from typing import Any

import humps
import requests
from src.core.hubstaff import hubstaff
from src.core.synonyms import SYNONYMS

log = getLogger(__name__)

METHODS = ['get', 'put', 'post', 'patch']
LOCATIONS = ['header', 'query', 'body']  # 'path', 'formData'


def permute_paths(swagger: dict, seed: int, meta: dict):
    """
    Replaces parts of swagger paths with dictionary words.

    Example:
        /v1/users/{id}/projects -> /v231/persons/{id}/tasks
    """

    rnd = random.Random(seed)
    part_to_name = {}  # mapping from parts to dictionary words (common for all endpoints)

    # permute synonyms
    synonyms = {key: rnd.sample(values + [key], k=len(values) + 1) for key, values in SYNONYMS.items()}

    def permute_path(path: str) -> str:
        parts = path.split('/')
        permuted_parts = []
        for part in parts:
            if not part:  # don't modify empty part (appears before first / after last slash)
                permuted_part = part
            elif re.match(r'v\d+', part):  # replace version with seed-specific version
                permuted_part = f'v{seed}'
            elif part.startswith('{') and part.endswith('}'):  # don't touch parametrized parts
                permuted_part = part
            else:  # otherwise just replace this part with dictionary word
                permuted_part = part_to_name.get(part)
                if not permuted_part:
                    if part not in synonyms:
                        log.warning(f'No synonyms defined for "{part}"')
                        synonyms_for_part = [part]
                    else:
                        synonyms_for_part = synonyms[part]

                    for synonym in synonyms_for_part:
                        if synonym not in part_to_name.values():
                            permuted_part = part_to_name.setdefault(part, synonym)
                            break
                    else:
                        raise ValueError(f'Out of synonyms for "{part}", current mapping: {part_to_name}')

            permuted_parts.append(permuted_part)

        return '/'.join(permuted_parts)

    swagger['paths'] = {permute_path(path): methods for path, methods in swagger['paths'].items()}


def permute_methods(swagger: dict, seed: int, meta: dict):
    """
    Replaces methods of swagger paths with random ones and modifies locations of parameters according to the methods.

    Example:
        "/v1/users": {
            "get": {  # <---- !!!
                "parameters": [
                        {
                            "in": "query",  # <---- !!!
                            "name": "organization_memberships",
                            "description": "Include the organization memberships for each user",
                            "type": "boolean",
                            "required": false
                        },

        --->

        "/v1/users": {
            "post": {  # <---- !!!
                "parameters": [
                        {
                            "in": "body",  # <---- !!!
                            "name": "organization_memberships",
                            "description": "Include the organization memberships for each user",
                            "type": "boolean",
                            "required": false
                        },

    """
    rnd = random.Random(seed)

    for path, methods in swagger['paths'].items():
        methods_pool = rnd.sample(METHODS, k=len(METHODS))
        swagger['paths'][path] = {
            methods_pool.pop(): description for _, description in methods.items()
        }

        # if we change GET to POST, then all parameters from "query" should go to "body" etc
        for method, description in swagger['paths'][path].items():
            for parameter in description.get('parameters', []):
                if method == 'get' and parameter['in'] != 'header':
                    parameter['in'] = 'query'
                elif method in ['post', 'patch', 'put'] and parameter['in'] != 'header':
                    parameter['in'] = 'body'


def permute_locations(swagger: dict, seed: int, meta: dict):
    """
    Replaces locations of parameters (i.e. moves parameter from header to query string etc).

    Example:
        "parameters": [
            {
                "in": "query",  # <---- !!!
                "name": "organization_memberships",
                "description": "Include the organization memberships for each user",
                "type": "boolean",
                "required": false
            },

        --->

        "parameters": [
            {
                "in": "header",  # <---- !!!
                "name": "organization_memberships",
                "description": "Include the organization memberships for each user",
                "type": "boolean",
                "required": false
            },
    """
    rnd = random.Random(seed)
    params_locations = {}  # persistence: same param is always located in the same place

    for _, methods in swagger['paths'].items():
        for method, description in methods.items():
            if method != 'get':
                continue

            for parameter in description.get('parameters', []):
                in_ = params_locations.get(parameter['name'])
                if not in_:
                    if rnd.choice((True, False)):  # decide whether to permute this time or not
                        in_ = {
                            'query': 'header',
                            'header': 'query',
                        }.get(parameter['in'], parameter['in'])
                    else:
                        in_ = parameter['in']
                    params_locations[parameter['name']] = in_

                parameter['in'] = in_
                if in_ == 'header':
                    parameter['name'] = humps.pascalize(parameter['name'])
                elif in_ == 'query':
                    parameter['name'] = humps.decamelize(parameter['name'].replace('-', ''))



def permute_credentials(request: requests.Request, meta: dict):

    app_token = request.headers.get('App-Token')
    if not app_token:
        raise ValueError('Missing app token')

    if app_token != meta['user'].api_credentials.app_token:
        raise ValueError('Wrong app token')

    request.headers['App-Token'] = hubstaff.app_token

    auth_token = request.headers.get('Auth-Token')
    if not auth_token:
        raise ValueError('Missing auth token')

    if auth_token != meta['user'].api_credentials.auth_token:
        raise ValueError('Wrong auth token')

    request.headers['Auth-Token'] = hubstaff.auth_token


def permute_result(swagger: dict, seed: int, meta: dict):
    """
    Replaces result object with a list. Horrible.

    Example:
        "definitions": {
            "user_with_auth_token": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "format": "int32",
                        "description": "User ID"
                    },
                    "name": {
                        "type": "string",
                        "description": "User name"
                    },
                    "last_activity": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Last activity of user"
                    },
                    "auth_token": {
                        "type": "string",
                        "description": "Auth token"
                    }
                },
                "description": "Obtain auth token for a user"
            },

        --->

        "definitions": {
            "user_with_auth_token": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "integer",
                            "format": "int32",
                            "description": "User ID"
                        },
                        "name": {
                            "type": "string",
                            "description": "User name"
                        },
                        "last_activity": {
                            "type": "string",
                            "format": "date-time",
                            "description": "Last activity of user"
                        },
                        "auth_token": {
                            "type": "string",
                            "description": "Auth token"
                        }
                    },
                    "description": "Obtain auth token for a user"
                }
            }

    """

    names = swagger['definitions'].keys()

    for name in names:
        definition = swagger['definitions'][name]
        swagger['definitions'][name] = {
            'type': 'object',
            'properties': {
                'result': definition,
            },
        }


def permute_result_processor(result: Any, meta: dict) -> Any:
    return {'result': result}


def personal_filter_result_processor(data: Any, meta: dict) -> Any:

    assert isinstance(data, dict)

    # allowed values:
    email = meta['user'].email
    user_id = meta['user_data']['id']
    organizations_names = {org['name'] for org in meta['user_data']['organizations']}
    projects_names = {project['name'] for project in meta['user_data']['projects']}
    projects_ids = {project['id'] for project in meta['user_data']['projects']}

    result = {}
    for key, content in data.items():

        if not isinstance(content, list) or not content:
            result[key] = content
            continue

        if 'email' in content[0]:
            result[key] = [item for item in content if item['email'] == email]

        elif 'user' in content[0] and 'email' in content[0]['user']:
            result[key] = [item for item in content if item['user']['email'] == email]

        elif key == 'organizations':
            result[key] = [item for item in content if item['name'] in organizations_names]

        elif key == 'projects':
            result[key] = [item for item in content if item['name'] in projects_names]

        elif 'user_id' in content[0]:
            result[key] = [item for item in content if item['user_id'] == user_id]

        elif 'project_id' in content[0]:
            result[key] = [item for item in content if item['project_id'] in projects_ids]

        else:
            log.debug(f'No filters applied for "{key}": {content}')
            result[key] = content

    return result
