from django.conf import settings
from django.conf.urls.static import static
from django.contrib.admin.sites import site
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.urls import include, path, re_path
from src.core.views import ApiDescriptionView, SwaggerView, SubmitTaskView, proxy, api_user_update

urlpatterns = [
    path('', login_required(ApiDescriptionView.as_view()), name='main'),
    re_path(r'^v(?P<user_pk>\d+)/', proxy, name='proxy'),
    path('swagger.json', login_required(SwaggerView.as_view()), name='swagger'),
    path(r'login/', LoginView.as_view(), name='login'),

    path('api/user/update', api_user_update),
    path('submit/', login_required(SubmitTaskView.as_view()), name='submit_task'),
    path('admin/', site.urls),
    path('', include('django.contrib.auth.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = 'src.core.views.handler404'
