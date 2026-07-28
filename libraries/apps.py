from django.apps import AppConfig


class LibrariesConfig(AppConfig):
    """Configure the libraries application and image decoders.
    Registers shared startup behavior for library features."""

    name = "libraries"

    def ready(self) -> None:
        """Register mobile HEIC and HEIF decoding with Pillow.
        Makes the shared image pipeline understand common phone photo formats."""
        from pillow_heif import register_heif_opener

        register_heif_opener()
