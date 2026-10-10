"""
Tiny in-memory stand-in for the Firestore client, covering only the calls this
project makes. Lets the test suite run with no emulator and no Google account.
"""
import itertools

_ids = itertools.count(1)


class Snapshot:
    def __init__(self, ref, data):
        self.reference = ref
        self.id = ref.id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class DocRef:
    def __init__(self, db, coll, doc_id):
        self._db, self._coll, self.id = db, coll, doc_id

    def _store(self):
        return self._db._data.setdefault(self._coll, {})

    def get(self):
        return Snapshot(self, self._store().get(self.id))

    def set(self, data, merge=False):
        if merge and self.id in self._store():
            self._store()[self.id].update(data)
        else:
            self._store()[self.id] = dict(data)

    def update(self, data):
        if self.id not in self._store():
            raise KeyError(f"No document to update: {self._coll}/{self.id}")
        self._store()[self.id].update(data)

    def delete(self):
        self._store().pop(self.id, None)


class Query:
    def __init__(self, db, coll, filters=(), order=None, limit=None, after=None):
        self._db, self._coll = db, coll
        self._filters, self._order, self._limit, self._after = list(filters), order, limit, after

    def _copy(self, **kw):
        q = Query(self._db, self._coll, self._filters, self._order, self._limit, self._after)
        for k, v in kw.items():
            setattr(q, '_' + k, v)
        return q

    def where(self, field=None, op=None, value=None, *, filter=None):
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        return self._copy(filters=self._filters + [(field, op, value)])

    def order_by(self, field, direction='ASCENDING'):
        return self._copy(order=(field, direction))

    def limit(self, n):
        return self._copy(limit=n)

    def start_after(self, snapshot):
        return self._copy(after=snapshot.id)

    @staticmethod
    def _match(doc, field, op, value):
        if field not in doc:
            return False
        v = doc[field]
        try:
            if op == '==':
                return v == value
            if op == '<':
                return v < value
            if op == '>':
                return v > value
            if op == 'in':
                return v in value
        except TypeError:
            return False
        raise NotImplementedError(op)

    def stream(self, timeout=None):
        store = self._db._data.get(self._coll, {})
        items = [(i, d) for i, d in store.items()
                 if all(self._match(d, *f) for f in self._filters)]
        if self._order:
            field, direction = self._order
            key = (lambda x: x[0]) if field == '__name__' else (lambda x: (x[1].get(field) is None, x[1].get(field)))
            items.sort(key=key, reverse=(direction == 'DESCENDING'))
            if field != '__name__':
                items = [x for x in items if field in x[1]]
        else:
            items.sort(key=lambda x: x[0])
        if self._after is not None:
            ids = [i for i, _ in items]
            items = items[ids.index(self._after) + 1:] if self._after in ids else items
        if self._limit is not None:
            items = items[:self._limit]
        self._db.reads += len(items)
        return iter([Snapshot(DocRef(self._db, self._coll, i), dict(d)) for i, d in items])

    def get(self, timeout=None):
        return list(self.stream())


class Collection(Query):
    def document(self, doc_id=None):
        return DocRef(self._db, self._coll, str(doc_id) if doc_id is not None else f"auto{next(_ids):08d}")

    def add(self, data):
        ref = self.document()
        ref.set(data)
        return None, ref


class Batch:
    def __init__(self):
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append(lambda: ref.set(data, merge=merge))

    def update(self, ref, data):
        self._ops.append(lambda: ref.update(data))

    def delete(self, ref):
        self._ops.append(ref.delete)

    def commit(self):
        for op in self._ops:
            op()
        self._ops = []


class FakeFirestore:
    def __init__(self):
        self._data = {}
        self.reads = 0

    def collection(self, name):
        return Collection(self, name)

    def batch(self):
        return Batch()

    def docs(self, coll):
        return self._data.get(coll, {})
