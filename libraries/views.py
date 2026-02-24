from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "home.html")


def style_preview(request: HttpRequest) -> HttpResponse:
    return render(request, "libraries/style_preview.html")
