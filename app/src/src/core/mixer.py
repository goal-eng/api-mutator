import json
import random
import re
import threading
from argparse import ArgumentParser
from collections import namedtuple
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import List, Iterable, Optional

import xmltodict
from dict2xml import dict2xml

thread_lock = threading.Lock()
synonyms = json.loads((Path(__file__).parent / 'data' / 'synonyms.json').read_text())


Format = namedtuple('Format', field_names=['name', 'encode', 'decode'])
FORMATS = [
    Format(name='application/json', decode=json.loads, encode=json.dumps),
    # Format(name='application/x-yaml', decode=yaml.load, encode=yaml.dump),
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

    part_to_name = {}  # mapping from parts to dictionary words (common for all endpoints)

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
            else:  # otherwise just replace with part with dictionary word
                permuted_part = part_to_name.get(part)
                if not permuted_part:

                    for synonym in random.sample(synonyms[part], k=len(synonyms[part])):
                        if synonym not in part_to_name.values():
                            permuted_part = part_to_name.setdefault(part, synonym)
                            break
                    else:
                        raise ValueError(f'Out of synonyms for "{part}", current mapping: {part_to_name}')

            permuted_parts.append(permuted_part)

        return '/'.join(permuted_parts)

    swagger['paths'] = {permute_path(path): methods for path, methods in swagger['paths'].items()}


def permute_formats(swagger: dict, _):
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
    fmt = random.choice(FORMATS)

    for path, methods in swagger['paths'].items():
        for method, description in methods.items():
            description['produces'] = description['consumes'] = [fmt.name]


def permute_methods(swagger: dict, _):
    """
    Replaces methods of swagger paths with random ones and modifies locations of parameters according to the methods.
    """
    for path, methods in swagger['paths'].items():
        methods_pool = random.sample(METHODS, k=len(METHODS))
        swagger['paths'][path] = {
            methods_pool.pop(): description for _, description in methods.items()
        }

        # if we change GET to POST, then all parameters from "query" should go to "formData" etc
        for method, description in swagger['paths'][path].items():
            parameters = description['parameters']
            for parameter in parameters:
                if method == 'get' and parameter['in'] == ['formData', 'body']:
                    parameter['in'] = 'query'
                elif method in ['post', 'patch', 'put'] and parameter['in'] in ['query']:
                    parameter['in'] = 'body'


def permute_locations(swagger: dict, _):
    """
    Replaces locations of parameters (i.e. moves parameter from header to query string etc).
    """
    for _, methods in swagger['paths'].items():
        for method, description in methods.items():
            for parameter in description['parameters']:
                if random.choice((True, False)):  # decide whether to permute this time or not
                    parameter['in'] = {
                        'query': 'header',
                        'body': 'header',
                        'header': 'query' if method == 'get' else 'body',
                    }.get(parameter['in'], parameter['in'])


@dataclass(frozen=True)
class Parameter:
    path: str
    method: Optional[str]
    format: Optional[str]
    in_: Optional[str]
    name: Optional[str]

    def __eq__(self, other) -> bool:
        """ Parameters are equal if their fields are equal. None value matches every other value. """
        for field in self.__annotations__:
            val1 = getattr(self, field)
            val2 = getattr(other, field)

            if not (val1 == val2 or val1 is None or val2 is None):
                return False

        return True


class ApiMixer:
    """
    Given any dictionary in Swagger format, performs random substitutions of specific fields.
    "Randomness" is consistent across runs and depends only on seed.
    """

    def __init__(self, swagger: dict, seed: int, permutations: Iterable[callable] = (
            permute_paths, permute_formats, permute_methods, permute_locations)):
        self.swagger = swagger
        self.seed = seed

        # apply permutations on swagger copy
        self.permuted_swagger = deepcopy(self.swagger)
        with thread_lock:
            random.seed(self.seed)
            for permutation in permutations:
                permutation(self.permuted_swagger, self.seed)

        # generate mappings from old parameters to new ones
        self.original_parameters = self.as_parameters(self.swagger)
        self.permuted_parameters = self.as_parameters(self.permuted_swagger)

    @classmethod
    def as_parameters(cls, swagger: dict) -> List[Parameter]:
        """ TODO """
        parameters = []

        for path, methods in swagger['paths'].items():
            for method, description in methods.items():
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
