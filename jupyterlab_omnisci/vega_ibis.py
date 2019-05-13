"""
Functionality for server-side ibis transforms of vega charts.
"""

import copy
import typing
import warnings
import pathlib

import ibis
import altair
import pandas
from ipykernel.comm import Comm
from IPython import get_ipython

__all__: typing.List[str] = []


_expr_map: typing.Dict[str, ibis.Expr] = {}

# New Vega Lite renderer mimetype which can process ibis expressions in names
MIMETYPE = "application/vnd.vega.ibis.v5+json"

EMPTY_VEGA = {
    "$schema": "https://vega.github.io/schema/vega/v5.json",
    "description": "An empty vega v5 spec",
    "width": 500,
    "height": 200,
    "padding": 5,
    "autosize": "pad",
    "signals": [],
    "data": [],
    "scales": [],
    "projections": [],
    "axes": [],
    "legends": [],
    "marks": [],
}


def empty(expr: ibis.Expr) -> pandas.DataFrame:
    """
    Creates an empty DF for a ibis expression, based on the schema

    https://github.com/ibis-project/ibis/issues/1676#issuecomment-441472528
    """
    return expr.schema().apply_to(pandas.DataFrame(columns=expr.columns))


def monkeypatch_altair():
    """
    Needed until https://github.com/altair-viz/altair/issues/843 is fixed to let Altair
    handle ibis inputs
    """
    original_chart_init = altair.Chart.__init__

    def updated_chart_init(self, data=None, *args, **kwargs):
        """
        If user passes in a Ibis expression, create an empty dataframe with
        those types and set the `ibis` attribute to the original ibis expression.
        """
        if data is not None and isinstance(data, ibis.Expr):
            expr = data
            data = empty(expr)
            data.ibis = expr

        return original_chart_init(self, data=data, *args, **kwargs)

    altair.Chart.__init__ = updated_chart_init


monkeypatch_altair()


def altair_data_transformer(data):
    """
    turn a pandas DF with the Ibis query that made it attached to it into
    a valid Vega Lite data dict. Since this has to be JSON serializiable
    (because of how Altair is set up), we create a unique name and
    save the ibis expression globally with that name so we can pick it up later.
    """
    assert isinstance(data, pandas.DataFrame)
    expr = data.ibis
    name = str(hash(expr))
    _expr_map[name] = expr
    return {"name": name}


def altair_renderer(spec):
    return {MIMETYPE: spec}


altair.data_transformers.register("ibis", altair_data_transformer)
altair.renderers.register("ibis", altair_renderer)


def compiler_target_function(comm, msg):
    updated_spec = _transform(msg["content"]["data"])
    comm.send(updated_spec)


get_ipython().kernel.comm_manager.register_target(
    "jupyterlab-omnisci:vega-compiler", compiler_target_function
)


def query_target_func(comm, msg):
    # These are the paramaters passed to the vega transform
    parameters = msg["content"]["data"]

    expr_name: str = parameters.pop("name")
    expr = _expr_map[expr_name]
    data = expr.execute()
    comm.send(altair.to_values(data)["values"])


get_ipython().kernel.comm_manager.register_target("queryibis", query_target_func)


def _transform(spec: typing.Dict[str, typing.Any]):
    new = copy.deepcopy(spec)
    for data in new["data"]:
        name = data.get("name")
        if name and _expr_map.get(name) is not None:
            data["transform"] = [{"type": "queryibis", "name": name}]
    return new
