import copy
from collections.abc import MutableMapping
from functools import partial
from typing import TYPE_CHECKING, Any, Self, TypeVar

from django import VERSION as DJANGO_VERSION
from django.db.models import Model
from django.db.models.deletion import Collector
from django.db.models.query import QuerySet
from django.db.models.query_utils import Q
from django.db.models.sql.query import Query

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

    def __init__(
        self,
        model: type[Model] | None = None,
        query: Query | None = None,
        using: str | None = None,
        hints: dict[str, Model] | None = None,
    ) -> None:
        super().__init__(model=model, query=query, using=using, hints=hints)
        self.init_modify_query_hook()
        self._unpatched = False

    def create(self, **kwargs: Any) -> T:
        if not self._unpatched:
            if self.model.Permanent.restore_on_create and not kwargs.get(
                settings.FIELD
            ):
                qs = self.get_unpatched()
                return qs.get_restore_or_create(**kwargs)
        return super().create(**kwargs)

    def get_restore_or_create(
        self, defaults: MutableMapping[str, Any] | None = None, **kwargs: Any
    ) -> tuple[T, bool]:
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

    def delete(self, force: bool = False) -> tuple[int, dict[str, int]]:
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

        if DJANGO_VERSION < (4, 0, 0):
            del_query.query.clear_ordering(force_empty=True)
        else:
            del_query.query.clear_ordering(force=True, clear_default=True)

        collector = Collector(using=del_query.db)
        collector.collect(del_query)
        deleted, _rows_count = collector.delete(force=force)

        # Clear the result cache, in case this QuerySet gets reused.
        self._result_cache = None

        return deleted, _rows_count

    delete.alters_data = True

    def restore(self) -> int:
        return self.get_unpatched().update(**{settings.FIELD: settings.FIELD_DEFAULT})

    def _update(self, values):
        # Modifying trigger field have to effect all objects
        if settings.FIELD in [field.attname for field, _, _ in values] and not getattr(
            self, "_unpatched", False
        ):
            return self.get_unpatched()._update(values)
        return super()._update(values)

    def get_unpatched(self) -> "Self":
        qs = self._clone()
        qs._unpatch()
        return qs

    def _clone(self, *args, **kwargs) -> "Self":
        c = super()._clone(*args, **kwargs)
        # We need clones stay unpatched
        if getattr(self, "_unpatched", False):
            c._unpatched = True
            c._unpatch()
        return c

    def _patch(self, q_object: T) -> None:
        self.query.add_q(q_object)

    def _unpatch(self) -> None:
        self._unpatched = True
        if not self.query.where.children:
            return
        condition = self.query.where.children[0]

        is_patched = (
            hasattr(condition, "lhs") and condition.lhs.target.name == settings.FIELD
        )
        if is_patched:
            del self.query.where.children[0]

    def init_modify_query_hook(self) -> None:
        """Child classes override me"""
        pass


class NonDeletedQuerySet(BasePermanentQuerySet[T]):
    def init_modify_query_hook(self) -> None:
        if not self.query.where:
            self._patch(Q(**{settings.FIELD: settings.FIELD_DEFAULT}))


class DeletedQuerySet(BasePermanentQuerySet[T]):
    def init_modify_query_hook(self) -> None:
        if not self.query.where:
            self._patch(~Q(**{settings.FIELD: settings.FIELD_DEFAULT}))


class PermanentQuerySet(BasePermanentQuerySet[T]):
    def init_modify_query_hook(self) -> None:
        return
