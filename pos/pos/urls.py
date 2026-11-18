"""ems URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from posApp import views
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from posApp import api_views
from django.contrib import admin
from django.urls import path, include




urlpatterns = [
    path('admin/', admin.site.urls,name="admin-site"),
    path('', include('posApp.urls')),
    path('api/', include('posApp.urls')),
    path('api/home_api/', views.home_api, name='home_api'),
    path('api-token-auth/', obtain_auth_token, name='api_token_auth'),
    path('api/token/',         TokenObtainPairView.as_view()),
    path('api/token/refresh/', TokenRefreshView.as_view()),
    path("api/login/", obtain_auth_token),
    
    
]
