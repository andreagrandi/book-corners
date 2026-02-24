from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def style_preview(request: HttpRequest) -> HttpResponse:
    return render(request, "libraries/style_preview.html")
