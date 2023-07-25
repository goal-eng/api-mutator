import json
import logging
from datetime import timedelta
from functools import lru_cache, partial, wraps
from pprint import pformat
from typing import Callable, Dict, List, Tuple, Union

import requests
import sentry_sdk
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django.views.generic.base import View
from django.views.generic.edit import FormView
from ratelimit import RateLimitException, limits

from src.core.forms import SubmitTaskForm
from src.core.hubstaff import HubstaffV2
from src.core.jira import JiraV3, dt
from src.core.mixer import ApiMixer, Parameter
from src.core.models import AccessAttemptFailure, SubmitTaskAttempt
from src.core.permutations import (
    check_and_remove_auth_headers,
    permute_locations,
    permute_paths,
    personal_filter_result_processor,
    redirect_self_endpoint,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__file__)
hubstaff = HubstaffV2(refresh_token=settings.HUBSTAFF_REFRESH_TOKEN)
jira = JiraV3(settings.JIRA_PROJECT_KEY)


class HubstaffUserNotFound(Exception):
    pass


class ParameterError(Exception):
    pass


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

    return JsonResponse({
        'message': f'Updated {email}',
        'user': user.username,
        'password': user.api_credentials.password,
        'app_token': user.api_credentials.app_token,
    })


def get_hubstaff_data(email: str) -> Tuple[int, dict, List[dict]]:
    # return Hubstaff user id, organization, projects
    for organization in hubstaff.iter_organizations():
        for user in hubstaff.iter_organization_users(organization['id']):
            if user['email'] == email:
                projects = list(hubstaff.iter_organization_projects(organization['id']))
                return user['id'], organization, projects

    raise HubstaffUserNotFound('User not found in Hubstaff API response')


def patch_swagger_auth(swagger: dict):
    """
    Add App-Token and Auth-Token headers to all endpoints. In-place.
    """

    del swagger['info']
    del swagger['securityDefinitions']

    app_token = {
        "in": "header",
        "name": "App-Token",
        "description": "User's application token",
        "type": "string",
        "required": True
    }
    auth_token = {
        "in": "header",
        "name": "Auth-Token",
        "description": "User's authentication token",
        "type": "string",
        "required": True
    }

    for path in swagger['paths'].values():
        for method_data in path.values():
            method_data['parameters'] = [app_token, auth_token, *method_data.get('parameters', [])]

    swagger['paths']['/v2/users/auth'] = {
        "post": {
            "summary": "Retrieve auth token",
            "description": "Returns auth token for current user.",
            "produces": ["application/json"],
            "parameters": [
                {
                    "in": "formData",
                    "name": "email",
                    "description": "User's email",
                    "type": "string",
                    "required": True
                },
                {
                    "in": "formData",
                    "name": "password",
                    "description": "User's password",
                    "type": "string",
                    "required": True
                },
                app_token,
            ],
            "responses": {
                "200": {
                    "description": "Auth token",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "auth_token": {
                                "type": "string",
                                "description": "Auth token"
                            }
                        }
                    }
                },
                "400": {
                    "description": "Invalid parameters",
                    "schema": {
                        "$ref": "#/definitions/Hubstaff_Public_V2_Entities_Error"
                    }
                },
                "401": {
                    "description": "Unauthorized",
                    "schema": {
                        "$ref": "#/definitions/Hubstaff_Public_V2_Entities_Error"
                    }
                },
                "429": {
                    "description": "Rate limit exceeded",
                    "schema": {
                        "$ref": "#/definitions/Hubstaff_Public_V2_Entities_Error"
                    }
                }
            },
            "tags": [
                "users"
            ],
            "operationId": "getV2Auth"
        }
    }


# save ApiMixer instance in memory, so that we don't regenerate mappings on each request
@lru_cache(maxsize=32)
def get_mixer(user_pk: int) -> ApiMixer:
    user = User.objects.get(pk=user_pk)
    hubstaff_user_id, hubstaff_user_organization, hubstaff_user_projects = get_hubstaff_data(user.email)
    swagger = json.loads(settings.SWAGGER_FILE_PATH.read_text())
    patch_swagger_auth(swagger)

    return ApiMixer(
        swagger=swagger,
        seed=user.pk,
        permutations=(
            permute_paths,
            permute_locations,
            # permute_result,
        ),
        request_processors=(
            partial(check_and_remove_auth_headers, user=user),
            partial(redirect_self_endpoint, hubstaff_user_id=hubstaff_user_id),
        ),
        result_processors=(
            partial(
                personal_filter_result_processor,
                email=user.email,
                hubstaff_user_id=hubstaff_user_id,
                hubstaff_user_organization=hubstaff_user_organization,
                hubstaff_user_projects=hubstaff_user_projects,
            ),
            # permute_result_processor,
        ),
    )


class ApiDescriptionView(TemplateView):
    template_name = 'api.html'

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            'SUPPORT_EMAIL': settings.SUPPORT_EMAIL,
        }


class SwaggerView(View):
    def get(self, *args, **kwargs):
        try:
            mixer = get_mixer(user_pk=self.request.user.pk)
        except HubstaffUserNotFound as exc:
            sentry_sdk.capture_exception(exc)
            return JsonResponse({
                "info": {
                    "title": "Unable to load API definitions",
                    "description": "Unfortunately, we cannot find user with your email in Hubstaff.\nPlease ensure that you accepted email invitation and thus joined Hubstaff organization.",
                },
                "swagger": "2.0",
            })
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
        except (PermissionDenied, ParameterError, ValueError) as exc:
            payload = {'code': exc.__class__.__name__.lower(), 'error': str(exc)}
            return JsonResponse(status=400, data=payload)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            log.error(exc)
            payload = {'code': 'error', 'error': 'Something went wrong, we\'re investigating'}
            return JsonResponse(status=500, data=payload)

    return wrapper


def rate_limit(fn: Callable) -> Callable:
    limited_fn = limits(calls=16, period=60)(fn)

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return limited_fn(*args, **kwargs)
        except RateLimitException:
            return JsonResponse(status=429, data={'error': 'Rate limit exceeded, please retry in a minute'})

    return wrapper


@csrf_exempt
@jsonify_exceptions
@rate_limit
def proxy(request, user_pk: int):
    if AccessAttemptFailure.objects.filter(datetime__gte=now() - timedelta(hours=1)).count() >= 10:
        raise PermissionDenied('Proxy is currently unavailable, please try again later')

    user_pk = int(user_pk)

    user = get_object_or_404(User, pk=user_pk)
    assert user.email, f'User has no email: {user}'

    # convert user's request to list of parameters
    permuted_parameters = _request_to_params(request)

    # convert each parameter to original (non-mutated) one, or drop if parameter is redundant
    parameters = {}
    mixer = get_mixer(user_pk=user_pk)
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

            raise ParameterError(
                f'Unexpected parameter: '
                f'method="{permuted_parameter.method.upper()}" path="{permuted_parameter.path}" '
                f'location="{permuted_parameter.in_.upper()}" '
                f'name="{permuted_parameter.name}" value="{value}"'
            )

    log.info(f'IN:\n{pformat(permuted_parameters)}')
    log.info(f'OUT:\n{pformat(parameters)}')

    # make a request with original (pure) parameters
    request = _params_to_request(host='https://' + mixer.swagger['host'], parameters=parameters)

    if request.url == f'{hubstaff.BASE_URL}/v2/users/auth':
        # this is a hack so that candidates don't reach real auth endpoint but instead
        # get fake credentials from this proxy

        if user.email != (email := request.data.get('email', '')):
            raise ParameterError(f'Wrong email provided: {email}')

        if user.api_credentials.password != request.data.get('password'):
            raise ParameterError('Password mismatch')

        if user.api_credentials.app_token != request.headers.get('App-Token', ''):
            raise ParameterError('App-Token mismatch')

        result = {
            'auth_token': user.api_credentials.auth_token,
        }
        status_code = 200

    else:
        if request.method.lower() != 'get':
            raise PermissionDenied('Only GET method is allowed :-O')

        for request_processor in mixer.request_processors:
            request_processor(request)

        response = hubstaff.send(request)

        status_code = response.status_code
        if status_code == 401:
            AccessAttemptFailure.objects.create(user=user)

        result = response.json()

    for processor in mixer.result_processors:
        result = processor(result)

    return JsonResponse(status=status_code, data=result)


class SubmitTaskView(FormView):
    template_name = 'submit_task.html'
    form_class = SubmitTaskForm

    def form_valid(self, form):
        zip_file = form.cleaned_data.get('zip_file')
        user = self.request.user

        try:
            if SubmitTaskAttempt.objects.filter(user=user, datetime__gte=now() - timedelta(days=30)).count() >= 2:
                raise PermissionDenied('You have exceeded allowed submission count.')

            custom_fields = {
                settings.JIRA_HUBSTAFF_BOT_SUBMISSION_CANDIDATE_EMAIL_CF: user.email,
            }
            issue = None
            issues = jira.find_issue_by_custom_field(custom_fields)
            if len(issues) > 0:
                issue = issues[0]
                issue_created = dt(issue['fields']['created'])
                if (now() - issue_created) > timedelta(days=30):
                    issue = None
                    log.info("The latest issue for the candidate `%s` was created over 30 days ago so created a new issue.", user.email)
                elif len(issues) > 1:
                    log.info("There are multiple issues for the candidate `%s`. Selected the lastest issue `%s`.", user.email, issue['key'])
            if not issue:
                issue = jira.create_issue(f'Hubstaff bot - {user.email}', settings.JIRA_HUBSTAFF_BOT_SUBMISSION_ISSUE_TYPE, custom_fields=custom_fields)
            jira.add_issue_attachment(issue['id'], (f'hubstaff_bot_{user.email}_{zip_file.name}', zip_file))

            SubmitTaskAttempt.objects.create(user=user)
            messages.success(self.request, 'Your task was successfully submitted')
        except PermissionDenied as exc:
            messages.error(self.request, str(exc), 'danger')

        return self.render_to_response(self.get_context_data(form=form))


def handler404(request, exception):
    return HttpResponse(status=404, content='')
