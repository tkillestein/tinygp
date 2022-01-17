# -*- coding: utf-8 -*-

from __future__ import annotations

__all__ = [
    "Kernel",
    "Custom",
    "Transform",
    "Subspace",
    "Sum",
    "Product",
    "Constant",
    "DotProduct",
    "Polynomial",
    "Exp",
    "ExpSquared",
    "Matern32",
    "Matern52",
    "Cosine",
    "ExpSineSquared",
    "RationalQuadratic",
]

from typing import Callable, Optional, Sequence, Union

import jax
import jax.numpy as jnp

from .metrics import Metric, diagonal_metric, unit_metric
from .types import JAXArray

Axis = Union[int, Sequence[int], JAXArray]


class Kernel:
    """The base class for all kernel implementations

    This subclass provides default implementations to add and multiply kernels.
    Subclasses should accept parameters in their ``__init__`` and then override
    :func:`Kernel.evaluate` with custom behavior.
    """

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        """Evaluate the kernel at a pair of input coordinates

        This should be overridden be subclasses to return the kernel-specific
        value. Two things to note:

        1. Users shouldn't generally call :func:`Kernel.evaluate`. Instead,
           always "call" the kernel instance directly; for example, you can
           evaluate the Matern-3/2 kernel using ``Matern32(1.5)(x1, x2)``, for
           arrays of input coordinates ``x1`` and ``x2``.
        2. When implementing a custom kernel, this method should treat ``X1``
           and ``X2`` as single datapoints. In other words, these inputs will
           typically either be scalars of have shape ``n_dim``, where ``n_dim``
           is the number of input dimensions, rather than ``n_data`` or
           ``(n_data, n_dim)``, and you should let the :class:`Kernel` ``vmap``
           magic handle all the broadcasting for you.
        """
        raise NotImplementedError()

    def __call__(
        self, X1: JAXArray, X2: Optional[JAXArray] = None
    ) -> JAXArray:
        if X2 is None:
            return jax.vmap(self.evaluate, in_axes=(0, 0))(X1, X1)
        return jax.vmap(
            jax.vmap(self.evaluate, in_axes=(None, 0)), in_axes=(0, None)
        )(X1, X2)

    def __add__(self, other: Union["Kernel", JAXArray]) -> "Kernel":
        if isinstance(other, Kernel):
            return Sum(self, other)
        return Sum(self, Constant(other))

    def __radd__(self, other: Union["Kernel", JAXArray]) -> "Kernel":
        if isinstance(other, Kernel):
            return Sum(other, self)
        return Sum(Constant(other), self)

    def __mul__(self, other: Union["Kernel", JAXArray]) -> "Kernel":
        if isinstance(other, Kernel):
            return Product(self, other)
        return Product(self, Constant(other))

    def __rmul__(self, other: Union["Kernel", JAXArray]) -> "Kernel":
        if isinstance(other, Kernel):
            return Product(other, self)
        return Product(Constant(other), self)


class Custom(Kernel):
    """A custom kernel class implemented as a callable

    Args:
        function: A callable with a signature and behavior that matches
            :func:`Kernel.evaluate`.
    """

    def __init__(self, function: Callable[[JAXArray, JAXArray], JAXArray]):
        self.function = function

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return self.function(X1, X2)


class Transform(Kernel):
    """Apply a transformation to the input coordinates of the kernel

    By default, the second parameter will be parsed as a Euclidean ``Metric``,
    for example, the following shows two equivalent ways of adding a length
    scale to a :class:`Matern32` kernel:

    .. code-block:: python

        >>> import numpy as np
        >>> from tinygp import kernels
        >>> kernel1 = kernels.Transform(kernels.Matern32(), 4.5)
        >>> kernel2 = kernels.Matern32(4.5)
        >>> np.testing.assert_allclose(
        ...     kernel1.evaluate(0.5, 0.1), kernel2.evaluate(0.5, 0.1)
        ... )

    The former allows for more flexible transforms, since the second parameter
    can be any metric as described below in the :ref:`Metrics` section.

    Args:
        kernel (Kernel): The fundamental kernel.
        metric: (Metric): A callable object that accepts coordinates as inputs
            and returns transformed coordinates.
    """

    def __init__(
        self,
        kernel: Kernel,
        metric: Optional[Union[Metric, JAXArray]] = None,
    ):
        self.kernel = kernel
        if metric is None:
            self.metric = unit_metric
        elif callable(metric):
            self.metric = metric  # type: ignore
        else:
            self.metric = diagonal_metric(metric)  # type: ignore

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return self.kernel.evaluate(self.metric(X1), self.metric(X2))


class Subspace(Kernel):
    """A kernel transform that selects a subset of the input dimensions

    For example, the following kernel only depends on the coordinates in the
    second dimension:

    .. code-block:: python

        >>> import numpy as np
        >>> from tinygp import kernels
        >>> kernel = kernels.Subspace(kernels.Matern32(), axis=1)
        >>> np.testing.assert_allclose(
        ...     kernel.evaluate(np.array([0.5, 0.1]), np.array([-0.4, 0.7])),
        ...     kernel.evaluate(np.array([100.5, 0.1]), np.array([-70.4, 0.7])),
        ... )

    Args:
        kernel (Kernel): The fundamental kernel.
        axis: (Axis, optional): An integer or tuple of integers specifying the
            axes to select.
    """

    def __init__(self, kernel: Kernel, axis: Optional[Axis] = None):
        self.kernel = kernel
        self.axis = axis

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        if self.axis is None:
            return self.kernel.evaluate(X1, X2)
        return self.kernel.evaluate(X1[self.axis], X2[self.axis])


class Sum(Kernel):
    """A helper to represent the sum of two kernels"""

    def __init__(self, kernel1: Kernel, kernel2: Kernel):
        self.kernel1 = kernel1
        self.kernel2 = kernel2

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return self.kernel1.evaluate(X1, X2) + self.kernel2.evaluate(X1, X2)


class Product(Kernel):
    """A helper to represent the product of two kernels"""

    def __init__(self, kernel1: Kernel, kernel2: Kernel):
        self.kernel1 = kernel1
        self.kernel2 = kernel2

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return self.kernel1.evaluate(X1, X2) * self.kernel2.evaluate(X1, X2)


class Constant(Kernel):
    """This kernel returns the constant

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = c

    where :math:`c` is a parameter.

    Args:
        c: The parameter :math:`c` in the above equation.
    """

    def __init__(self, value: JAXArray):
        self.value = jnp.asarray(value)

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return self.value


class DotProduct(Kernel):
    """The dot product kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = \mathbf{x}_i \cdot \mathbf{x}_j

    with no parameters.
    """

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return X1 @ X2


class Polynomial(Kernel):
    """A polynomial kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = [(\mathbf{x}_i / \ell) \cdot
            (\mathbf{x}_j / \ell) + \sigma^2]^P

    Args:
        order: The power :math:`P`.
        scale: The parameter :math:`\ell`.
        sigma: The parameter :math:`\sigma`.
    """

    def __init__(
        self,
        *,
        order: JAXArray,
        scale: JAXArray = jnp.ones(()),
        sigma: JAXArray = jnp.zeros(()),
    ):
        self.order = order
        self.scale = scale
        self.sigma2 = jnp.square(sigma)

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return (
            (X1 / self.scale) @ (X2 / self.scale) + self.sigma2
        ) ** self.order


class Exp(Kernel):
    """The exponential kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = \exp(-r)

    where

    .. math::

        r = ||(\mathbf{x}_i - \mathbf{x}_j) / \ell||_1

    Args:
        scale: The parameter :math:`\ell`.
    """

    def __init__(self, scale: JAXArray = jnp.ones(())):
        self.scale = scale

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return jnp.exp(-jnp.sum(jnp.abs((X1 - X2) / self.scale)))


class ExpSquared(Kernel):
    """The exponential squared or radial basis function kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = \exp(-r^2 / 2)

    where

    .. math::

        r = ||(\mathbf{x}_i - \mathbf{x}_j) / \ell||_2

    Args:
        scale: The parameter :math:`\ell`.
    """

    def __init__(self, scale: JAXArray = jnp.ones(())):
        self.scale = scale

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        return jnp.exp(-0.5 * jnp.sum(jnp.square((X1 - X2) / self.scale)))


class Matern32(Kernel):
    """The Matern-3/2 kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = (1 + \sqrt{3}\,r)\,\exp(-\sqrt{3}\,r)

    where

    .. math::

        r = ||(\mathbf{x}_i - \mathbf{x}_j) / \ell||_1

    Args:
        scale: The parameter :math:`\ell`.
    """

    def __init__(self, scale: JAXArray = jnp.ones(())):
        self.scale = scale

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        r = jnp.sum(jnp.abs((X1 - X2) / self.scale))
        arg = jnp.sqrt(3.0) * r
        return (1.0 + arg) * jnp.exp(-arg)


class Matern52(Kernel):
    """The Matern-5/2 kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = (1 + \sqrt{5}\,r +
            5\,r^2/\sqrt{3})\,\exp(-\sqrt{5}\,r)

    where

    .. math::

        r = ||(\mathbf{x}_i - \mathbf{x}_j) / \ell||_1

    Args:
        scale: The parameter :math:`\ell`.
    """

    def __init__(self, scale: JAXArray = jnp.ones(())):
        self.scale = scale

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        r = jnp.sum(jnp.abs((X1 - X2) / self.scale))
        arg = jnp.sqrt(5.0) * r
        return (1.0 + arg + jnp.square(arg) / 3.0) * jnp.exp(-arg)


class Cosine(Kernel):
    """The cosine kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = \cos(2\,\pi\,r)

    where

    .. math::

        r = ||(\mathbf{x}_i - \mathbf{x}_j) / P||_1

    Args:
        period: The parameter :math:`P`.
    """

    def __init__(self, period: JAXArray):
        self.period = period

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        r = jnp.sum(jnp.abs((X1 - X2) / self.period))
        return jnp.cos(2 * jnp.pi * r)


class ExpSineSquared(Kernel):
    """The exponential sine squared or quasiperiodic kernel

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = \exp(-\Gamma\,\sin^2 \pi r)

    where

    .. math::

        r = ||(\mathbf{x}_i - \mathbf{x}_j) / P||_1

    Args:
        period: The parameter :math:`P`.
        gamma: The parameter :math:`\Gamma`.
    """

    def __init__(self, period: JAXArray, gamma: JAXArray):
        self.period = period
        self.gamma = gamma

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        r = jnp.sum(jnp.abs((X1 - X2) / self.period))
        return jnp.exp(-self.gamma * jnp.square(jnp.sin(jnp.pi * r)))


class RationalQuadratic(Kernel):
    r"""The rational quadratic

    .. math::

        k(\mathbf{x}_i,\,\mathbf{x}_j) = (1 + r^2 / 2\,\alpha)^{-\alpha}

    where

    .. math::

        r = ||\mathbf{x}_i - \mathbf{x}_j||_2

    Args:
        alpha: The parameter :math:`\alpha`.
    """

    def __init__(self, alpha: JAXArray):
        self.alpha = alpha

    def evaluate(self, X1: JAXArray, X2: JAXArray) -> JAXArray:
        r2 = jnp.sum(jnp.square(X1 - X2))
        return (1.0 + 0.5 * r2 / self.alpha) ** -self.alpha
