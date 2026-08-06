from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_add_contributor_agreement_acceptance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contributoragreementacceptance",
            name="channel",
            field=models.CharField(
                choices=[
                    (
                        "api_credential_registration",
                        "API credential registration",
                    ),
                    ("api_social_apple", "API Apple registration"),
                    ("api_social_google", "API Google registration"),
                    ("api_existing_user", "API existing-user acceptance"),
                    (
                        "web_credential_registration",
                        "Web credential registration",
                    ),
                    ("web_social_apple", "Web Apple registration"),
                    ("web_social_google", "Web Google registration"),
                ],
                max_length=40,
            ),
        ),
    ]

