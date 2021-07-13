import json
import random
import re
from argparse import ArgumentParser
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cached_property
from logging import getLogger
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

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

    for _, methods in swagger['paths'].items():
        for method, description in methods.items():
            if method != 'get':
                continue

            for parameter in description.get('parameters', []):
                if rnd.choice((True, False)):  # decide whether to permute this time or not
                    parameter['in'] = {
                        'query': 'header',
                        'header': 'query',
                    }.get(parameter['in'], parameter['in'])

                    if parameter['in'] == 'header':
                        parameter['name'] = humps.pascalize(parameter['name'])
                    elif parameter['in'] == 'query':
                        parameter['name'] = humps.decamelize(parameter['name'].replace('-', ''))


def permute_credentials(request: requests.Request, meta: dict):

    if 'App-Token' not in request.headers:
        raise ValueError('Missing app token')
    request.headers['App-Token'] = hubstaff.app_token

    if 'Auth-Token' not in request.headers:
        raise ValueError('Missing auth token')
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


def personal_filter_result_processor(result: Any, meta: dict) -> Any:

    email = meta['user']['email']

    def is_forbidden(value: Any) -> bool:
        if isinstance(value, str) and '@' in value and value != email:
            return True

        return False

    def fix_recursively(data: Any) -> Any:

        if isinstance(data, dict):
            if any(is_forbidden(value) for value in data.values()):
                return {}
            else:
                return {key: fix_recursively(value) for key, value in data.items()}

        if isinstance(data, list):
            return [fixed for value in data if (fixed := fix_recursively(value))]

        return data

    return fix_recursively(result)


@dataclass(frozen=True)
class Parameter:
    """
    A class representing 1 particular configuration unit from swagger file.

    Examples:
        (path='/v1/users/{id}', method='get', in_='header', name='App-Token')
        (path='/v1/users/{id}', method='get', in_='header', name='Auth-Token')
        (path='/v1/users/{id}', method='get', in_='query', name='id')
    """

    path: str
    method: Optional[str]
    in_: Optional[str]
    name: Optional[str]

    @cached_property
    def re_path(self) -> re.Pattern:
        return re.compile(re.sub(r'\{(.+?)\}', r'(?P<\1>.+)', self.path))  # /users/{id}/projects -> /users/.*?/projects

    def __eq__(self, other) -> bool:
        """
        Parameters are equal if their fields are equal. None value matches every other value (i.e. None == wildcard).
        These are equal:
            (path='/v1/users/{id}', method='get', in_='header', name='App-Token')
            (path='/v1/users/{id}', method='get', in_=None, name=None)
        """
        for field_ in self.__annotations__:
            # we do case-insensitive comparison bc case may change
            # (field name will be capitalized if transformed into header, and vice versa)
            val1 = getattr(self, field_)
            val2 = getattr(other, field_)

            if val1 is None or val2 is None:
                continue

            val1 = val1.lower()
            val2 = val2.lower()

            if val1 == val2:
                continue

            if field_ == 'path':
                if '{' in val1 and self.re_path.match(val2):
                    continue

                elif '{' in val2 and other.re_path.match(val1):
                    continue

            return False

        return True


@dataclass
class ApiMixer:
    """
    Given any dictionary in Swagger format, perform random substitutions of specific fields.

    "Randomness" is consistent across runs and depends only on seed.

    Permutation is achieved by following steps:
    1) Swagger file is parsed and converted into list of Parameters.
       Parameter is just a tuple of path, method, value location, and value itself:
       (path='/v1/users/{id}', method='get', in_='header', name='App-Token')
    2) A permuted list of parameters is generated by applying "permutations" - functions
       which take single Parameter and transform it to another Parameter.
    3) In the end we have list of original parameters and s probably convertedimilar list with those parameters
       after permutations, which is 1-to-1 mapping (mapping is done by indexes, i.e.
       parameters[i] ---> permuted_parameters[i]).
    4) Now, when a request comes to the application, it is parsed to a set of permuted
       parameters, those are converted back to original parameters using the mapping from step 3,
       and original parameters are used to construct "real" request to Hubstaff API.
    5) Response from Hubstaff API is returned back to user.
    """

    swagger: dict
    seed: int
    meta: dict = field(default_factory=dict)

    permutations: Iterable[callable] = (
        permute_paths,
        # permute_methods,
        permute_locations,
        permute_result,
    )

    request_processors: Iterable[callable] = (
        permute_credentials,
    )

    result_processors: Iterable[callable] = (
        personal_filter_result_processor,
        permute_result_processor,
    )

    def __post_init__(self):

        # apply permutations on swagger copy
        self.permuted_swagger = deepcopy(self.swagger)
        for permutation in self.permutations:
            permutation(self.permuted_swagger, self.seed, self.meta)

        # generate all parameters for original and permuted swagger definitions
        self.original_parameters = self.as_parameters(self.swagger)
        self.permuted_parameters = self.as_parameters(self.permuted_swagger)

    @classmethod
    def as_parameters(cls, swagger: dict) -> List[Parameter]:
        """
        Covert a hierarchical swagger definition to a list of parameters.

        Example output: [
            # parameters from /v1/users/{id} endpoint:
            Parameter(path='/v1/users/{id}', method='get', in_='header', name='App-Token')
            Parameter(path='/v1/users/{id}', method='get', in_='header', name='Auth-Token')
            Parameter(path='/v1/users/{id}', method='get', in_='query', name='id'),
            # parameters for other endpoints:
            ...
        ]
        """

        parameters = []

        for path, methods in swagger['paths'].items():
            for method, description in methods.items():

                if 'parameters' not in description:
                    # if no parameters for this endpoint, we just create a wildcard dummy parameter,
                    # so that we remember path and method
                    parameters.append(Parameter(path, method, None, None))
                else:
                    for parameter in description['parameters']:
                        parameters.append(Parameter(path, method, parameter['in'], parameter['name']))

        return parameters

    def reverse(self, permuted_parameter: Parameter) -> Tuple[Parameter, Parameter]:
        """
        Convert permuted parameter to permuted definition + original parameter.
        Raises ValueError if permuted parameter is not expected.
        """
        idx = self.permuted_parameters.index(permuted_parameter)
        return self.permuted_parameters[idx], self.original_parameters[idx]


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--swagger_file', type=Path, default='data/hubstaff.v1.swagger.json')
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()

    swagger_data = json.loads(args.swagger_file.read_text())
    mixer = ApiMixer(swagger_data, args.seed)

    print(json.dumps(mixer.permuted_swagger['paths'], indent=4, default=str))
