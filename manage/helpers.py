from django.template.loader import render_to_string


def render_with_toast(request, template, context, *, toast_message, toast_type="success"):
    """Render a template and append an OOB toast notification for HTMX responses."""
    from django.shortcuts import render

    response = render(request, template, context)
    toast_html = render_to_string(
        "manage/_toast.html",
        {"toast_message": toast_message, "toast_type": toast_type},
    )
    response.content = response.content + toast_html.encode()
    return response
