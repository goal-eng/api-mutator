import json
import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, Union

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import SuspiciousOperation, PermissionDenied
from django.http import JsonResponse, HttpResponse, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django.views.generic.base import View
from requests import RequestException
from .mixer import ApiMixer, Parameter, FORMATS
from .models import AccessAttemptFailure

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__file__)


# save ApiMixer instance in memory, so that we don't regenerate mappings on each request
@lru_cache(maxsize=32)
def get_mixer(seed: int) -> ApiMixer:
    swagger = Path(__file__).parent / 'data' / 'hubstaff.v1.swagger.json'
    return ApiMixer(json.loads(swagger.read_text()), seed)


class ApiDescriptionView(TemplateView):
    template_name = 'api.html'


class SwaggerView(View):
    def get(self, *args, **kwargs):
        mixer = get_mixer(seed=self.request.user.pk)
        swagger = mixer.permuted_swagger
        swagger['host'] = self.request.META['HTTP_HOST']
        return JsonResponse(swagger)


session = requests.Session()


def _request_to_params(request: HttpRequest) -> Dict[Parameter, Union[int, str]]:
    """ Converts user's request to dict {Parameter: value} """

    permuted_path = request.path
    permuted_method = request.method.lower()
    permuted_format = None if request.content_type == 'text/plain' else request.content_type
    permuted_parameters = {}

    for header, value in request.headers.items():
        permuted_parameters[Parameter(permuted_path, permuted_method, permuted_format, 'header', header.lower())] = value

    for post, value in request.POST.items():
        permuted_parameters[Parameter(permuted_path, permuted_method, permuted_format, 'formData', post)] = value

    for get, value in request.GET.items():
        permuted_parameters[Parameter(permuted_path, permuted_method, permuted_format, 'query', get)] = value

    if permuted_format:
        try:
            fmt = next(filter(lambda fmt: fmt.name == permuted_format, FORMATS))
        except StopIteration:
            raise ValueError(f'Unknown content-type: "{permuted_format}"')

        try:
            data = fmt.decode(request.body) if request.body else {}
        except Exception:
            raise ValueError(f'Could not decode "{fmt.name}": {request.body}')

        if not isinstance(data, dict):
            raise ValueError(f'Payload is not a dictionary: {request.body}')

        for key, value in data.items():
            permuted_parameters[Parameter(permuted_path, permuted_method, permuted_format, 'body', key)] = value

    return permuted_parameters


def _params_to_request(host: str, parameters: Dict[Parameter, Union[str, int]]) -> requests.Response:
    """ Uses the list of parameters to make a request to host and returns response """
    assert parameters, 'Missing parameters to form a request'
    assert len({(param.path, param.method, param.format) for param in parameters}) == 1, f'Inconsistent parameters {parameters}'

    first_param = next(iter(parameters.keys()))

    method = first_param.method
    path = first_param.path.format(
        **{param.name: value for param, value in parameters.items() if param.in_ == 'path'}
    )  # /v1/user/{id} -> /v1/user/1

    return getattr(session, method)(
        host + path,
        headers={param.name: value for param, value in parameters.items() if param.in_ == 'header'},
        json={param.name: value for param, value in parameters.items() if param.in_ == 'body'},
        params={param.name: value for param, value in parameters.items() if param.in_ == 'query'},
        timeout=(60, 60),
    )


@csrf_exempt
def proxy(request, user_pk: int):
    user_pk = int(user_pk)

    if AccessAttemptFailure.objects.filter(datetime__gte=now() - timedelta(hours=24)).count() >= 10:
        raise PermissionDenied(f'Proxy is currently unavailable, please try again later')

    user = get_object_or_404(User, pk=user_pk)
    if user.failed_attempts.filter(datetime__gte=now() - timedelta(hours=24)).count() > \
            settings.HUBSTAFF_MAX_FAILED_BEFORE_BLOCK:
        raise SuspiciousOperation(f'Too many attempts to access Hubstaff API with wrong credentials; '
                                  f'please wait 24h before further attempts')

    # convert user's request to list of parameters
    try:
        permuted_parameters = _request_to_params(request)
    except ValueError as exc:
        return JsonResponse(status=400, data={'error': str(exc)})

    # convert each parameter to original (non-mutated) one, or drop if parameter is redundant
    parameters = {}
    mixer = get_mixer(seed=user_pk)
    for permuted_parameter, value in permuted_parameters.items():
        try:
            parameters[mixer.reverse(permuted_parameter)] = value
        except ValueError:
            if permuted_parameter.in_ == 'header':
                continue  # we ignore redundant headers

            return JsonResponse(status=400, data={
                'error': f'Unexpected: {permuted_parameter.method.upper()} {permuted_parameter.path} '
                         f'{permuted_parameter.in_.upper()} {permuted_parameter.name}={value}'})

    log.info(f'IN: {permuted_parameters}')
    log.info(f'OUT: {parameters}')

    fmt = next(filter(lambda fmt: fmt.name == next(iter(permuted_parameters)).format, FORMATS))

    # make a request with original (pure) parameters
    try:
        response = _params_to_request(host='https://' + mixer.swagger['host'], parameters=parameters)
    except RequestException as exc:
        return HttpResponse(status=500, content=fmt.encode({'error': f'API error: {exc}'}), content_type=fmt.name)

    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    if response.status_code == 401:
        AccessAttemptFailure.objects.create(user=user)

    return HttpResponse(status=response.status_code, content=fmt.encode(payload), content_type=fmt.name)
