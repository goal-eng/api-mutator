import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import requests

from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import SuspiciousOperation, PermissionDenied
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import View

from django.views.generic import TemplateView


from .mixer import ApiMixer, freeze
from .models import AccessAttemptFailure


def rev(d: dict) -> dict:
    return {v: k for k, v in d.items()}


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

    mixer = get_mixer(seed=user_pk)

    # retrieve original path
    permuted_path = request.path
    try:
        path = rev(mixer.path_mapping)[permuted_path]
    except KeyError:
        raise ValueError(f'Unknown path "{permuted_path}"')

    # retrieve original method
    permuted_method = request.method.lower()
    try:
        method = rev(mixer.method_mapping)[(permuted_path, permuted_method)][1]
    except KeyError:
        raise ValueError(f'Path "{permuted_path}" does not accept "{permuted_method.upper()}" requests')

    # retrieve original format
    try:
        # fix for Swagger UI: if payload is empty, format is 'text/plain' -> convert it to correct format
        if request.content_type == 'text/plain':
            permuted_format = next(
                fmt for pth, mthd, fmt in rev(mixer.format_mapping)
                if pth == permuted_path and mthd == permuted_method
            )
        else:
            permuted_format = next(fmt for fmt in mixer.FORMATS if fmt.name == request.content_type)
    except StopIteration:
        raise ValueError(f'Content type "{request.content_type}" is unknown')
    try:
        format = rev(mixer.format_mapping)[(permuted_path, permuted_method, permuted_format)][2]
    except KeyError:
        raise ValueError(f'Format "{permuted_format.name}" '
                         f'is not supported by "{permuted_method.upper()} {permuted_path}"')

    # retrieve original params
    permuted_params = []
    for header, value in request.headers.items():
        permuted_params.append({'in': 'header', 'name': header.lower(), 'value': value})

    for post, value in request.POST.items():
        permuted_params.append({'in': 'formData', 'name': post, 'value': value})

    for get, value in request.GET.items():
        permuted_params.append({'in': 'query', 'name': get, 'value': value})

    try:
        data = permuted_format.decode(request.body) if request.body else {}
        if not isinstance(data, dict):
            raise ValueError()
    except Exception:
        raise ValueError(f'Could not decode {permuted_format.name}: {request.body}')

    for key, value in data.items():
        permuted_params.append({'in': 'body', 'name': key, 'value': value})

    params = []
    rev_parameter_mapping = rev(mixer.parameter_mapping)
    for p in permuted_params:
        try:
            params.append({
                **p,
                'in': rev_parameter_mapping[(permuted_path, permuted_method, p['in'], p['name'])][2],
                'name': rev_parameter_mapping[(permuted_path, permuted_method, p['in'], p['name'])][3],
            })
        except KeyError:
            if p['in'] == 'header':
                print(f'Skipping header {p}')
            else:
                raise ValueError(f'Param {p} is not valid for "{permuted_method.upper()} {permuted_path}"')

    # construct request to original API
    path = path.format(**{p['name']: p['value'] for p in params if p['in'] == 'path'})  # /v1/user/{id} -> /v1/user/1
    data = {p['name']: p['value'] for p in params if p['in'] == 'formData'}
    query = {p['name']: p['value'] for p in params if p['in'] == 'query'}
    headers = {p['name']: p['value'] for p in params if p['in'] == 'header'}

    print(f'Input: {permuted_method.upper()} {permuted_path}')
    for param in permuted_params:
        print(f'\t{param}')
    print(f'Output: {method.upper()} {path}')
    # print(f'\theaders={headers}, json={data}, params={query}')
    for param in params:
        print(f'\t{param}')

    response = getattr(session, method)(
        'https://' + mixer.swagger['host'] + path,
        headers=headers, json=data, params=query, timeout=(60, 60),
    )
    if response.status_code == 401:
        AccessAttemptFailure.objects.create(user=user)
    response.raise_for_status()

    data = response.json()

    return HttpResponse(format.encode(data), content_type=format.name)
