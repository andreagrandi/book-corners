from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("libraries", "0017_libraryphoto_idx_photo_creator_created_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="library",
            name="pending_changes",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="library",
            name="pending_photo",
            field=models.ImageField(
                blank=True,
                default="",
                upload_to="libraries/pending_photos/%Y/%m/",
            ),
        ),
        migrations.AddField(
            model_name="library",
            name="pending_photo_thumbnail",
            field=models.ImageField(
                blank=True,
                default="",
                upload_to="libraries/pending_photos/thumbnails/%Y/%m/",
            ),
        ),
        migrations.AddIndex(
            model_name="library",
            index=models.Index(
                fields=["-updated_at"],
                name="idx_lib_updated_at_desc",
            ),
        ),
    ]
