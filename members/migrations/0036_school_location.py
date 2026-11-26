from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("members", "0035_workflow_level_self_approval")]

    operations = [
        migrations.AddField(
            model_name="school",
            name="location",
            field=models.CharField(
                default="", max_length=200, verbose_name="Department location"
            ),
            preserve_default=False,
        ),
    ]
