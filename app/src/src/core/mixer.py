import json
import random
import re
from argparse import ArgumentParser
from collections import namedtuple
from copy import deepcopy
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Iterable, List, Optional

import xmltodict
import yaml
from dict2xml import dict2xml
from src.core.synonyms import SYNONYMS

log = getLogger(__name__)

Format = namedtuple('Format', field_names=['name', 'encode', 'decode'])
FORMATS = [
    Format(name='application/json', decode=json.loads, encode=json.dumps),
    Format(name='application/x-yaml', decode=yaml.load, encode=yaml.dump),
    Format(name='application/xml', decode=xmltodict.parse, encode=dict2xml),
]
METHODS = ['get', 'put', 'post', 'patch']
LOCATIONS = ['header', 'query', 'body']  # 'path', 'formData'


def permute_paths(swagger: dict, seed: int):
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


def permute_formats(swagger: dict, seed: int):
    """
    Replaces request & response formats for all endpoints.

    Example:
        "/v1/auth": {
            "post": {
                "produces": [
                    "application/json"
                ],
                "consumes": [
                    "application/x-www-form-urlencoded",
                    "application/json"
                ],

        --->

        "/v1/auth": {
            "post": {
                "produces": [
                    "application/xml"
                ],
                "consumes": [
                    "application/xml"
                ],
    """
    rnd = random.Random(seed)
    fmt = rnd.choice(FORMATS)

    for path, methods in swagger['paths'].items():
        for method, description in methods.items():
            description['produces'] = description['consumes'] = [fmt.name]


def permute_methods(swagger: dict, seed: int):
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


def permute_locations(swagger: dict, seed: int):
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
            for parameter in description.get('parameters', []):
                if rnd.choice((True, False)):  # decide whether to permute this time or not
                    parameter['in'] = {
                        'query': 'header',
                        'body': 'header',
                        'header': 'query' if method == 'get' else 'body',
                    }.get(parameter['in'], parameter['in'])


@dataclass(frozen=True)
class Parameter:
    """
    A class representing 1 particular configuration unit from swagger file.
    Examples:
        (path='/v1/users/{id}', method='get', format='json', in_='header', name='App-Token')
        (path='/v1/users/{id}', method='get', format='json', in_='header', name='Auth-Token')
        (path='/v1/users/{id}', method='get', format='json', in_='query', name='id')
    """
    path: str
    method: Optional[str]
    format: Optional[str]
    in_: Optional[str]
    name: Optional[str]

    def __eq__(self, other) -> bool:
        """
        Parameters are equal if their fields are equal. None value matches every other value (i.e. None == wildcard).
        These are equal:
            (path='/v1/users/{id}', method='get', format='json', in_='header', name='App-Token')
            (path='/v1/users/{id}', method='get', format='json', in_=None, name=None)
        """
        for field in self.__annotations__:
            # we do case-insensitive comparison bc case may change
            # (field name will be capitalized if transformed into header, and vice versa)
            val1 = getattr(self, field).lower()
            val2 = getattr(other, field).lower()

            if not (val1 == val2 or val1 is None or val2 is None):
                return False

        return True


class ApiMixer:
    """
    Given any dictionary in Swagger format, performs random substitutions of specific fields.
    "Randomness" is consistent across runs and depends only on seed.

    Permutation is achieved by following steps:
    1) Swagger file is parsed and converted into list of Parameters.
       Parameter is just a tuple of path, method, format, value location, and value itself:
       (path='/v1/users/{id}', method='get', format='json', in_='header', name='App-Token')
    2) A permuted list of parameters is generated by applying "permutations" - functions
       which take single Parameter and transform it to another Parameter.
    3) In the end we have list of original parameters and s probably convertedimilar list with those parameters
       after permutations, which is 1-to-1 mapping (mapping is done by indexes, i.e.
       parameters[i] <---> permuted_parameters[i]).
    4) Now, when a request comes to the application, it is parsed to a set of permuted
       parameters, those are converted back to original parameters using the mapping from step 3,
       and original parameters are used to construct "real" request to Hubstaff API.
    5) Response from Hubstaff API is returned back to user.
    """

    def __init__(
        self,
        swagger: dict,
        seed: int,
        permutations: Iterable[callable] = (
            permute_paths,
            permute_formats,
            permute_methods,
            permute_locations,
        )
    ):
        self.swagger = swagger
        self.seed = seed

        # apply permutations on swagger copy
        self.permuted_swagger = deepcopy(self.swagger)
        for permutation in permutations:
            permutation(self.permuted_swagger, self.seed)

        # generate all parameters for original and permuted swagger definitions
        self.original_parameters = self.as_parameters(self.swagger)
        self.permuted_parameters = self.as_parameters(self.permuted_swagger)

    @classmethod
    def as_parameters(cls, swagger: dict) -> List[Parameter]:
        """
        Coverts a hierarchical swagger definition to a list of parameters.
        Example output: [
            # parameters from /v1/users/{id} endpoint:
            Parameter(path='/v1/users/{id}', method='get', format='json', in_='header', name='App-Token')
            Parameter(path='/v1/users/{id}', method='get', format='json', in_='header', name='Auth-Token')
            Parameter(path='/v1/users/{id}', method='get', format='json', in_='query', name='id'),
            # parameters for other endpoints:
            ...
        ]
        """
        parameters = []

        for path, methods in swagger['paths'].items():
            for method, description in methods.items():

                if 'parameters' not in description:
                    # if no parameters for this endpoint, we just create a wildcard dummy parameter,
                    # so that we remember path, method and format
                    parameters.append(Parameter(path, method, description['produces'][0], None, None))
                else:
                    for parameter in description['parameters']:
                        parameters.append(Parameter(path, method, description['produces'][0], parameter['in'], parameter['name']))

        return parameters

    def reverse(self, permuted_parameter: Parameter) -> Parameter:
        """ Converts permuted parameter to original one. Raises ValueError if permuted parameter is not expected. """
        return self.original_parameters[self.permuted_parameters.index(permuted_parameter)]


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--swagger_file', type=Path, default='data/hubstaff.v1.swagger.json')
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()

    swagger_data = json.loads(args.swagger_file.read_text())
    mixer = ApiMixer(swagger_data, args.seed)

    print(json.dumps(mixer.permuted_swagger['paths'], indent=4, default=str))
