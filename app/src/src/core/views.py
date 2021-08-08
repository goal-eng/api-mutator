import json
import logging
from datetime import timedelta
from functools import lru_cache
from pprint import pformat
from typing import Dict, Union

import requests
from django.db import transaction
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import SuspiciousOperation, PermissionDenied
from django.http import JsonResponse, HttpResponse, HttpRequest, Http404
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django.views.generic.base import View
from src.core.mixer import ApiMixer, Parameter
from src.core.models import AccessAttemptFailure
from src.core.hubstaff import hubstaff
from src.core.permutations import permute_paths, permute_locations, permute_result, \
    permute_credentials, personal_filter_result_processor, permute_result_processor

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__file__)


@csrf_exempt
def api_user_update(request):
    if not request.method == 'POST':
        raise Http404()

    email = request.POST.get('email', None)
    if not email:
        return JsonResponse({'error': 'Missing email'}, status=400)

    if not settings.API_KEY:
        return JsonResponse({'error': 'API key not set'}, status=500)

    if request.headers.get('ApiKey', '') != settings.API_KEY:
        return JsonResponse({'error': 'Bad API key'}, status=403)

    with transaction.atomic():
        user, _ = User.objects.get_or_create(
            username=email,
            email=email,
        )
        password = User.objects.make_random_password()
        user.set_password(password)
        user.save()

    log.info(f'Updated {email} password: {password}')
    return JsonResponse({'message': f'Updated {email}', 'password': password})


# save ApiMixer instance in memory, so that we don't regenerate mappings on each request
@lru_cache(maxsize=32)
def get_mixer(user_pk: int) -> ApiMixer:

    user_obj = User.objects.get(pk=user_pk)

    # get user ID and project ID
    offset = 0
    while True:
        users = hubstaff.get('/users', params={
            'organization_memberships': True,
            'project_memberships': True,
            'offset': offset,
        })['users']

        try:
            user_data = next(user for user in users if user['email'] == user_obj.email)
            break
        except StopIteration:
            offset += len(users)

        if not users:
            raise ValueError(f'User with email {user_obj.email} not found in Hubstaff API /users response: {users}')

    return ApiMixer(
        swagger=json.loads(settings.SWAGGER_FILE_PATH.read_text()),
        seed=user_obj.pk,
        meta={
            'user': user_obj,
            'user_data': user_data,
        },
        permutations=(
            permute_paths,
            # permute_methods,
            permute_locations,
            permute_result,
        ),
        request_processors=(
            permute_credentials,
        ),
        result_processors=(
            personal_filter_result_processor,
            permute_result_processor,
        ),
    )


class ApiDescriptionView(TemplateView):
    template_name = 'api.html'


class SwaggerView(View):
    def get(self, *args, **kwargs):
        mixer = get_mixer(user_pk=self.request.user.pk)
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


def jsonify_exceptions(fn: callable) -> callable:

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return JsonResponse(status=400, data=permute_result_processor({
                'error': str(exc),
                'help': f'Please contact {settings.SUPPORT_EMAIL} if you think '
                        f'the API is misbehaving or you have any questions',
            }, meta={}))

    return wrapper


@csrf_exempt
@jsonify_exceptions
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
    permuted_parameters = _request_to_params(request)

    # convert each parameter to original (non-mutated) one, or drop if parameter is redundant
    parameters = {}
    mixer = get_mixer(user_pk=user.pk)
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

            raise ValueError(
                f'Unexpected parameter: '
                f'method="{permuted_parameter.method.upper()}" path="{permuted_parameter.path}" '
                f'location="{permuted_parameter.in_.upper()}" '
                f'name="{permuted_parameter.name}" value="{value}"'
            )

    log.info(f'IN:\n{pformat(permuted_parameters)}')
    log.info(f'OUT:\n{pformat(parameters)}')

    # make a request with original (pure) parameters
    request = _params_to_request(host='https://' + mixer.swagger['host'], parameters=parameters)

    if request.url == 'https://api.hubstaff.com/v1/auth':
        # this is a hack so that candidates don't reach real auth endpoint but instead
        # get fake credentials from out proxy

        if user.email != (email := request.data.get('email', '')):
            raise ValueError(f'Wrong email provided: {email}')

        if not user.check_password(request.data.get('password')):
            raise ValueError('Password mismatch')

        if user.api_credentials.app_token != request.headers.get('App-Token', ''):
            raise ValueError('App-Token mismatch')

        result = {
            'id': None,
            'name': None,
            'last_activity': None,
            'auth_token': user.api_credentials.auth_token,
        }
        status_code = 200

    else:
        for request_processor in mixer.request_processors:
            request_processor(request, meta=mixer.meta)  # TODO: refactor

        prepared_request = session.prepare_request(request)
        response = session.send(prepared_request, timeout=(60, 60))

        status_code = response.status_code
        if status_code == 401:
            AccessAttemptFailure.objects.create(user=user)

        result = response.json()

    for processor in mixer.result_processors:
        result = processor(result, meta=mixer.meta)

    return JsonResponse(status=status_code, data=result)


def handler404(request, exception):
    return HttpResponse(status=404, content='')
