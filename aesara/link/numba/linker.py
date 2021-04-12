import ast
import uuid

from aesara.graph.basic import Constant

from .dispatch import make_numba_thunk
from .naming import NameFactory, unique_name_for_apply


def create_storage_map(fgraph, order):
    storage_map = {}
    constants = {}

    input_storage = [unique_name_for_apply(input) for input in fgraph.inputs]

    for node, name in zip(fgraph.inputs, input_storage):
        storage_map[node] = name

    for node in order:
        for r in node.inputs:
            if r not in storage_map:
                assert isinstance(r, Constant)
                name = unique_name_for_apply(r)
                constants[name] = r.data
                storage_map[r] = name
        for r in node.outputs:
            assert r not in storage_map
            storage_map[r] = unique_name_for_apply(r)

    output_storage = [storage_map[r] for r in fgraph.outputs]

    return input_storage, output_storage, constants, storage_map


def compile_graph(graph, order=None, *, debug=False):
    if order is None:
        order = graph.toposort()

    with NameFactory():
        input_storage, output_storage, constants, storage_map = create_storage_map(
            graph, order
        )

        builder = AstBuilder()
        run_func = builder.make_njit_function_def("run_graph", input_storage)

        global_vars = {}
        global_vars.update(constants)
        for node in order:
            thunk = make_numba_thunk(node.op, node, storage_map)
            global_vars.update(thunk._global_vars)
            run_func.body.extend(thunk._call_ast)

        run_func.body.append(
            ast.Return(
                value=ast.Tuple(
                    elts=[
                        ast.Name(id=output, ctx=ast.Load()) for output in output_storage
                    ],
                    ctx=ast.Load(),
                )
            )
        )

        mod = builder.wrap_in_module(["numba"], [run_func])

        # TODO name must be globally unique
        run_graph = builder.compile(
            mod,
            f"aesara_function_{uuid.uuid4().bytes[:6]}",
            globals=global_vars,
            debug=debug,
        )

    return run_graph


class AstBuilder:
    def wrap_in_module(self, imports, funcs):
        mod = ast.Module(body=[], type_ignores=[])
        body = mod.body

        body.extend(
            ast.Import(names=[ast.alias(name=name, asname=None)]) for name in imports
        )
        body.extend(funcs)
        return mod

    def make_njit_function_def(self, name, input_names):
        run_func = ast.FunctionDef(
            name=name,
            args=ast.arguments(
                args=[
                    ast.arg(arg=input, annotation=None, type_comment=None)
                    for input in input_names
                ],
                posonlyargs=[],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
                vararg=None,
                kwarg=None,
            ),
            decorator_list=[
                ast.Attribute(
                    value=ast.Name(id="numba", ctx=ast.Load()),
                    attr="njit",
                    ctx=ast.Load(),
                )
            ],
            returns=None,
            type_comment=None,
            body=[],
        )

        return run_func

    def compile(self, mod, module_name, globals, *, debug=True):
        mod = ast.fix_missing_locations(mod)
        if debug:
            from tempfile import NamedTemporaryFile

            import astor

            mod_src = astor.to_source(mod)

            with NamedTemporaryFile(delete=False) as f:
                filename = f.name
                f.write(mod_src.encode())

            # Make sure line numbers match source
            mod = ast.parse(mod_src)
        else:
            filename = "<meta>"

        globals = globals.copy()
        exec(compile(mod, filename, "exec"), globals)

        return globals["run_graph"]
