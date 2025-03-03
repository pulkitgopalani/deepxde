from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np

from .nn import NN
from .. import activations
from .. import initializers
from .. import regularizers
from ... import config
from ...backend import tf
from ...utils import timing


class DeepONet(NN):
    """Deep operator network.

    `Lu et al. Learning nonlinear operators via DeepONet based on the universal
    approximation theorem of operators. Nat Mach Intell, 2021.
    <https://doi.org/10.1038/s42256-021-00302-5>`_

    Args:
        layer_sizes_branch: A list of integers as the width of a fully connected
            network, or `(dim, f)` where `dim` is the input dimension and `f` is a
            network function. The width of the last layer in the branch and trunk net
            should be equal.
        layer_sizes_trunk (list): A list of integers as the width of a fully connected
            network.
        activation: If `activation` is a ``string``, then the same activation is used in
            both trunk and branch nets. If `activation` is a ``dict``, then the trunk
            net uses the activation `activation["trunk"]`, and the branch net uses
            `activation["branch"]`.
        trainable_branch: Boolean.
        trainable_trunk: Boolean or a list of booleans.
    """

    def __init__(
        self,
        layer_sizes_branch,
        layer_sizes_trunk,
        activation,
        kernel_initializer,
        regularization=None,
        use_bias=True,
        stacked=False,
        trainable_branch=True,
        trainable_trunk=True,
    ):
        super(DeepONet, self).__init__()
        if isinstance(trainable_trunk, (list, tuple)):
            if len(trainable_trunk) != len(layer_sizes_trunk) - 1:
                raise ValueError("trainable_trunk does not match layer_size_trunk.")

        self.layer_size_func = layer_sizes_branch
        self.layer_size_loc = layer_sizes_trunk
        if isinstance(activation, dict):
            self.activation_branch = activations.get(activation["branch"])
            self.activation_trunk = activations.get(activation["trunk"])
        elif activation == 'abs':
            self.activation_branch = self.activation_trunk = activation
        else:
            self.activation_branch = self.activation_trunk = activations.get(activation)
        self.kernel_initializer = initializers.get(kernel_initializer)
        if stacked:
            self.kernel_initializer_stacked = initializers.get(
                kernel_initializer + "stacked"
            )
        self.regularizer = regularizers.get(regularization)
        self.use_bias = use_bias
        self.stacked = stacked
        self.trainable_branch = trainable_branch
        self.trainable_trunk = trainable_trunk

        self._inputs = None
        self._X_func_default = None

    @property
    def inputs(self):
        return self._inputs

    @inputs.setter
    def inputs(self, value):
        if value[1] is not None:
            raise ValueError("DeepONet does not support setting trunk net input.")
        self._X_func_default = value[0]
        self._inputs = self.X_loc

    @property
    def outputs(self):
        return self.y

    @property
    def targets(self):
        return self.target

    def _feed_dict_inputs(self, inputs):
        if not isinstance(inputs, (list, tuple)):
            n = len(inputs)
            inputs = [np.tile(self._X_func_default, (n, 1)), inputs]
        return dict(zip([self.X_func, self.X_loc], inputs))

    @timing
    def build(self):
        print("Building DeepONet...")
        self.X_func = tf.placeholder(config.real(tf), [None, self.layer_size_func[0]])
        self.X_loc = tf.placeholder(config.real(tf), [None, self.layer_size_loc[0]])
        self._inputs = [self.X_func, self.X_loc]

        # Branch net to encode the input function
        y_func = self.X_func
        if callable(self.layer_size_func[1]):
            # User-defined network
            y_func = self.layer_size_func[1](y_func)
        elif self.stacked:
            # Stacked fully connected network
            stack_size = self.layer_size_func[-1]
            for i in range(1, len(self.layer_size_func) - 1):
                y_func = self._stacked_dense(
                    y_func,
                    self.layer_size_func[i],
                    stack_size,
                    activation=self.activation_branch,
                    trainable=self.trainable_branch,
                )
            y_func = self._stacked_dense(
                y_func,
                1,
                stack_size,
                use_bias=self.use_bias,
                trainable=self.trainable_branch,
            )
        else:
            # Unstacked fully connected network
            if self.activation_branch == 'abs':
                for i in range(1, len(self.layer_size_func) - 1):
                    y_func = self._dense(
                        y_func,
                        self.layer_size_func[i],
                        use_bias=self.use_bias,
                        regularizer=self.regularizer,
                        trainable=self.trainable_branch,
                    )
                    y_func = tf.abs(y_func)
                y_func = self._dense(
                    y_func,
                    self.layer_size_func[-1],
                    use_bias=self.use_bias,
                    regularizer=self.regularizer,
                    trainable=self.trainable_branch,
                )    
            
            else:
                for i in range(1, len(self.layer_size_func) - 1):
                    y_func = self._dense(
                        y_func,
                        self.layer_size_func[i],
                        use_bias=self.use_bias,
                        activation=self.activation_branch,
                        regularizer=self.regularizer,
                        trainable=self.trainable_branch,
                    )
                y_func = self._dense(
                    y_func,
                    self.layer_size_func[-1],
                    use_bias=self.use_bias,
                    regularizer=self.regularizer,
                    trainable=self.trainable_branch,
                )

        # Trunk net to encode the domain of the output function
        y_loc = self.X_loc
        # if self._input_transform is not None:
        #     y_loc = self._input_transform(y_loc)

        if self.activation_trunk == 'abs':
            for i in range(1, len(self.layer_size_loc)-1):
                y_loc = self._dense(
                    y_loc,
                    self.layer_size_loc[i],
                    regularizer=self.regularizer,
                    trainable=self.trainable_trunk[i - 1]
                    if isinstance(self.trainable_trunk, (list, tuple))
                    else self.trainable_trunk,
                )
                y_loc = tf.abs(y_loc)
            y_loc = self._dense(
                    y_loc,
                    self.layer_size_loc[-1],
                    regularizer=self.regularizer,
                    trainable=self.trainable_trunk[-1]
                    if isinstance(self.trainable_trunk, (list, tuple))
                    else self.trainable_trunk,
                )
        else:
            for i in range(1, len(self.layer_size_loc)-1):
                y_loc = self._dense(
                    y_loc,
                    self.layer_size_loc[i],
                    use_bias=self.use_bias,
                    activation=self.activation_trunk,
                    regularizer=self.regularizer,
                    trainable=self.trainable_trunk[i - 1]
                    if isinstance(self.trainable_trunk, (list, tuple))
                    else self.trainable_trunk,
                )
            y_loc = self._dense(
                    y_loc,
                    self.layer_size_loc[-1],
                    use_bias=self.use_bias,
                    regularizer=self.regularizer,
                    trainable=self.trainable_trunk[-1]
                    if isinstance(self.trainable_trunk, (list, tuple))
                    else self.trainable_trunk,
                )

        # Dot product
        if y_func.shape[-1] != y_loc.shape[-1]:
            raise AssertionError(
                "Output sizes of branch net and trunk net do not match."
            )
        self.y = tf.einsum("bi,bi->b", y_func, y_loc)
        self.y = tf.expand_dims(self.y, axis=1)
        # Add bias
        if self.use_bias:
            b = tf.Variable(tf.zeros(1))
            self.y += b

        # if self._output_transform is not None:
        #     self.y = self._output_transform(self._inputs, self.y)

        self.target = tf.placeholder(config.real(tf), [None, 1])
        self.built = True

    def _dense(
        self,
        inputs,
        units,
        activation=None,
        use_bias=False,
        regularizer=None,
        trainable=True,
    ):
        return tf.layers.dense(
            inputs,
            units,
            activation=activation,
            use_bias=use_bias,
            kernel_initializer=self.kernel_initializer,
            kernel_regularizer=regularizer,
            trainable=trainable,
        )

    def _stacked_dense(
        self, inputs, units, stack_size, activation=None, use_bias=True, trainable=True
    ):
        """Stacked densely-connected NN layer.

        Args:
            inputs: If inputs is the NN input, then it is a 2D tensor with shape:
                `(batch_size, input_dim)`; otherwise, it is 3D tensor with shape:
                `(batch_size, stack_size, input_dim)`.

        Returns:
            tensor: outputs.

            If outputs is the NN output, i.e., units = 1,
            2D tensor with shape: `(batch_size, stack_size)`;
            otherwise, 3D tensor with shape: `(batch_size, stack_size, units)`.
        """
        shape = inputs.shape
        input_dim = shape[-1]
        if len(shape) == 2:
            # NN input layer
            W = tf.Variable(
                self.kernel_initializer_stacked([stack_size, input_dim, units]),
                trainable=trainable,
            )
            outputs = tf.einsum("bi,nij->bnj", inputs, W)
        elif units == 1:
            # NN output layer
            W = tf.Variable(
                self.kernel_initializer_stacked([stack_size, input_dim]),
                trainable=trainable,
            )
            outputs = tf.einsum("bni,ni->bn", inputs, W)
        else:
            W = tf.Variable(
                self.kernel_initializer_stacked([stack_size, input_dim, units]),
                trainable=trainable,
            )
            outputs = tf.einsum("bni,nij->bnj", inputs, W)
        if use_bias:
            if units == 1:
                # NN output layer
                b = tf.Variable(tf.zeros(stack_size), trainable=trainable)
            else:
                b = tf.Variable(tf.zeros([stack_size, units]), trainable=trainable)
            outputs += b
        if activation is not None:
            return activation(outputs)
        return outputs


class DeepONetCartesianProd(NN):
    """Deep operator network for dataset in the format of Cartesian product.

    Args:
        layer_size_branch: A list of integers as the width of a fully connected network,
            or `(dim, f)` where `dim` is the input dimension and `f` is a network
            function. The width of the last layer in the branch and trunk net should be
            equal.
        layer_size_trunk (list): A list of integers as the width of a fully connected
            network.
        activation: If `activation` is a ``string``, then the same activation is used in
            both trunk and branch nets. If `activation` is a ``dict``, then the trunk
            net uses the activation `activation["trunk"]`, and the branch net uses
            `activation["branch"]`.
    """

    def __init__(
        self,
        layer_size_branch,
        layer_size_trunk,
        activation,
        kernel_initializer,
        regularization=None,
    ):
        super(DeepONetCartesianProd, self).__init__()
        self.layer_size_func = layer_size_branch
        self.layer_size_loc = layer_size_trunk
        if isinstance(activation, dict):
            self.activation_branch = activations.get(activation["branch"])
            self.activation_trunk = activations.get(activation["trunk"])
        else:
            self.activation_branch = self.activation_trunk = activations.get(activation)
        self.kernel_initializer = initializers.get(kernel_initializer)
        self.regularizer = regularizers.get(regularization)

        self._inputs = None

    @property
    def inputs(self):
        return self._inputs

    @property
    def outputs(self):
        return self.y

    @property
    def targets(self):
        return self.target

    @timing
    def build(self):
        print("Building DeepONetCartesianProd...")
        self.X_func = tf.placeholder(config.real(tf), [None, self.layer_size_func[0]])
        self.X_loc = tf.placeholder(config.real(tf), [None, self.layer_size_loc[0]])
        self._inputs = [self.X_func, self.X_loc]

        # Branch net to encode the input function
        y_func = self.X_func
        if callable(self.layer_size_func[1]):
            # User-defined network
            y_func = self.layer_size_func[1](y_func)
        else:
            # Fully connected network
            for i in range(1, len(self.layer_size_func) - 1):
                y_func = tf.layers.dense(
                    y_func,
                    self.layer_size_func[i],
                    activation=self.activation_branch,
                    kernel_initializer=self.kernel_initializer,
                    kernel_regularizer=self.regularizer,
                )
            y_func = tf.layers.dense(
                y_func,
                self.layer_size_func[-1],
                kernel_initializer=self.kernel_initializer,
                kernel_regularizer=self.regularizer,
            )

        # Trunk net to encode the domain of the output function
        y_loc = self.X_loc
        if self._input_transform is not None:
            y_loc = self._input_transform(y_loc)
        for i in range(1, len(self.layer_size_loc)):
            y_loc = tf.layers.dense(
                y_loc,
                self.layer_size_loc[i],
                activation=self.activation_trunk,
                kernel_initializer=self.kernel_initializer,
                kernel_regularizer=self.regularizer,
            )

        # Dot product
        if y_func.shape[-1] != y_loc.shape[-1]:
            raise AssertionError(
                "Output sizes of branch net and trunk net do not match."
            )
        self.y = tf.einsum("bi,ni->bn", y_func, y_loc)
        # Add bias
        b = tf.Variable(tf.zeros(1))
        self.y += b

        if self._output_transform is not None:
            self.y = self._output_transform(self._inputs, self.y)

        self.target = tf.placeholder(config.real(tf), [None, None])
        self.built = True


class FourierDeepONetCartesianProd(DeepONetCartesianProd):
    """Deep operator network with a Fourier trunk net for dataset in the format of
    Cartesian product.

    There are two pairs of trunk and branch nets. One pair is the vanilla DeepONet, and
    the other one uses Fourier basis as the trunk net. Because the dataset is in the
    format of Cartesian product, the Fourier branch-trunk nets are implemented via the
    inverse FFT.

    Args:
        layer_size_Fourier_branch: A list of integers as the width of a fully connected
            network, or `(dim, f)` where `dim` is the input dimension and `f` is a
            network function.
        output_shape (tuple[int]): Shape of the output.
    """

    def __init__(
        self,
        layer_size_Fourier_branch,
        output_shape,
        layer_size_branch,
        layer_size_trunk,
        activation,
        kernel_initializer,
        regularization=None,
    ):
        super(FourierDeepONetCartesianProd, self).__init__(
            layer_size_branch,
            layer_size_trunk,
            activation,
            kernel_initializer,
            regularization=regularization,
        )
        self.layer_size_Fourier = layer_size_Fourier_branch
        self.output_shape = output_shape

    @timing
    def build(self):
        print("Building FourierDeepONetCartesianProd...")
        output_transform = self._output_transform
        self._output_transform = None
        super(FourierDeepONetCartesianProd, self).build()

        # Branch net for the Fourier trunk net
        y_func = self.X_func
        if callable(self.layer_size_Fourier[1]):
            # User-defined network
            y_func = self.layer_size_Fourier[1](y_func)
        else:
            # Fully connected network
            for i in range(1, len(self.layer_size_Fourier) - 1):
                y_func = tf.layers.dense(
                    y_func,
                    self.layer_size_Fourier[i],
                    activation=self.activation_branch,
                    kernel_initializer=self.kernel_initializer,
                    kernel_regularizer=self.regularizer,
                )
            y_func = tf.layers.dense(
                y_func,
                self.layer_size_Fourier[-1],
                kernel_initializer=self.kernel_initializer,
                kernel_regularizer=self.regularizer,
            )

        if self.layer_size_loc[0] == 1:
            # Inverse 1D FFT
            # 1D branch output
            modes = self.layer_size_Fourier[-1] // 2
            y_func = tf.dtypes.complex(y_func[:, :modes], y_func[:, modes:])
            y = tf.signal.irfft(y_func, fft_length=self.output_shape)
        elif self.layer_size_loc[0] == 2:
            # Inverse 2D FFT
            s = y_func.shape
            if len(s) == 2:
                # 1D branch output
                modes = s[-1] // 2
                y_func = tf.dtypes.complex(y_func[:, :modes], y_func[:, modes:])
                # TODO: Need a better way to determine the modes size
                # Case 1
                # modes1, modes2 = 24, 12
                # Case 2
                # modes1 = self.output_shape[0]
                # if modes % modes1 != 0:
                #     raise AssertionError("Fourier branch-trunk nets do not match.")
                # modes2 = modes // modes1
                # Case 3
                modes2 = self.output_shape[1] // 2 + 1
                if modes % modes2 != 0:
                    raise AssertionError("Fourier branch-trunk nets do not match.")
                modes1 = modes // modes2
                y_func = tf.keras.layers.Reshape((modes1, modes2))(y_func)
            elif len(s) == 4:
                # 3D branch output (H, W, C=2)
                if s[-1] != 2:
                    raise AssertionError(
                        "The channel number of Fourier branch net output is not 2."
                    )
                y_func = tf.dtypes.complex(y_func[:, :, :, 0], y_func[:, :, :, 1])
            y = tf.signal.irfft2d(y_func, fft_length=self.output_shape)
            y = tf.keras.layers.Flatten()(y)

        self.y += y

        self._output_transform = output_transform
        if self._output_transform is not None:
            self.y = self._output_transform(self._inputs, self.y)
