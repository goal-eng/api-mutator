import json
import logging
from datetime import timedelta
from functools import lru_cache
from pprint import pformat
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
from .mixer import ApiMixer, Parameter
from .models import AccessAttemptFailure
from .hubstaff import hubstaff

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__file__)


# save ApiMixer instance in memory, so that we don't regenerate mappings on each request
@lru_cache(maxsize=32)
def get_mixer(seed: int, email: str) -> ApiMixer:

    # get user ID and project ID
    offset = 0
    while True:
        users = hubstaff.get('/users', params={
            'organization_memberships': True,
            'project_memberships': True,
            'offset': offset,
        })['users']

        try:
            user = next(user for user in users if user['email'] == email)
            break
        except StopIteration:
            offset += len(users)

        if not users:
            raise ValueError(f'User with email {email} not found in Hubstaff API /users response: {users}')

    return ApiMixer(json.loads(settings.SWAGGER_FILE_PATH.read_text()), seed, meta={'user': user})


class ApiDescriptionView(TemplateView):
    template_name = 'api.html'


class SwaggerView(View):
    def get(self, *args, **kwargs):
        mixer = get_mixer(seed=self.request.user.pk, email=self.request.user.email)
        swagger = mixer.permuted_swagger
        swagger['host'] = self.request.META['HTTP_HOST']
        return JsonResponse(swagger)


session = requests.Session()


def _request_to_params(request: HttpRequest) -> Dict[Parameter, Union[int, str]]:
    """ Convert user's request to dict {Parameter: value}. """

    permuted_path = request.path
    permuted_method = request.method.lower()
    permuted_parameters = {}

    # path is a parameter as well
    permuted_parameters[Parameter(permuted_path, permuted_method, 'path', None)] = None

    for header, value in request.headers.items():
        permuted_parameters[Parameter(permuted_path, permuted_method, 'header', header)] = value

    for post, value in request.POST.items():
        permuted_parameters[Parameter(permuted_path, permuted_method, 'formData', post)] = value

    for get, value in request.GET.items():
        permuted_parameters[Parameter(permuted_path, permuted_method, 'query', get)] = value

    return permuted_parameters


def _params_to_request(host: str, parameters: Dict[Parameter, Union[str, int]]) -> requests.Request:
    """ Uses the list of parameters to make a request to host and returns response """

    if not parameters:
        raise ValueError('No payload provided (no headers or parameters)')

    assert len({(param.path, param.method) for param in parameters}) == 1, f'Inconsistent parameters {parameters}'

    first_param = next(iter(parameters.keys()))

    path = first_param.path.format(
        **{param.name: value for param, value in parameters.items() if param.in_ == 'path'}
    )  # /v1/user/{id} -> /v1/user/1

    return requests.Request(
        first_param.method,
        host + path,
        headers={param.name: value for param, value in parameters.items() if param.in_ == 'header'},
        json={param.name: value for param, value in parameters.items() if param.in_ == 'body'},
        params={param.name: value for param, value in parameters.items() if param.in_ == 'query'},
        data={param.name: value for param, value in parameters.items() if param.in_ == 'formData'},
    )


@csrf_exempt
def proxy(request, user_pk: int):
    user_pk = int(user_pk)

    if AccessAttemptFailure.objects.filter(datetime__gte=now() - timedelta(hours=24)).count() >= 10:
        raise PermissionDenied('Proxy is currently unavailable, please try again later')

    user = get_object_or_404(User, pk=user_pk)
    assert user.email, f'User has no email: {user}'
    if user.failed_attempts.filter(datetime__gte=now() - timedelta(hours=24)).count() > \
            settings.HUBSTAFF_MAX_FAILED_BEFORE_BLOCK:
        raise SuspiciousOperation('Too many attempts to access Hubstaff API with wrong credentials; '
                                  'please wait 24h before further attempts')

    # convert user's request to list of parameters
    try:
        permuted_parameters = _request_to_params(request)
    except ValueError as exc:
        return JsonResponse(status=400, data={'error': str(exc)})

    # convert each parameter to original (non-mutated) one, or drop if parameter is redundant
    parameters = {}
    mixer = get_mixer(seed=user_pk, email=user.email)
    for permuted_parameter, value in permuted_parameters.items():
        try:
            log.debug(f'Permuted parameter: {permuted_parameter}')
            permuted_definition, restored_parameter = mixer.reverse(permuted_parameter)
            log.debug(f'Restored parameter: {restored_parameter}')

            if restored_parameter.in_ == 'path':
                path_params = permuted_definition.re_path.match(permuted_parameter.path).groupdict()
                assert len(path_params) <= 1, f'Multiple path parameters not supported: {path_params}'
                value = next(iter(path_params.values()))

            parameters[restored_parameter] = value
        except ValueError:
            if permuted_parameter.in_ in {'path', 'header'}:
                log.debug(f'Ignoring unexpected {permuted_parameter.in_} parameter: {permuted_parameter}')
                continue  # we ignore redundant headers

            return JsonResponse(status=400, data={
                'error': f'Unexpected parameter: '
                         f'method="{permuted_parameter.method.upper()}" path="{permuted_parameter.path}" '
                         f'location="{permuted_parameter.in_.upper()}" '
                         f'name="{permuted_parameter.name}" value="{value}"'
            })

    log.info(f'IN:\n{pformat(permuted_parameters)}')
    log.info(f'OUT:\n{pformat(parameters)}')

    # make a request with original (pure) parameters
    try:
        request = _params_to_request(host='https://' + mixer.swagger['host'], parameters=parameters)
    except ValueError as exc:
        return JsonResponse(status=400, data={
            'error': str(exc),
        })

    for request_processor in mixer.request_processors:
        request_processor(request, meta=mixer.meta)  # TODO: refactor

    try:
        prepared_request = session.prepare_request(request)
        response = session.send(prepared_request, timeout=(60, 60))
    except RequestException as exc:
        return JsonResponse(status=500, data={'error': f'API error: {exc}'})

    if response.status_code == 401:
        AccessAttemptFailure.objects.create(user=user)

    try:
        result = response.json()
        for processor in mixer.result_processors:
            result = processor(result, meta=mixer.meta)
    except ValueError:
        result = response.text

    return JsonResponse(status=response.status_code, data=result)


def handler404(request, exception):
    return HttpResponse(status=404, content='')
