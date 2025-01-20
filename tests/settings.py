INSTALLED_APPS = ["django_permanent", "tests.test_app"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": 'testdb'}}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

MIDDLEWARE_CLASSES = []
