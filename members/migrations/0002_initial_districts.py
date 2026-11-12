from django.db import migrations


DISTRICTS = [
    ('Ampara', 'AMP', 'Eastern'), ('Anuradhapura', 'ANU', 'North Central'),
    ('Badulla', 'BAD', 'Uva'), ('Batticaloa', 'BAT', 'Eastern'),
    ('Colombo', 'COL', 'Western'), ('Galle', 'GAL', 'Southern'),
    ('Gampaha', 'GAM', 'Western'), ('Hambantota', 'HAM', 'Southern'),
    ('Jaffna', 'JAF', 'Northern'), ('Kalutara', 'KAL', 'Western'),
    ('Kandy', 'KAN', 'Central'), ('Kegalle', 'KEG', 'Sabaragamuwa'),
    ('Kilinochchi', 'KIL', 'Northern'), ('Kurunegala', 'KUR', 'North Western'),
    ('Mannar', 'MAN', 'Northern'), ('Matale', 'MTL', 'Central'),
    ('Matara', 'MTR', 'Southern'), ('Monaragala', 'MON', 'Uva'),
    ('Mullaitivu', 'MUL', 'Northern'), ('Nuwara Eliya', 'NUW', 'Central'),
    ('Polonnaruwa', 'POL', 'North Central'), ('Puttalam', 'PUT', 'North Western'),
    ('Ratnapura', 'RAT', 'Sabaragamuwa'), ('Trincomalee', 'TRI', 'Eastern'),
    ('Vavuniya', 'VAV', 'Northern'),
]


def seed_districts(apps, schema_editor):
    district = apps.get_model('members', 'District')
    for name, code, province in DISTRICTS:
        district.objects.using(schema_editor.connection.alias).get_or_create(name=name, defaults={'code': code, 'province': province})


class Migration(migrations.Migration):
    dependencies = [('members', '0001_initial')]
    operations = [migrations.RunPython(seed_districts, migrations.RunPython.noop)]
