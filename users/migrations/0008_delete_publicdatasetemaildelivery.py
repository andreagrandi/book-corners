from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_publicdatasetemaildelivery"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PublicDatasetEmailDelivery",
        ),
    ]
