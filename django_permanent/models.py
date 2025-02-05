from django.db import models, router
from django.db.models.deletion import Collector
from django.utils.module_loading import import_string

from . import settings
from .deletion import *  # NOQA
from .query import DeletedQuerySet, NonDeletedQuerySet, PermanentQuerySet
from .related import *  # NOQA
from .signals import post_restore, pre_restore

Manager = models.Manager().from_queryset(PermanentQuerySet)


class PermanentModel(models.Model):
    # Ideally we would be using MakePermanentManagers here
    # but we don't as it doesn't play nicely with mypy-django-stubs
    objects = NonDeletedQuerySet.as_manager()
    all_objects = PermanentQuerySet.as_manager()
    deleted_objects = DeletedQuerySet.as_manager()

    class Meta:
        abstract = True

        default_manager_name = "objects"
        base_manager_name = "objects"

    class Permanent:
        restore_on_create = False

    def delete(self, using=None, force=False):
        using = using or router.db_for_write(self.__class__, instance=self)
        assert (
            self._get_pk_val() is not None
        ), f"{self._meta.object_name} object can't be deleted because its {self._meta.pk.attname} attribute is set to None."
        collector = Collector(using=using)
        collector.collect([self])
        collector.delete(force=force)

    delete.alters_data = True  # type: ignore

    def restore(self) -> None:
        pre_restore.send(sender=self.__class__, instance=self)
        setattr(self, settings.FIELD, settings.FIELD_DEFAULT)
        self.save(update_fields=[settings.FIELD])
        post_restore.send(sender=self.__class__, instance=self)


field = import_string(settings.FIELD_CLASS)
PermanentModel.add_to_class(settings.FIELD, field(**settings.FIELD_KWARGS))
