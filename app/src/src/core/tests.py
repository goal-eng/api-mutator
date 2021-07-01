import json
from typing import List

import pytest
import requests
from django.conf import settings
from src.core.mixer import ApiMixer
from src.core.views import _params_to_request


@pytest.fixture
def swagger() -> dict:
    return json.loads(settings.SWAGGER_FILE_PATH.read_text())


@pytest.fixture
def mixers(swagger) -> List[ApiMixer]:
    return [
        ApiMixer(swagger=swagger, seed=15),
        ApiMixer(swagger=swagger, seed=16),
    ]


def test_diff(mixers):

    assert mixers[0].original_parameters == mixers[1].original_parameters
    assert mixers[0].permuted_parameters != mixers[0].original_parameters
    assert mixers[0].permuted_parameters[0] != mixers[1].permuted_parameters[0]


def test_forward(mixers):

    mixer = mixers[0]

    sample_param = mixer.permuted_parameters[0]
    permuted_params = [
        param for param in mixer.permuted_parameters
        if (param.path, param.method) == (sample_param.path, sample_param.method)
    ]

    request = _params_to_request(host='http://localhost', parameters={param: 1 for param in permuted_params})
    assert isinstance(request, requests.Request)

    # restored_params = _request_to_params(request)
    # assert restored_params == permuted_params


def test_credentials_work():
    response = requests.get('https://api.hubstaff.com/v1/users', headers={
        'App-Token': settings.HUBSTAFF_APP_TOKEN,
        'Auth-Token': settings.HUBSTAFF_AUTH_TOKEN,
    })
    assert response.ok, f'Bad response: {response.status_code} {response.text}'
