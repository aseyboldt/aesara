from __future__ import (division, absolute_import, print_function)
import os
import theano.tensor as T
from theano import config
from theano import gof
from theano.gof import local_optimizer
from theano.tensor.opt import register_canonicalize
from theano.tensor.opt import register_stabilize
from theano.tensor.extra_ops import cpu_contiguous
from theano.gradient import grad_undefined

ctc_enabled = config.ctc.enabled


class ConnectionistTemporalClassification(gof.COp, gof.OpenMPOp):
    __props__ = ('compute_grad',)

    func_file = "./ctc_wrapper.c"
    func_name = "APPLY_SPECIFIC(ctc_cost_cpu)"

    def __init__(self, compute_grad=True):
        if not compute_grad:
            self.func_name = "APPLY_SPECIFIC(ctc_cost_cpu_no_grad)"

        gof.COp.__init__(self, self.func_file, self.func_name)
        gof.OpenMPOp.__init__(self)

        self.compute_grad = compute_grad
        self.costs = T.fvector(name="ctc_cost")
        if self.compute_grad:
            self.gradients = T.ftensor3(name="ctc_grad")

        if config.ctc.root == "":
            raise ValueError('ctc.root variable is not set, please set it '
                             'to the root directory of the CTC library in '
                             'your system.')

    def c_lib_dirs(self):
        dirs = []
        if ctc_enabled:
            # We assume here that the compiled library (libwarpctc.so) is available
            # at the build directory of the CTC root directory.
            dirs.append(os.path.join(config.ctc.root, "build"))
        return dirs

    def c_libraries(self):
        return ["warpctc"]

    def c_header_dirs(self):
        dirs = []
        if ctc_enabled:
            # We assume here that the header is available at the include directory
            # of the CTC root directory.
            dirs.append(os.path.join(config.ctc.root, "include"))
        return dirs

    def c_compile_args(self):
        return gof.OpenMPOp.c_compile_args(self)

    def c_headers(self):
        return ["ctc.h"] + gof.OpenMPOp.c_headers(self)

    def make_node(self, activations, labels, input_lengths):
        if not ctc_enabled:
            raise RuntimeError('Baidu CTC is not enabled and '
                               'ConnectionistTemporalClassification Op '
                               'can not be constructed.')
        t_activations = T.as_tensor_variable(activations)
        # Ensure activations array is C-contiguous
        t_activations = cpu_contiguous(t_activations)

        t_labels = T.as_tensor_variable(labels)
        t_input_lengths = T.as_tensor_variable(input_lengths)

        if t_activations.type.dtype != 'float32':
            raise TypeError('Activations must use the float32 type!')

        if t_labels.type.dtype != 'int32':
            raise TypeError('Labels must use the int32 type!')

        if t_input_lengths.type.dtype != 'int32':
            raise TypeError('Label lengths must use the int32 type!')

        # Return only the cost. Gradient will be returned by grad()
        self.default_output = 0

        return gof.Apply(self, inputs=[t_activations, t_labels, t_input_lengths],
                         outputs=[self.costs, self.gradients])

    def grad(self, inputs, output_grads):
        if not ctc_enabled:
            raise RuntimeError('Baidu CTC is not enabled and '
                               'ConnectionistTemporalClassification Op '
                               'can not be constructed.')
        return [self.gradients,
                grad_undefined(self, 1, inputs[1]),
                grad_undefined(self, 2, inputs[2])]


def ctc(activations, labels, input_lengths):
    return ConnectionistTemporalClassification()(activations, labels, input_lengths)


# Disable gradient computation if not needed
@register_canonicalize
@register_stabilize
@local_optimizer([ConnectionistTemporalClassification])
def local_ConnectionistTemporalClassification_no_grad(node):
    if isinstance(node.op, ConnectionistTemporalClassification):
        if len(node.outputs) > 1:
            if len(node.outputs[1].clients) == 0:   # gradient is not used
                node.op = ConnectionistTemporalClassification(compute_grad=False)
                node.outputs = node.outputs[:1]   # costs only
