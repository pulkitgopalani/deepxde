from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math

import numpy as np

from .data import Data
from .. import array_ops
from .. import config
from ..backend import tf
from ..utils import run_if_all_none


class Discretization(object):
    """Space discretization scheme parameters.
    """

    def __init__(self, dim, meshtype, resolution, nanchor):
        self.dim = dim
        self.meshtype, self.resolution = meshtype, resolution
        self.nanchor = nanchor

        self._check()

    def _check(self):
        if self.meshtype not in ["static", "dynamic"]:
            raise ValueError("Wrong meshtype %s" % self.meshtype)
        if self.dim >= 2 and self.meshtype == "static":
            raise ValueError(
                "Do not support meshtype static for dimension %d" % self.dim
            )
        if self.dim != len(self.resolution):
            raise ValueError(
                "Resolution %s does not math dimension %d."
                % (self.resolution, self.dim)
            )


class FPDE(Data):
    """Fractional PDE solver.
    """

    def __init__(self, frac, alpha, func, geom, disc, batch_size=0, ntest=None):
        if disc.meshtype == "static" and geom.idstr != "Interval":
            raise ValueError("Only Interval supports static mesh.")

        self.frac, self.alpha, self.func, self.geom = frac, alpha, func, geom
        self.disc = disc

        self.batch_size = batch_size
        self.ntest = ntest

        self.nbc = disc.nanchor
        self.train_x, self.train_y, self.frac_train = None, None, None
        self.test_x, self.test_y, self.frac_test = None, None, None

    def losses(self, targets, outputs, loss, model):
        int_mat_train = self.get_int_matrix(self.batch_size, True)
        int_mat_test = self.get_int_matrix(self.ntest, False)
        f = tf.cond(
            tf.equal(model.net.data_id, 0),
            lambda: self.frac(
                model.net.inputs[self.nbc :], outputs[self.nbc :], int_mat_train
            ),
            lambda: self.frac(
                model.net.inputs[self.nbc :], outputs[self.nbc :], int_mat_test
            ),
        )
        l = [
            loss(targets[: self.nbc], outputs[: self.nbc]),
            loss(tf.zeros(tf.shape(f)), f),
        ]
        return l

    @run_if_all_none("train_x", "train_y")
    def train_next_batch(self, batch_size=None):
        self.train_x, self.train_y, self.frac_train = self.get_x(self.batch_size)
        return self.train_x, self.train_y

    @run_if_all_none("test_x", "test_y")
    def test(self):
        self.test_x, self.test_y, self.frac_test = self.get_x(self.ntest)
        return self.test_x, self.test_y

    def get_x(self, size):
        if self.disc.meshtype == "static":
            if size != self.disc.resolution[0] - 2 + self.disc.nanchor:
                raise ValueError("Mesh resolution does not match batch size.")
            discreteop = Fractional(self.alpha, self.geom, self.disc, None)
            x = discreteop.get_x()
            x = np.roll(x, len(x) - 1)
        elif self.disc.meshtype == "dynamic":
            # x = self.geom.random_points(size-self.disc.nanchor, 'sobol')
            x = self.geom.uniform_points(size - self.disc.nanchor, False)
            discreteop = Fractional(self.alpha, self.geom, self.disc, x)
            x = discreteop.get_x()
        if self.disc.nanchor > 0:
            x = np.vstack(
                (self.geom.random_boundary_points(self.disc.nanchor, "sobol"), x)
            )
        y = self.func(x)
        return x, y, discreteop

    def get_int_matrix(self, size, training):
        if training:
            if self.train_x is None:
                self.train_next_batch()
            int_mat = self.frac_train.get_matrix(True)
        else:
            if self.test_x is None:
                self.test()
            int_mat = self.frac_test.get_matrix(True)
        if self.disc.meshtype == "static":
            int_mat = np.roll(int_mat, int_mat.shape[1] - 1, axis=1)
            int_mat = int_mat[1:-1]
        return int_mat


class Fractional(object):
    """Fractional derivative.

    static:
        n: number of points
        x0: None
    dynamic:
        n: resolution lambda
        x0: not boundary points
    """

    def __init__(self, alpha, geom, disc, x0):
        if (disc.meshtype == "static" and x0 is not None) or (
            disc.meshtype == "dynamic" and x0 is None
        ):
            raise ValueError("Wrong inputs.")

        self.alpha, self.geom = alpha, geom
        self.disc, self.x0 = disc, x0
        if disc.meshtype == "dynamic":
            self._check_dynamic_stepsize()

        self.x, self.xindex_start, self.w = None, None, None
        self._w_init = self._init_weights()

    def _check_dynamic_stepsize(self):
        h = 1 / self.disc.resolution[-1]
        min_h = self.geom.mindist2boundary(self.x0)
        if min_h < h:
            print(
                "Warning: mesh step size %f is larger than the boundary distance %f."
                % (h, min_h)
            )

    def _init_weights(self):
        n = (
            self.disc.resolution[0]
            if self.disc.meshtype == "static"
            else self.dynamic_dist2npts(self.geom.diam) + 1
        )
        w = [1]
        for j in range(1, n):
            w.append(w[-1] * (j - 1 - self.alpha) / j)
        return array_ops.convert_to_array(w)

    def get_x(self):
        self.x = (
            self.get_x_static()
            if self.disc.meshtype == "static"
            else self.get_x_dynamic()
        )
        return self.x

    def get_matrix(self, sparse=False):
        return (
            self.get_matrix_static()
            if self.disc.meshtype == "static"
            else self.get_matrix_dynamic(sparse)
        )

    def get_x_static(self):
        return self.geom.uniform_points(self.disc.resolution[0], True)

    def dynamic_dist2npts(self, dx):
        return int(math.ceil(self.disc.resolution[-1] * dx))

    def get_x_dynamic(self):
        if any(map(self.geom.on_boundary, self.x0)):
            raise ValueError("Boundary points exist.")
        if self.geom.dim == 1:
            dirns, dirn_w = [-1, 1], [1, 1]
        elif self.geom.dim == 2:
            gauss_x, gauss_w = np.polynomial.legendre.leggauss(self.disc.resolution[0])
            thetas = np.pi * gauss_x + np.pi
            dirns = np.vstack((np.cos(thetas), np.sin(thetas))).T
            dirn_w = np.pi * gauss_w
        elif self.geom.dim == 3:
            gauss_x, gauss_w = np.polynomial.legendre.leggauss(
                max(self.disc.resolution[:2])
            )
            thetas = (np.pi * gauss_x[: self.disc.resolution[0]] + np.pi) / 2
            phis = np.pi * gauss_x[: self.disc.resolution[1]] + np.pi
            dirns, dirn_w = [], []
            for i in range(self.disc.resolution[0]):
                for j in range(self.disc.resolution[1]):
                    dirns.append(
                        [
                            np.sin(thetas[i]) * np.cos(phis[j]),
                            np.sin(thetas[i]) * np.sin(phis[j]),
                            np.cos(thetas[i]),
                        ]
                    )
                    dirn_w.append(gauss_w[i] * gauss_w[j] * np.sin(thetas[i]))
            dirn_w = np.pi ** 2 / 2 * np.array(dirn_w)
        x, self.w = [], []
        for x0i in self.x0:
            xi = list(
                map(
                    lambda dirn: self.geom.background_points(
                        x0i, dirn, self.dynamic_dist2npts, 0
                    ),
                    dirns,
                )
            )
            wi = list(
                map(
                    lambda i: dirn_w[i]
                    * np.linalg.norm(xi[i][1] - xi[i][0]) ** (-self.alpha)
                    * self.get_weight(len(xi[i]) - 1),
                    range(len(dirns)),
                )
            )
            # first order
            xi, wi = zip(*map(self.modify_first_order, xi, wi))
            # second order
            # xi, wi = zip(*map(self.modify_second_order, xi, wi))
            # third order
            # xi, wi = zip(*map(self.modify_third_order, xi, wi))
            x.append(np.vstack(xi))
            self.w.append(array_ops.hstack(wi))
        self.xindex_start = np.hstack(([0], np.cumsum(list(map(len, x))))) + len(
            self.x0
        )
        return np.vstack([self.x0] + x)

    def modify_first_order(self, x, w):
        x = np.vstack(([2 * x[0] - x[1]], x[:-1]))
        if not self.geom.inside(x[0]):
            return x[1:], w[1:]
        return x, w

    def modify_second_order(self, x=None, w=None):
        w0 = np.hstack(([config.real(np)(0)], w))
        w1 = np.hstack((w, [config.real(np)(0)]))
        beta = 1 - self.alpha / 2
        w = beta * w0 + (1 - beta) * w1
        if x is None:
            return w
        x = np.vstack(([2 * x[0] - x[1]], x))
        if not self.geom.inside(x[0]):
            return x[1:], w[1:]
        return x, w

    def modify_third_order(self, x=None, w=None):
        w0 = np.hstack(([config.real(np)(0)], w))
        w1 = np.hstack((w, [config.real(np)(0)]))
        w2 = np.hstack(([config.real(np)(0)] * 2, w[:-1]))
        beta = 1 - self.alpha / 2
        w = (
            (-6 * beta ** 2 + 11 * beta + 1) / 6 * w0
            + (11 - 6 * beta) * (1 - beta) / 12 * w1
            + (6 * beta + 1) * (beta - 1) / 12 * w2
        )
        if x is None:
            return w
        x = np.vstack(([2 * x[0] - x[1]], x))
        if not self.geom.in_domain(x[0]):
            return x[1:], w[1:]
        return x, w

    def get_weight(self, n):
        return self._w_init[: n + 1]

    def get_matrix_static(self):
        if not array_ops.istensor(self.alpha):
            int_mat = np.zeros(
                (self.disc.resolution[0], self.disc.resolution[0]),
                dtype=config.real(np),
            )
            h = self.geom.diam / (self.disc.resolution[0] - 1)
            for i in range(1, self.disc.resolution[0] - 1):
                # first order
                int_mat[i, 1 : i + 2] = np.flipud(self.get_weight(i))
                int_mat[i, i - 1 : -1] += self.get_weight(
                    self.disc.resolution[0] - 1 - i
                )
                # second order
                # int_mat[i, 0:i+2] = np.flipud(self.modify_second_order(w=self.get_weight(i)))
                # int_mat[i, i-1:] += self.modify_second_order(w=self.get_weight(self.disc.resolution[0]-1-i))
                # third order
                # int_mat[i, 0:i+2] = np.flipud(self.modify_third_order(w=self.get_weight(i)))
                # int_mat[i, i-1:] += self.modify_third_order(w=self.get_weight(self.disc.resolution[0]-1-i))
            return h ** (-self.alpha) * int_mat
        int_mat = tf.zeros((1, self.disc.resolution[0]), dtype=config.real(tf))
        for i in range(1, self.disc.resolution[0] - 1):
            if True:
                # shifted
                row = tf.concat(
                    [
                        tf.zeros(1, dtype=config.real(tf)),
                        tf.reverse(self.get_weight(i), [0]),
                        tf.zeros(
                            self.disc.resolution[0] - i - 2, dtype=config.real(tf)
                        ),
                    ],
                    0,
                )
                row += tf.concat(
                    [
                        tf.zeros(i - 1, dtype=config.real(tf)),
                        self.get_weight(self.disc.resolution[0] - 1 - i),
                        tf.zeros(1, dtype=config.real(tf)),
                    ],
                    0,
                )
            else:
                # not shifted
                row = tf.concat(
                    [
                        tf.reverse(self.get_weight(i), [0]),
                        tf.zeros(self.disc.resolution[0] - i - 1),
                    ],
                    0,
                )
                row += tf.concat(
                    [tf.zeros(i), self.get_weight(self.disc.resolution[0] - 1 - i)], 0
                )
            row = tf.expand_dims(row, 0)
            int_mat = tf.concat([int_mat, row], 0)
        int_mat = tf.concat(
            [int_mat, tf.zeros([1, self.disc.resolution[0]], dtype=config.real(tf))], 0
        )
        h = self.geom.diam / (self.disc.resolution[0] - 1)
        return h ** (-self.alpha) * int_mat

    def get_matrix_dynamic(self, sparse):
        if self.x is None:
            raise ValueError("Get dynamic points first.")

        if sparse:
            print("Generating sparse fractional matrix...")
            dense_shape = (self.x0.shape[0], self.x.shape[0])
            indices, values = [], []
            beg = self.x0.shape[0]
            for i in range(self.x0.shape[0]):
                for _ in range(array_ops.shape(self.w[i])[0]):
                    indices.append([i, beg])
                    beg += 1
                values = array_ops.hstack((values, self.w[i]))
            return indices, values, dense_shape

        print("Generating dense fractional matrix...")
        int_mat = np.zeros((self.x0.shape[0], self.x.shape[0]), dtype=config.real(np))
        beg = self.x0.shape[0]
        for i in range(self.x0.shape[0]):
            int_mat[i, beg : beg + self.w[i].size] = self.w[i]
            beg += self.w[i].size
        return int_mat
