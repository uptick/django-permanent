import copy
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from django.db.models import Manager, Model
from django.db.models.deletion import Collector
from django.db.models.query import QuerySet
from django.db.models.query_utils import Q
from django.db.models.sql.where import WhereNode

from . import settings
from .signals import post_restore, pre_restore

if TYPE_CHECKING:
    from .models import PermanentModel
T = TypeVar("T", bound="PermanentModel")


class BasePermanentQuerySet(QuerySet[T]):
    def __deepcopy__(self, memo):
        obj = self.__class__(model=self.model)
        for k, v in self.__dict__.items():
            if k == "_result_cache":
                obj.__dict__[k] = None
            else:
                obj.__dict__[k] = copy.deepcopy(v, memo)
        return obj

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._unpatched = False

    def create(self, **kwargs):
        if not self._unpatched:
            if self.model.Permanent.restore_on_create and not kwargs.get(
                settings.FIELD
            ):
                qs = self.get_unpatched()
                return qs.get_restore_or_create(**kwargs)
        return super().create(**kwargs)

    def get_restore_or_create(self, **kwargs):
        qs = self.get_unpatched()
        obj, created = qs.get_or_create(**kwargs)
        if isinstance(obj, dict):
            geter, seter = obj.get, obj.__setitem__
        else:
            geter, seter = partial(getattr, obj), partial(setattr, obj)

        if not created and geter(settings.FIELD, True):
            pre_restore.send(sender=self.model, instance=obj)
            seter(settings.FIELD, settings.FIELD_DEFAULT)
            self.model.all_objects.filter(id=geter("id")).update(
                **{settings.FIELD: settings.FIELD_DEFAULT}
            )
            post_restore.send(sender=self.model, instance=obj)

        return obj

    def delete(self, force=False):
        """
        Deletes the records in the current QuerySet.
        """
        assert self.query.can_filter(), "Cannot use 'limit' or 'offset' with delete."

        del_query = self._clone()

        # The delete is actually 2 queries - one to find related objects,
        # and one to delete. Make sure that the discovery of related
        # objects is performed on the same database as the deletion.
        del_query._for_write = True

        # Disable non-supported fields.
        del_query.query.select_for_update = False
        del_query.query.select_related = False
        del_query.query.clear_ordering(force_empty=True)

        collector = Collector(using=del_query.db)
        collector.collect(del_query)
        deleted, _rows_count = collector.delete(force=force)

        # Clear the result cache, in case this QuerySet gets reused.
        self._result_cache = None

        return deleted, _rows_count

    delete.alters_data = True

    def restore(self):
        return self.get_unpatched().update(**{settings.FIELD: settings.FIELD_DEFAULT})

    def values(self, *fields):
        return super().values(*fields)

    def values_list(self, *fields, **kwargs):
        return super().values_list(*fields, **kwargs)

    def _update(self, values):
        # Modifying trigger field have to effect all objects
        if settings.FIELD in [field.attname for field, _, _ in values] and not getattr(
            self, "_unpatched", False
        ):
            return self.get_unpatched()._update(values)
        return super()._update(values)

    def get_unpatched(self):
        qs = self._clone()
        qs._unpatch()
        return qs

    def _clone(self, *args, **kwargs):
        c = super()._clone(*args, **kwargs)
        # We need clones stay unpatched
        if getattr(self, "_unpatched", False):
            c._unpatched = True
            c._unpatch()
        return c

    def _patch(self, q_object):
        self.query.add_q(q_object)

    def _unpatch(self):
        self._unpatched = True
        if not self.query.where.children:
            return
        condition = self.query.where.children[0]

        is_patched = (
            hasattr(condition, "lhs") and condition.lhs.target.name == settings.FIELD
        )
        if is_patched:
            del self.query.where.children[0]


class NonDeletedQuerySet(BasePermanentQuerySet[T]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.query.where:
            self._patch(Q(**{settings.FIELD: settings.FIELD_DEFAULT}))


class DeletedWhereNode(WhereNode):
    pass


class DeletedQuerySet(BasePermanentQuerySet[T]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.query.where:
            self.query.where_class = DeletedWhereNode
            self._patch(~Q(**{settings.FIELD: settings.FIELD_DEFAULT}))


class AllWhereNode(WhereNode):
    pass


class PermanentQuerySet(BasePermanentQuerySet[T]):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.query.where:
            self.query.where_class = AllWhereNode
