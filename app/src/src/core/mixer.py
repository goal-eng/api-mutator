import json
import logging
import random
import string
import threading
from argparse import ArgumentParser
from collections import namedtuple
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from pprint import pprint
from typing import Tuple

import xmltodict
import yaml
from dict2xml import dict2xml

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


thread_lock = threading.Lock()


def randstr():
    return ''.join(random.choices(string.ascii_lowercase, k=random.choice(range(1, 10))))


class ApiMixer:
    """
    Given any dictionary in Swagger format, performs random substitutions of specific fields.
    "Randomness" is consistent across runs and depends only on seed.
    """

    Format = namedtuple('Format', field_names=['name', 'encode', 'decode'])
    FORMATS = [
        Format(name='application/json', decode=json.loads, encode=json.dumps),
        Format(name='application/x-yaml', decode=yaml.load, encode=yaml.dump),
        Format(name='application/xml', decode=xmltodict.parse, encode=dict2xml),
    ]

    METHODS = ['get', 'put', 'post', 'patch']
    LOCATIONS = ['header', 'query', 'body']  # 'path', 'formData'

    def __init__(self, swagger: dict, seed: int):
        self.swagger = swagger
        self.seed = seed

        self.path_mapping, self.method_mapping, self.format_mapping, self.parameter_mapping = self.generate_mappings()

    def generate_mappings(self) -> Tuple[dict, dict, dict, dict]:
        """ Converts original definitions to permuted ones and returns permuted->original mappings """
        path_mapping = {}  # path -> permuted path
        method_mapping = {}  # (path, method) -> (permuted path, permuted method)
        format_mapping = {}  # (path, method, format) -> (permuted path, permuted method, permuted format)
        parameter_mapping = {}  # (path, method, in, name) -> (permuted path, permuted method, permuted in, permuted name)

        with thread_lock:
            random.seed(self.seed)

            for path, methods in self.swagger['paths'].items():
                # permute path
                permuted_path = f'/v{self.seed}'
                for _ in range(random.choice(range(1, 3))):
                    permuted_path += f'/{randstr()}'
                path_mapping[path] = permuted_path

                available_methods = random.sample(self.METHODS, len(self.METHODS))  # bc we cannot use same method twice
                for method, description in methods.items():
                    # permute method
                    permuted_method = available_methods.pop()
                    method_mapping[(path, method)] = (permuted_path, permuted_method)

                    # permute in & out format
                    format_mapping[(path, method, self.FORMATS[0])] = \
                        (permuted_path, permuted_method, random.choice(self.FORMATS))

                    # permute parameters' name and kind
                    for i, parameter in enumerate(description['parameters']):
                        parameter_mapping[(path, method, parameter['in'], parameter['name'])] = \
                            (permuted_path, permuted_method, random.choice(self.LOCATIONS), randstr())

        return path_mapping, method_mapping, format_mapping, parameter_mapping

    @property
    @lru_cache
    def permuted_swagger(self) -> dict:
        perm_swagger = deepcopy(self.swagger)

        # replace parameters
        for path, methods in perm_swagger['paths'].items():
            for method, method_desc in methods.items():
                method_desc['parameters'] = [{
                    **p,
                    'in': self.parameter_mapping[(path, method, p['in'], p['name'])][2],
                    'name': self.parameter_mapping[(path, method, p['in'], p['name'])][3],
                } for p in method_desc['parameters']]

        # replace formats
        for (path, method, format), (perm_path, perm_method, perm_format) in self.format_mapping.items():
            perm_swagger['paths'][path][method]['produces'] = [perm_format.name]
            perm_swagger['paths'][path][method]['consumes'] = [perm_format.name]

        # replace methods
        for path, methods in perm_swagger['paths'].items():
            perm_swagger['paths'][path] = {
                self.method_mapping[(path, method)][1]: method_desc
                for method, method_desc in perm_swagger['paths'][path].items()
            }

        # replace paths
        perm_swagger['paths'] = {
            self.path_mapping[path]: methods
            for path, methods in perm_swagger['paths'].items()
        }

        return perm_swagger


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--swagger_file', type=Path, default='data/hubstaff.v1.swagger.json')
    parser.add_argument('--seed', type=int, default=1)
    args = parser.parse_args()

    swagger_data = json.loads(args.swagger_file.read_text())
    mixer = ApiMixer(swagger_data, args.seed)

    print('---- Paths ----')
    pprint(mixer.path_mapping)

    print('---- Swagger ----')
    print(json.dumps(mixer.permuted_swagger['paths'], indent=4, default=str))
