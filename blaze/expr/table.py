""" An abstract Table

>>> accounts = TableSymbol('accounts', '{name: string, amount: int}')
>>> deadbeats = accounts['name'][accounts['amount'] < 0]
"""
from __future__ import absolute_import, division, print_function

from datashape import dshape, var, DataShape, Record, isdimension
import datashape
import operator
from toolz import concat, partial, first, pipe
from toolz.curried import filter
from . import scalar
from .core import Expr, Scalar
from .scalar import ScalarSymbol, NumberSymbol
from .scalar import *
from ..utils import unique
from ..compatibility import _strtypes


class TableExpr(Expr):
    """ Super class for all Table Expressions """
    @property
    def dshape(self):
        return datashape.var * self.schema

    @property
    def columns(self):
        return self.schema[0].names

    @property
    def dtype(self):
        ds = self.schema[-1]
        if isinstance(ds, Record):
            if len(ds.fields.values()) > 1:
                raise TypeError("`.dtype` not defined for multicolumn object. "
                                "Use `.schema` instead")
            else:
                return dshape(ds.fields.values()[0])
        else:
            return dshape(ds)

    def __getitem__(self, key):
        if isinstance(key, _strtypes):
            if key not in self.columns:
                raise ValueError("Mismatched Column: %s" % str(key))
            return Column(self, key)
        if isinstance(key, list) and all(isinstance(k, _strtypes) for k in key):
            key = tuple(key)
            if not all(col in self.columns for col in key):
                raise ValueError("Mismatched Columns: %s" % str(key))
            return Projection(self, tuple(key))
        if isinstance(key, TableExpr):
            return Selection(self, key)
        raise TypeError("Did not understand input: %s[%s]" % (self, key))

    def sort(self, key=None, ascending=True):
        """ Sort table

        Parameters
        ----------
        key: string, list of strings, TableExpr
            Defines by what you want to sort.  Either:
                A single column string, ``t.sort('amount')``
                A list of column strings, ``t.sort(['name', 'amount'])``
                A Table Expression, ``t.sort(-t['amount'])``
        ascending: bool
            Determines order of the sort
        """
        if key is None:
            key = self.columns[0]
        return Sort(self, key, ascending)

    def head(self, n=10):
        return Head(self, n)

    def relabel(self, labels):
        return ReLabel(self, labels)

    def map(self, func, schema=None):
        return Map(self, func, schema)


class TableSymbol(TableExpr):
    """ A Symbol for Tabular data

    This is a leaf in the expression tree

    >>> accounts = TableSymbol('accounts',
    ...                        '{name: string, amount: int, id: int}')
    >>> accounts['amount'] + 1
    accounts['amount'] + 1

    We define a TableSymbol with a name like ``accounts`` and the datashape of
    a single row, called a schema.
    """
    __slots__ = 'name', 'schema'

    def __init__(self, name, schema):
        self.name = name
        self.schema = dshape(schema)

    def __str__(self):
        return self.name


class Projection(TableExpr):
    """

    SELECT a, b, c
    FROM table
    """
    __slots__ = 'parent', '_columns'

    def __init__(self, table, columns):
        self.parent = table
        self._columns = tuple(columns)

    @property
    def columns(self):
        return self._columns

    @property
    def schema(self):
        d = self.parent.schema[0].fields
        return DataShape(Record([(col, d[col]) for col in self.columns]))

    def __str__(self):
        return '%s[[%s]]' % (self.parent,
                             ', '.join(["'%s'" % col for col in self.columns]))


class ColumnSyntaxMixin(object):
    def __eq__(self, other):
        return columnwise(Eq, self, other)

    def __lt__(self, other):
        return columnwise(LT, self, other)

    def __gt__(self, other):
        return columnwise(GT, self, other)

    def __add__(self, other):
        return columnwise(Add, self, other)

    def __radd__(self, other):
        return columnwise(Add, other, self)

    def __mul__(self, other):
        return columnwise(Mul, self, other)

    def __rmul__(self, other):
        return columnwise(Mul, other, self)

    def __div__(self, other):
        return columnwise(Div, self, other)

    def __rdiv__(self, other):
        return columnwise(Div, other, self)

    def __sub_(self, other):
        return columnwise(Sub, self, other)

    def __rsub__(self, other):
        return columnwise(Sub, other, self)

    def __pow__(self, other):
        return columnwise(Pow, self, other)

    def __rpow__(self, other):
        return columnwise(Pow, other, self)

    def __mod__(self, other):
        return columnwise(Mod, self, other)

    def __rmod__(self, other):
        return columnwise(Mod, other, self)

    def label(self, label):
        return Label(self, label)

    def count(self):
        return count(self)

    def distinct(self):
        return Distinct(self)

    def nunique(self):
        return nunique(self)

    def sum(self):
        return sum(self)

    def min(self):
        return min(self)

    def max(self):
        return max(self)

    def any(self):
        return any(self)

    def all(self):
        return all(self)

    def mean(self):
        return mean(self)

    def var(self):
        return var(self)

    def std(self):
        return std(self)


class Column(ColumnSyntaxMixin, Projection):
    """

    SELECT a
    FROM table
    """
    __slots__ = 'parent', 'column'

    __hash__ = Expr.__hash__

    def __init__(self, table, column):
        self.parent = table
        self.column = column

    @property
    def columns(self):
        return (self.column,)

    def __str__(self):
        return "%s['%s']" % (self.parent, self.columns[0])


class Selection(TableExpr):
    """
    WHERE a op b
    """
    __slots__ = 'parent', 'predicate'

    def __init__(self, table, predicate):
        if predicate.dtype != dshape('bool'):
            raise TypeError("Must select over a boolean predicate.  Got:\n"
                            "%s[%s]" % (table, predicate))
        self.parent = table
        self.predicate = predicate  # A Relational

    def __str__(self):
        return "%s[%s]" % (self.parent, self.predicate)

    @property
    def schema(self):
        return self.parent.schema


def _index(t, element):
    """ tuple.args fails when == is overloaded.  This is a hacky fix

    Long term we shouldn't use == in Exprs.  We should use it only in the user
    interface layer
    """
    for i, item in enumerate(t):
        if isinstance(item, TableExpr) and isinstance(element, TableExpr):
            eq = TableExpr.isidentical
        else:
            eq = lambda x, y: x is y
        if eq(item, element):
            return i

    raise IndexError("Could not find %s in %s" % (element, t))



def columnwise(op, *column_inputs):
    """ Merge columns with op

    *expr :: ScalarExpr
    args :: (Column, base)

    """
    expr_inputs = []
    parents = set()
    for col in column_inputs:
        if isinstance(col, ColumnWise):
            expr_inputs.append(col.expr)
            parents.add(col.parent)
        elif isinstance(col, Column):
            # TODO: specify dtype
            expr_inputs.append(NumberSymbol(col.columns[0]))
            parents.add(col.parent)
        else:
            # maybe something like 5 or 'Alice'
            expr_inputs.append(col)

    if not len(parents) == 1:
        raise ValueError("All inputs must be from same Table.\n"
                         "Saw the following tables: %s"
                         % ', '.join(map(str, parents)))

    expr = op(*expr_inputs)
    return ColumnWise(first(parents), expr)


class ColumnWise(TableExpr, ColumnSyntaxMixin):
    """

    a op b
    """
    __slots__ = 'parent', 'expr'
    def __init__(self, parent, expr):
        self.parent = parent
        self.expr = expr

    __hash__ = Expr.__hash__

    @property
    def schema(self):
        return self.expr.dshape

    def __str__(self):
        columns = self.active_columns()
        newcol = lambda c: "%s['%s']" % (self.parent, c)
        return eval_str(self.expr.subs(dict(zip(columns,
                                                map(newcol, columns)))))

    def active_columns(self):
        return sorted(unique(x.name for x in self.traverse()
                                    if isinstance(x, ScalarSymbol)))


class Join(TableExpr):
    """ Join two tables on common columns

    Parameters
    ----------
    lhs : TableExpr
    rhs : TableExpr
    on_left : string
    on_right : string

    >>> names = TableSymbol('names', '{name: string, id: int}')
    >>> amounts = TableSymbol('amounts', '{amount: int, id: int}')

    Join tables based on shared column name
    >>> joined = Join(names, amounts, 'id')

    Join based on different column names
    >>> amounts = TableSymbol('amounts', '{amount: int, acctNumber: int}')
    >>> joined = Join(names, amounts, 'id', 'acctNumber')
    """
    __slots__ = 'lhs', 'rhs', 'on_left', 'on_right'

    def __init__(self, lhs, rhs, on_left, on_right=None):
        self.lhs = lhs
        self.rhs = rhs
        if not on_right:
            on_right = on_left
        self.on_left = on_left
        self.on_right = on_right
        if lhs.schema[0][on_left] != rhs.schema[0][on_right]:
            raise TypeError("Schema's of joining columns do not match")

    @property
    def schema(self):
        rec1 = self.lhs.schema[0]
        rec2 = self.rhs.schema[0]

        rec = rec1.parameters[0] + tuple((k, v) for k, v in rec2.parameters[0]
                                                 if  k != self.on_right)
        return dshape(Record(rec))


sin = partial(columnwise, scalar.sin)
cos = partial(columnwise, scalar.cos)
tan = partial(columnwise, scalar.tan)
exp = partial(columnwise, scalar.exp)
log = partial(columnwise, scalar.log)


class Reduction(Scalar):
    """ A column-wise reduction

    >>> t = TableSymbol('t', '{name: string, amount: int, id: int}')
    >>> e = t['amount'].sum()

    >>> data = [['Alice', 100, 1],
    ...         ['Bob', 200, 2],
    ...         ['Alice', 50, 3]]

    >>> from blaze.compute.python import compute
    >>> compute(e, data)
    350
    """
    __slots__ = 'parent',

    def __init__(self, table):
        self.parent = table

    @property
    def dshape(self):
        return self.parent.dshape.subarray(1)

    @property
    def symbol(self):
        return type(self).__name__


class any(Reduction): pass
class all(Reduction): pass
class sum(Reduction): pass
class max(Reduction): pass
class min(Reduction): pass
class mean(Reduction): pass
class var(Reduction): pass
class std(Reduction): pass
class count(Reduction): pass
class nunique(Reduction): pass


class By(TableExpr):
    """ Split-Apply-Combine Operator

    >>> t = TableSymbol('t', '{name: string, amount: int, id: int}')
    >>> e = By(t, t['name'], t['amount'].sum())

    >>> data = [['Alice', 100, 1],
    ...         ['Bob', 200, 2],
    ...         ['Alice', 50, 3]]

    >>> from blaze.compute.python import compute
    >>> compute(e, data) #doctest: +SKIP
    {'Alice': 150, 'Bob': 200}
    """

    __slots__ = 'parent', 'grouper', 'apply'

    def __init__(self, parent, grouper, apply):
        self.parent = parent
        s = TableSymbol('', parent.schema)
        self.grouper = grouper.subs({parent: s})
        self.apply = apply.subs({parent: s})
        if isdimension(self.apply.dshape[0]):
            raise TypeError("Expected Reduction")

    @property
    def schema(self):
        group = self.grouper.schema[0].parameters[0]
        if isinstance(self.apply.dshape[0], Record):
            apply = self.apply.dshape[0].parameters[0]
        else:
            apply = (('0', self.apply.dshape),)

        params = unique(group + apply, key=lambda x: x[0])

        return dshape(Record(list(params)))


class Sort(TableExpr):
    __slots__ = 'parent', 'column', 'ascending'

    def __init__(self, parent, column, ascending=True):
        self.parent = parent
        self.column = column
        self.ascending = ascending

    @property
    def schema(self):
        return self.parent.schema


class Distinct(TableExpr):
    """ Distinct elements filter

    >>> t = TableSymbol('t', '{name: string, amount: int, id: int}')
    >>> e = Distinct(t)

    >>> data = [('Alice', 100, 1),
    ...         ('Bob', 200, 2),
    ...         ('Alice', 100, 1)]

    >>> from blaze.compute.python import compute
    >>> sorted(compute(e, data))
    [('Alice', 100, 1), ('Bob', 200, 2)]
    """

    def __init__(self, table):
        self.parent = table

    @property
    def schema(self):
        return self.parent.schema

class Head(TableExpr):
    __slots__ = 'parent', 'n'

    def __init__(self, parent, n=10):
        self.parent = parent
        self.n = n

    @property
    def schema(self):
        return self.parent.schema

    @property
    def dshape(self):
        return self.n * self.schema


class Label(TableExpr, ColumnSyntaxMixin):
    __slots__ = 'parent', 'label'

    def __init__(self, parent, label):
        self.parent = parent
        self.label = label

    @property
    def schema(self):
        if isinstance(self.parent.schema[0], Record):
            dtype = list(self.parent.schema[0].fields.values()[0])
        else:
            dtype = list(self.parent.schema[0])
        return DataShape(Record([[self.label, dtype]]))


class ReLabel(TableExpr):
    __slots__ = 'parent', 'labels'

    def __init__(self, parent, labels):
        self.parent = parent
        if isinstance(labels, dict):  # Turn dict into tuples
            labels = tuple(sorted(labels.items()))
        self.labels = labels

    @property
    def schema(self):
        subs = dict(self.labels)
        d = self.parent.schema[0].fields

        return DataShape(Record([[subs.get(name, name), dtype]
            for name, dtype in self.parent.schema[0].parameters[0]]))


class Map(TableExpr):
    """ Map an arbitrary Python function across rows in a Table

    >>> from datetime import datetime

    >>> t = TableSymbol('t', '{price: real, time: int64}')  # times as integers
    >>> datetimes = t['time'].map(datetime.utcfromtimestamp)

    Optionally provide extra schema information

    >>> datetimes = t['time'].map(datetime.utcfromtimestamp,
    ...                           schema='{time: datetime}')

    See Also:
        Apply
    """
    __slots__ = 'parent', 'func', '_schema'

    def __init__(self, parent, func, schema=None):
        self.parent = parent
        self.func = func
        self._schema = schema

    @property
    def schema(self):
        if self._schema:
            return dshape(self._schema)
        else:
            raise NotImplementedError()


class Apply(TableExpr):
    """ Apply an arbitrary Python function onto a Table

    >>> t = TableSymbol('t', '{name: string, amount: int}')
    >>> h = Apply(hash, t)  # Hash value of resultant table

    Optionally provide extra datashape information

    >>> h = Apply(hash, t, dshape='real')

    Apply brings a function within the expression tree.
    The following transformation is often valid

    Before ``compute(Apply(f, expr), ...)``
    After  ``f(compute(expr, ...)``

    See Also:
        Map
    """
    __slots__ = 'parent', 'func', '_dshape'

    def __init__(self, func, parent, dshape=None):
        self.parent = parent
        self.func = func
        self._dshape = dshape

    @property
    def schema(self):
        if isdimension(self.dshape[0]):
            return self.dshape.subshape[0]
        else:
            return TypeError("Non-tabular datashape, %s" % self.dshape)

    @property
    def dshape(self):
        if self._dshape:
            return dshape(self._dshape)
        else:
            return NotImplementedError("Datashape of arbitrary Apply not defined")
